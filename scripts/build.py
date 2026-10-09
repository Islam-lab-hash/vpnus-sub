"""Build the public VLESS subscription from the provider's full Xray data.

The provider is exposed through Happy Decoder in multiple forms. JSON is used
for transport/REALITY settings, while TXT is used only for human remarks. Names
are matched inside the same UUID+host+port endpoint queue, so a country label is
never copied to an unrelated server merely because its global position changed.
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
            text = base64.b64decode(
                compact + "=" * (-len(compact) % 4), altchars=b"-_", validate=True
            ).decode("utf-8-sig").strip()
        except (binascii.Error, UnicodeError, ValueError):
            break
    return []


def split_uri(uri: str):
    p = urllib.parse.urlsplit(uri)
    return p, dict(urllib.parse.parse_qsl(p.query, keep_blank_values=True))


def endpoint_key(uri: str):
    p, _ = split_uri(uri)
    return (urllib.parse.unquote(p.username or ""), (p.hostname or "").lower(), p.port)


def canonical_key(uri: str):
    try:
        p, q = split_uri(uri)
        return (
            p.scheme.lower(), urllib.parse.unquote(p.username or ""),
            (p.hostname or "").lower(), p.port, tuple(sorted(q.items())),
        )
    except Exception:
        return ("raw", uri.split("#", 1)[0])


def dedupe(links):
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


def attach_txt_names(structured: list[str], human: list[str]):
    """Match names only within the same endpoint, preserving repeated-endpoint order."""
    queues = defaultdict(deque)
    for uri in human:
        queues[endpoint_key(uri)].append(display_name(uri))
    out, matched = [], 0
    for uri in structured:
        queue = queues.get(endpoint_key(uri))
        if queue:
            out.append(replace_name(uri, queue.popleft()))
            matched += 1
        else:
            out.append(uri)
    return out, matched


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


def repair_exact(uri: str, known_by_endpoint: dict) -> str:
    known = known_by_endpoint.get(endpoint_key(uri))
    if not known:
        return uri
    p, q = split_uri(uri)
    _, kq = split_uri(known)
    changed = False
    for key in (
        "pbk", "sni", "sid", "fp", "flow", "security", "type", "encryption",
        "serviceName", "authority", "path", "host", "mode", "spx",
    ):
        if not q.get(key) and kq.get(key):
            q[key] = kq[key]
            changed = True
    if not changed:
        return uri
    return urllib.parse.urlunsplit((
        p.scheme, p.netloc, p.path,
        urllib.parse.urlencode(q, safe="/:,@"), p.fragment,
    ))


def repair_filter(links, fallback):
    known = {endpoint_key(x): x for x in fallback}
    usable, rejected, repaired = [], 0, 0
    for uri in links:
        fixed = repair_exact(uri, known)
        repaired += int(fixed != uri)
        if is_usable_vless(fixed):
            usable.append(fixed)
        else:
            rejected += 1
    return dedupe(usable), rejected, repaired


def http_get(url: str, ua="Mozilla/5.0") -> str:
    last_error = None
    for attempt in range(1, HTTP_ATTEMPTS + 1):
        req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "*/*"})
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as response:
                body = response.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise ValueError("response too large")
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


def qset(q, key, value):
    if value is not None and value != "":
        q[key] = str(value)


def outbound_to_vless(outbound):
    if str(outbound.get("protocol", "")).lower() != "vless":
        return []
    settings = outbound.get("settings") or {}
    stream = outbound.get("streamSettings") or {}
    name = str(outbound.get("remarks") or outbound.get("tag") or "VLESS").strip()
    result = []
    for node in settings.get("vnext") or []:
        address, port = str(node.get("address") or "").strip(), node.get("port")
        if not address or not port:
            continue
        for user in node.get("users") or []:
            uid = str(user.get("id") or "").strip()
            if not uid:
                continue
            q = {}
            qset(q, "encryption", user.get("encryption") or "none")
            qset(q, "flow", user.get("flow"))
            network = str(stream.get("network") or "tcp").lower()
            security = str(stream.get("security") or "none").lower()
            qset(q, "type", network)
            if security != "none":
                qset(q, "security", security)
            if security == "reality":
                r = stream.get("realitySettings") or {}
                qset(q, "pbk", r.get("publicKey") or r.get("password"))
                qset(q, "sni", r.get("serverName"))
                qset(q, "sid", r.get("shortId"))
                qset(q, "fp", r.get("fingerprint"))
                qset(q, "spx", r.get("spiderX"))
            elif security == "tls":
                t = stream.get("tlsSettings") or {}
                qset(q, "sni", t.get("serverName"))
                qset(q, "fp", t.get("fingerprint"))
            if network == "grpc":
                g = stream.get("grpcSettings") or {}
                qset(q, "serviceName", g.get("serviceName"))
                qset(q, "authority", g.get("authority"))
                if g.get("multiMode"):
                    qset(q, "mode", "multi")
            elif network in ("xhttp", "splithttp"):
                x = stream.get("xhttpSettings") or stream.get("splithttpSettings") or {}
                qset(q, "path", x.get("path"))
                qset(q, "host", x.get("host"))
                qset(q, "mode", x.get("mode"))
            elif network == "ws":
                w = stream.get("wsSettings") or {}
                qset(q, "path", w.get("path"))
                headers = w.get("headers") or {}
                qset(q, "host", headers.get("Host") or headers.get("host"))
            netloc = f"{urllib.parse.quote(uid, safe='')}@{address}:{int(port)}"
            result.append(urllib.parse.urlunsplit((
                "vless", netloc, "", urllib.parse.urlencode(q, safe="/:,@"),
                urllib.parse.quote(name, safe=""),
            )))
    return result


def decode_xray_json(text):
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
            result.extend(outbound_to_vless(outbound))
    return result


def fetch_views(source_url):
    base = "https://happy-decoder.cc/p/"
    specs = [
        ("happy-json", base + "ua=happ,hwid=off,json/" + source_url, "json", "Mozilla/5.0"),
        ("happy-txt", base + "ua=happ,hwid=off,txt,name=remarks+idx/" + source_url, "text", "Mozilla/5.0"),
        ("happy-as-is", base + "ua=happ,hwid=off/" + source_url, "auto", "Mozilla/5.0"),
        ("direct", source_url, "text", "Happ/4.3.0"),
    ]
    results, errors = {}, []
    for name, url, mode, ua in specs:
        try:
            text = http_get(url, ua)
            links = decode_xray_json(text) if mode == "json" else decode_subscription(text)
            if mode == "auto" and not links:
                links = decode_xray_json(text)
            links = dedupe(links)
            if not links:
                raise ValueError("no VLESS profiles")
            results[name] = links
        except urllib.error.HTTPError as exc:
            errors.append(f"{name}:HTTP_{exc.code}")
        except urllib.error.URLError as exc:
            errors.append(f"{name}:URL_{type(exc.reason).__name__}")
        except Exception as exc:
            errors.append(f"{name}:{type(exc).__name__}")
    return results, errors


def existing_links():
    try:
        return decode_subscription(OUTPUT.read_text(encoding="ascii")) if OUTPUT.exists() else []
    except Exception:
        return []


def write_output(links):
    payload = ("\n".join(links) + "\n").encode("utf-8")
    OUTPUT.write_text(base64.b64encode(payload).decode("ascii") + "\n", encoding="ascii")


def main():
    fallback = [x for x in dedupe(decode_subscription(FALLBACK.read_text(encoding="utf-8"))) if is_usable_vless(x)]
    if not fallback:
        raise SystemExit("sources.txt has no usable fallback configs")

    source_url = os.environ.get("SOURCE_SUB_URL", "").strip()
    views, errors = fetch_views(source_url) if source_url else ({}, ["source:missing-secret"])

    names_matched = 0
    if views.get("happy-json") and views.get("happy-txt"):
        views["happy-json"], names_matched = attach_txt_names(views["happy-json"], views["happy-txt"])

    stats, candidates = [], []
    for adapter, links in views.items():
        usable, rejected, repaired = repair_filter(links, fallback)
        stats.append({"adapter": adapter, "raw": len(links), "usable": len(usable), "rejected": rejected, "repaired": repaired})
        candidates.append((adapter, usable, len(links), rejected, repaired))

    json_candidate = next((x for x in candidates if x[0] == "happy-json"), None)
    if json_candidate and len(json_candidate[1]) == json_candidate[2] and json_candidate[1]:
        primary = json_candidate
    elif candidates:
        primary = max(candidates, key=lambda x: (len(x[1]), -x[3], x[2]))
    else:
        primary = ("none", [], 0, 0, 0)

    adapter, usable, raw_count, rejected, repaired = primary
    previous = existing_links()
    if usable:
        final = usable
    elif previous and all(is_usable_vless(x) for x in previous):
        final = previous
    else:
        final = fallback
    write_output(final)

    status = {
        "remote_ok": bool(usable),
        "adapter": adapter,
        "remote_count": raw_count,
        "remote_usable_count": len(usable),
        "remote_rejected_count": rejected,
        "remote_repaired_count": repaired,
        "name_source": "endpoint-txt" if names_matched else "none",
        "names_preserved": names_matched,
        "pre_filter_count": len(final),
        "published_count": len(final),
        "fallback_count": len(fallback),
        "adapter_stats": stats,
        "errors": errors,
        "checked_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"REMOTE_OK={str(bool(usable)).lower()} PRIMARY={adapter} REMOTE={raw_count} "
        f"USABLE={len(usable)} NAMES_MATCHED={names_matched}"
    )
    for stat in stats:
        print(f"ADAPTER {stat['adapter']}: raw={stat['raw']} usable={stat['usable']} rejected={stat['rejected']} repaired={stat['repaired']}")
    if errors:
        print("ADAPTER_ERRORS=" + ",".join(errors))


if __name__ == "__main__":
    main()
