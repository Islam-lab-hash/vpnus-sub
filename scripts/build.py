"""Synchronize the subscription source into sub.txt.

The remote provider currently returns some VLESS/REALITY entries without
client-critical parameters (SNI/fingerprint/flow).  A syntactically importable
URI is not necessarily usable, so this builder repairs entries only when the
same endpoint exists in the known-good fallback and rejects incomplete REALITY
entries instead of publishing them.

The committed sub.txt remains the last-known-good public payload. Curation and
renaming are performed by filter_top.py.
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
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FALLBACK = ROOT / "sources.txt"
OUTPUT = ROOT / "sub.txt"
STATUS = ROOT / "sync-status.json"
MAX_BYTES = 2_000_000
HTTP_TIMEOUT = 25
HTTP_ATTEMPTS = 3
RETRYABLE_HTTP = {408, 425, 429, 500, 502, 503, 504}
CRITICAL_REALITY = ("pbk", "sni", "sid", "fp")


def decode_subscription(text: str) -> list[str]:
    """Accept plain VLESS or Base64/URL-safe-Base64 VLESS subscriptions."""
    text = text.lstrip("\ufeff").strip()
    if not text:
        return []

    for _ in range(3):
        links = [
            line.strip()
            for line in text.splitlines()
            if line.strip().startswith("vless://")
        ]
        if links:
            return links
        try:
            compact = "".join(text.split())
            raw = base64.b64decode(
                compact + "=" * (-len(compact) % 4),
                altchars=b"-_",
                validate=True,
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
    return (
        urllib.parse.unquote(p.username or ""),
        (p.hostname or "").lower(),
        p.port,
    )


def canonical_key(uri: str):
    """Technical identity of a VLESS config, ignoring only its display name."""
    try:
        p, q = split_uri(uri)
        if p.scheme.lower() != "vless" or not p.hostname:
            return ("raw", uri.split("#", 1)[0])
        return (
            p.scheme.lower(),
            urllib.parse.unquote(p.username or ""),
            (p.hostname or "").lower(),
            p.port,
            tuple(sorted(q.items())),
        )
    except Exception:
        return ("raw", uri.split("#", 1)[0])


def dedupe(links: list[str]) -> list[str]:
    result: list[str] = []
    seen = set()
    for link in links:
        key = canonical_key(link)
        if key in seen:
            continue
        seen.add(key)
        result.append(link)
    return result


def is_usable_vless(uri: str) -> bool:
    """Reject imported-looking but non-functional REALITY profiles."""
    try:
        p, q = split_uri(uri)
        if p.scheme.lower() != "vless" or not p.username or not p.hostname or p.port is None:
            return False
        if not 1 <= p.port <= 65535:
            return False
        if q.get("security", "").lower() == "reality":
            if any(not q.get(k) for k in CRITICAL_REALITY):
                return False
            # This provider's TCP REALITY endpoints use Vision. Publishing the
            # stripped remote form without flow imports successfully but does not
            # establish a working tunnel in standard clients.
            if q.get("type", "tcp").lower() == "tcp" and not q.get("flow"):
                return False
        return True
    except Exception:
        return False


def merge_missing_from_known_good(remote_uri: str, fallback_by_endpoint: dict) -> str:
    """Repair only an exact UUID+host+port match; never guess another server's SNI."""
    known = fallback_by_endpoint.get(endpoint_key(remote_uri))
    if not known:
        return remote_uri

    rp, rq = split_uri(remote_uri)
    _, kq = split_uri(known)
    changed = False

    # Fill fields that the remote adapter can strip. Existing remote values win.
    for key in ("pbk", "sni", "sid", "fp", "flow", "security", "type", "encryption"):
        if not rq.get(key) and kq.get(key):
            rq[key] = kq[key]
            changed = True

    if not changed:
        return remote_uri

    query = urllib.parse.urlencode(rq, doseq=False, safe="/:,@")
    rebuilt = urllib.parse.urlunsplit((rp.scheme, rp.netloc, rp.path, query, rp.fragment))
    return rebuilt


def repair_and_filter_remote(remote: list[str], fallback: list[str]):
    fallback_by_endpoint = {endpoint_key(x): x for x in fallback}
    repaired: list[str] = []
    rejected: list[str] = []
    repair_count = 0

    for uri in remote:
        fixed = merge_missing_from_known_good(uri, fallback_by_endpoint)
        if fixed != uri:
            repair_count += 1
        if is_usable_vless(fixed):
            repaired.append(fixed)
        else:
            rejected.append(uri)

    return dedupe(repaired), len(rejected), repair_count


