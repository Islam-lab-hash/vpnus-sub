"""Synchronize the provider subscription without flattening its structure.

Happy Decoder exposes several representations of the same source. JSON keeps
the full Xray transport/REALITY settings; TXT keeps the provider's human
remarks. We combine those two views, but publish one authoritative technical
representation instead of merging equivalent JSON/TXT/as-is copies.
"""
import base64
import binascii
import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FALLBACK = ROOT / "sources.txt"
OUTPUT = ROOT / "sub.txt"
STATUS = ROOT / "sync-status.json"
MAX_BYTES = 3_000_000
HTTP_TIMEOUT = 25
HTTP_ATTEMPTS = 3
RETRYABLE_HTTP = {408, 425, 429, 500, 502, 503, 504}
CRITICAL_REALITY = ("pbk", "sni", "sid", "fp")


def decode_subscription(text: str) -> list[str]:
    text = text.lstrip("\ufeff").strip()
    if not text:
        return []
    for _ in range(3):
        links = [x.strip() for x in text.splitlines() if x.strip().startswith("vless://")]
        if links:
            return links
        try:
            compact = "".join(text.split())
            raw = base64.b64decode(
                compact + "=" * (-len(compact) % 4), altchars=b"-_", validate=True
            )
            text = raw.decode("utf-8-sig").strip()
        except (binascii.Error, UnicodeError, ValueError):
            break
    return []


def split_uri(uri: str):
    p = urllib.parse.urlsplit(uri)
    q = dict(urllib.parse.parse_qsl(p.query, keep_blank_values=True))
    return p, q


def endpoint_key(uri: str):
    p, _ = split_uri(uri)
    return (urllib.parse.unquote(p.username or ""), (p.hostname or "").lower(), p.port)


def canonical_key(uri: str):
    try:
        p, q = split_uri(uri)
        if p.scheme.lower() != "vless" or not p.hostname:
            return ("raw", uri.split("#", 1)[0])
        return (
            p.scheme.lower(), urllib.parse.unquote(p.username or ""),
            (p.hostname or "").lower(), p.port, tuple(sorted(q.items())),
        )
    except Exception:
        return ("raw", uri.split("#", 1)[0])


def dedupe(links: list[str]) -> list[str]:
    out, seen = [], set()
    for link in links:
        key = canonical_key(link)
        if key not in seen:
            seen.add(key)
            out.append(link)
    return out


def display_name(uri: str) -> str:
    return urllib.parse.unquote(urllib.parse.urlsplit(uri).fragment or "").replace("+", " ").strip()


def replace_name(uri: str, name: str) -> str:
    return uri.split("#", 1)[0] + "#" + urllib.parse.quote(name or "VLESS", safe="")


def preserve_human_names(structured: list[str], human: list[str]):
    if not structured or not human:
        return structured, 0, "none"
    if len(structured) == len(human):
        matches = sum(endpoint_key(a) == endpoint_key(b) for a, b in zip(structured, human))
        if matches >= max(1, int(len(structured) * 0.60)):
            return (
                [replace_name(a, display_name(b)) for a, b in zip(structured, human)],
                len(structured),
                "ordered-txt",
            )
    queues = defaultdict(deque)
    for uri in human:
        queues[endpoint_key(uri)].append(display_name(uri))
    enriched, changed = [], 0
    for uri in structured:
        names = queues.get(endpoint_key(uri))
        if names:
            enriched.append(replace_name(uri, names.popleft()))
            changed += 1
        else:
            enriched.append(uri)
    return enriched, changed, "endpoint-txt"


def is_usable_vless(uri: str) -> bool:
    try:
        p, q = split_uri(uri)
        if p.scheme.lower() != "vless" or not p.username or not p.hostname or p.port is None:
            return False
        if not 1 <= p.port <= 65535:
            return False
        if q.get("security", "").lower() == "reality":
            if any(not q.get(k) for k in CRITICAL_REALITY):
                return False
            if q.get("type", "tcp").lower() == "tcp" and not q.get("flow"):
                return False
        return True
    except Exception:
        return False


def merge_missing_from_known_good(remote_uri: str, fallback_by_endpoint: dict) -> str:
    known = fallback_by_endpoint.get(endpoint_key(remote_uri))
    if not known:
        return remote_uri
    rp, rq = split_uri(remote_uri)
    _, kq = split_uri(known)
    changed = False
    for key in (
        "pbk", "sni", "sid", "fp", "flow", "security", "type", "encryption",
        "serviceName", "authority", "path", "host", "mode", "spx",
    ):
        if not rq.get(key) and kq.get(key):
            rq[key] = kq[key]
            changed = True
    if not changed:
        return remote_uri
    query = urllib.parse.urlencode(rq, doseq=False, safe="/:,@")
    return urllib.parse.urlunsplit((rp.scheme, rp.netloc, rp.path, query, rp.fragment))