def http_get(url: str, user_agent: str = "Mozilla/5.0") -> str:
    """GET text with bounded size and retries for transient network failures."""
    last_error: Exception | None = None
    for attempt in range(1, HTTP_ATTEMPTS + 1):
        req = urllib.request.Request(
            url,
            headers={"User-Agent": user_agent, "Accept": "text/plain, */*"},
        )
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as response:
                body = response.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise ValueError("response exceeds 2 MB")
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


def proxy_urls(source_url: str):
    # Happy Decoder documents /p/ as a subscription proxy. HWID remains disabled.
    yield (
        "happy-base64",
        "https://happy-decoder.cc/p/ua=happ,hwid=off,base64/" + source_url,
        "Mozilla/5.0",
    )

    # Plain-text conversion is a second documented output mode and occasionally
    # preserves provider output differently from base64 conversion.
    yield (
        "happy-txt",
        "https://happy-decoder.cc/p/ua=happ,hwid=off,txt/" + source_url,
        "Mozilla/5.0",
    )

    # Compatibility query form, response as-is.
    query = urllib.parse.urlencode(
        {
            "u": source_url,
            "ua": "Happ/4.3.0",
            "hwid": "0",
            "os": "ios",
            "ver": "18.3",
            "model": "iPhone16,2",
        }
    )
    yield ("happy-query", "https://happy-decoder.cc/p/?" + query, "Mozilla/5.0")

    # Final fallback: source directly.
    yield ("direct", source_url, "Happ/4.3.0")


def fetch_remote(source_url: str):
    if not source_url.startswith("https://"):
        raise ValueError("SOURCE_SUB_URL must use HTTPS")

    errors: list[str] = []
    for adapter, url, ua in proxy_urls(source_url):
        try:
            text = http_get(url, ua)
            links = dedupe(decode_subscription(text))
            if not links:
                raise ValueError("response contains no VLESS links")
            return links, adapter, errors
        except urllib.error.HTTPError as exc:
            errors.append(f"{adapter}:HTTP_{exc.code}")
        except urllib.error.URLError as exc:
            errors.append(f"{adapter}:URL_{type(exc.reason).__name__}")
        except Exception as exc:
            errors.append(f"{adapter}:{type(exc).__name__}")
    return [], "none", errors


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
        raise SystemExit("sources.txt has no usable VLESS fallback configs")

    source_url = os.environ.get("SOURCE_SUB_URL", "").strip()
    remote_raw: list[str] = []
    adapter = "none"
    errors: list[str] = []

    if source_url:
        remote_raw, adapter, errors = fetch_remote(source_url)
    else:
        errors.append("source:missing-secret")

    usable_remote, rejected_remote, repaired_remote = repair_and_filter_remote(remote_raw, fallback)
    previous = existing_output_links()
    remote_ok = bool(remote_raw)

    # Known-good local entries are always part of the candidate set. Remote entries
    # may extend/refresh it, but an incomplete remote profile can no longer replace a
    # working one.
    if remote_ok:
        pre_filter = dedupe(usable_remote + fallback)
        write_output(pre_filter)
    elif previous and all(is_usable_vless(x) for x in previous):
        pre_filter = previous
    else:
        pre_filter = fallback
        write_output(pre_filter)

    status = {
        "remote_ok": remote_ok,
        "adapter": adapter,
        "remote_count": len(remote_raw),
        "remote_usable_count": len(usable_remote),
        "remote_rejected_count": rejected_remote,
        "remote_repaired_count": repaired_remote,
        "pre_filter_count": len(pre_filter),
        "published_count": len(pre_filter),
        "fallback_count": len(fallback),
        "errors": errors,
        "checked_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    STATUS.write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        f"REMOTE_OK={str(remote_ok).lower()} ADAPTER={adapter} "
        f"REMOTE={len(remote_raw)} USABLE={len(usable_remote)} "
        f"REPAIRED={repaired_remote} REJECTED={rejected_remote} "
        f"PRE_FILTER={len(pre_filter)} FALLBACK={len(fallback)}"
    )
    if errors:
        print("ADAPTER_ERRORS=" + ",".join(errors))

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("### Subscription sync\n\n")
            f.write(f"Remote fetched: **{len(remote_raw)}**  \n")
            f.write(f"Remote usable: **{len(usable_remote)}**  \n")
            f.write(f"Remote repaired: **{repaired_remote}**  \n")
            f.write(f"Remote rejected as incomplete: **{rejected_remote}**  \n")
            f.write(f"Candidate pool: **{len(pre_filter)}**  \n")
            f.write(f"Adapter: **{adapter}**  \n")
            if errors:
                f.write("Adapters tried: `" + "`, `".join(errors) + "`\n")


if __name__ == "__main__":
    main()