def repair_and_filter_remote(remote: list[str], fallback: list[str]):
    fallback_by_endpoint = {endpoint_key(x): x for x in fallback}
    usable, rejected, repaired = [], 0, 0
    for uri in remote:
        fixed = merge_missing_from_known_good(uri, fallback_by_endpoint)
        repaired += int(fixed != uri)
        if is_usable_vless(fixed):
            usable.append(fixed)
        else:
            rejected += 1
    return dedupe(usable), rejected, repaired


def http_get(url: str, user_agent: str = "Mozilla/5.0") -> str:
    last_error = None
    for attempt in range(1, HTTP_ATTEMPTS + 1):
        req = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": "*/*"})
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as response:
                body = response.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise ValueError("response exceeds 3 MB")
                return body.decode("utf-8-sig")
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in RETRYABLE_HTTP or attempt == HTTP_ATTEMPTS:
                raise
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            last_error = exc
            if attempt == HTTP_ATTEMPTS:
                raise
        time.sleep(attempt * 2)
    raise RuntimeError("request failed") from last_error


def qset(query: dict, key: str, value):
    if value is not None and value != "":
        query[key] = str(value)


def json_outbound_to_vless(outbound: dict) -> list[str]:
    if str(outbound.get("protocol", "")).lower() != "vless":
        return []
    settings = outbound.get("settings") or {}
    stream = outbound.get("streamSettings") or {}
    vnext = settings.get("vnext") or []
    result = []
    base_name = str(outbound.get("remarks") or outbound.get("tag") or "VLESS").strip()
    for node_index, node in enumerate(vnext, start=1):
        address = str(node.get("address") or "").strip()
        port = node.get("port")
        users = node.get("users") or []
        if not address or not port:
            continue
        for user_index, user in enumerate(users, start=1):
            uid = str(user.get("id") or "").strip()
            if not uid:
                continue
            query = {}
            qset(query, "encryption", user.get("encryption") or "none")
            qset(query, "flow", user.get("flow"))
            network = str(stream.get("network") or "tcp").lower()
            security = str(stream.get("security") or "none").lower()
            qset(query, "type", network)
            if security and security != "none":
                qset(query, "security", security)
            if security == "reality":
                rs = stream.get("realitySettings") or {}
                qset(query, "pbk", rs.get("publicKey") or rs.get("password"))
                qset(query, "sni", rs.get("serverName"))
                qset(query, "sid", rs.get("shortId"))
                qset(query, "fp", rs.get("fingerprint"))
                qset(query, "spx", rs.get("spiderX"))
            elif security == "tls":
                ts = stream.get("tlsSettings") or {}
                qset(query, "sni", ts.get("serverName"))
                qset(query, "fp", ts.get("fingerprint"))
            if network == "grpc":
                gs = stream.get("grpcSettings") or {}
                qset(query, "serviceName", gs.get("serviceName"))
                qset(query, "authority", gs.get("authority"))
                if gs.get("multiMode"):
                    qset(query, "mode", "multi")
            elif network in ("xhttp", "splithttp"):
                xs = stream.get("xhttpSettings") or stream.get("splithttpSettings") or {}
                qset(query, "path", xs.get("path"))
                qset(query, "host", xs.get("host"))
                qset(query, "mode", xs.get("mode"))
            elif network == "ws":
                ws = stream.get("wsSettings") or {}
                qset(query, "path", ws.get("path"))
                headers = ws.get("headers") or {}
                qset(query, "host", headers.get("Host") or headers.get("host"))
            name = base_name
            if len(vnext) > 1 or len(users) > 1:
                name += f" - {node_index}.{user_index}"
            netloc = f"{urllib.parse.quote(uid, safe='')}@{address}:{int(port)}"
            result.append(urllib.parse.urlunsplit((
                "vless", netloc, "", urllib.parse.urlencode(query, safe="/:,@"),
                urllib.parse.quote(name, safe=""),
            )))
    return result


def decode_xray_json(text: str) -> list[str]:
    try:
        data = json.loads(text)
    except Exception:
        return []
    objects = data if isinstance(data, list) else [data]
    outbounds = []
    for obj in objects:
        if isinstance(obj, dict):
            if isinstance(obj.get("outbounds"), list):
                outbounds.extend(obj["outbounds"])
            elif obj.get("protocol"):
                outbounds.append(obj)
    result = []
    for outbound in outbounds:
        if isinstance(outbound, dict):
            result.extend(json_outbound_to_vless(outbound))
    return result


def proxy_requests(source_url: str):
    base = "https://happy-decoder.cc/p/"
    yield "happy-json", base + "ua=happ,hwid=off,json/" + source_url, "json"
    yield "happy-txt", base + "ua=happ,hwid=off,txt,name=remarks+idx/" + source_url, "text"
    yield "happy-base64", base + "ua=happ,hwid=off,base64,name=remarks+idx/" + source_url, "text"
    yield "happy-as-is", base + "ua=happ,hwid=off/" + source_url, "auto"
    yield "direct", source_url, "text"


def fetch_all_remote(source_url: str):
    if not source_url.startswith("https://"):
        raise ValueError("SOURCE_SUB_URL must use HTTPS")
    results, errors = [], []
    for adapter, url, mode in proxy_requests(source_url):
        try:
            text = http_get(url, "Happ/4.3.0" if adapter == "direct" else "Mozilla/5.0")
            links = decode_xray_json(text) if mode == "json" else decode_subscription(text)
            if mode == "auto" and not links:
                links = decode_xray_json(text)
            links = dedupe(links)
            if not links:
                raise ValueError("response contains no VLESS profiles")
            results.append((adapter, links))
        except urllib.error.HTTPError as exc:
            errors.append(f"{adapter}:HTTP_{exc.code}")
        except urllib.error.URLError as exc:
            errors.append(f"{adapter}:URL_{type(exc.reason).__name__}")
        except Exception as exc:
            errors.append(f"{adapter}:{type(exc).__name__}")
    return results, errors


def existing_output_links() -> list[str]:
    if not OUTPUT.exists():
        return []
    try:
        return dedupe(decode_subscription(OUTPUT.read_text(encoding="ascii")))
    except Exception:
        return []


def write_output(links: list[str]):
    payload = ("\n".join(links) + "\n").encode("utf-8")
    encoded = base64.b64encode(payload).decode("ascii") + "\n"
    if not OUTPUT.exists() or OUTPUT.read_text(encoding="ascii") != encoded:
        OUTPUT.write_text(encoded, encoding="ascii")


def main():
    fallback = dedupe(decode_subscription(FALLBACK.read_text(encoding="utf-8")))
    fallback = [x for x in fallback if is_usable_vless(x)]
    if not fallback:
        raise SystemExit("sources.txt has no usable fallback configs")

    source_url = os.environ.get("SOURCE_SUB_URL", "").strip()
    remote_results, errors = (fetch_all_remote(source_url) if source_url else ([], ["source:missing-secret"]))
    by_adapter = {adapter: links for adapter, links in remote_results}

    name_source = by_adapter.get("happy-txt") or by_adapter.get("happy-base64") or []
    name_mode, name_count = "none", 0
    if by_adapter.get("happy-json") and name_source:
        enriched, name_count, name_mode = preserve_human_names(by_adapter["happy-json"], name_source)
        by_adapter["happy-json"] = enriched

    adapter_stats = []
    candidates = []
    for adapter, raw_links in remote_results:
        raw_links = by_adapter.get(adapter, raw_links)
        usable, rejected, repaired = repair_and_filter_remote(raw_links, fallback)
        adapter_stats.append({
            "adapter": adapter, "raw": len(raw_links), "usable": len(usable),
            "rejected": rejected, "repaired": repaired,
        })
        candidates.append((adapter, usable, len(raw_links), rejected, repaired))

    # Prefer the full JSON structure whenever it is complete. Other formats are
    # diagnostics/fallbacks, not additional servers: merging them duplicates the
    # same logical outbounds with slightly different serialization.
    json_candidate = next((x for x in candidates if x[0] == "happy-json"), None)
    if json_candidate and json_candidate[1] and len(json_candidate[1]) == json_candidate[2]:
        primary = json_candidate
    elif candidates:
        primary = max(candidates, key=lambda x: (len(x[1]), -x[3], x[2]))
    else:
        primary = ("none", [], 0, 0, 0)

    adapter, remote_usable, remote_count, rejected, repaired = primary
    previous = existing_output_links()
    remote_ok = bool(remote_usable)

    if remote_ok:
        pre_filter = remote_usable
        write_output(pre_filter)
    elif previous and all(is_usable_vless(x) for x in previous):
        pre_filter = previous
    else:
        pre_filter = fallback
        write_output(pre_filter)

    status = {
        "remote_ok": remote_ok,
        "adapter": adapter,
        "remote_count": remote_count,
        "remote_usable_count": len(remote_usable),
        "remote_rejected_count": rejected,
        "remote_repaired_count": repaired,
        "name_source": name_mode,
        "names_preserved": name_count,
        "pre_filter_count": len(pre_filter),
        "published_count": len(pre_filter),
        "fallback_count": len(fallback),
        "adapter_stats": adapter_stats,
        "errors": errors,
        "checked_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        f"REMOTE_OK={str(remote_ok).lower()} PRIMARY={adapter} "
        f"REMOTE={remote_count} USABLE={len(remote_usable)} "
        f"NAMES={name_count}/{name_mode} PRE_FILTER={len(pre_filter)}"
    )
    for stat in adapter_stats:
        print(
            f"ADAPTER {stat['adapter']}: raw={stat['raw']} usable={stat['usable']} "
            f"rejected={stat['rejected']} repaired={stat['repaired']}"
        )
    if errors:
        print("ADAPTER_ERRORS=" + ",".join(errors))

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("### Subscription sync\n\n")
            f.write(f"Authoritative source: **{adapter}**  \n")
            f.write(f"Usable provider variants: **{len(remote_usable)}**  \n")
            f.write(f"Provider names restored: **{name_count}** ({name_mode})  \n")


if __name__ == "__main__":
    main()
