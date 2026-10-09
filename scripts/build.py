"""Synchronize a public Base64 VLESS subscription.

SOURCE_SUB_URL is kept in GitHub Actions secrets. The preferred transport is
Happy Decoder's documented /p/ proxy with HWID explicitly disabled. If every
remote adapter fails, the last published sub.txt is kept unchanged.
"""
import base64
import binascii
import json
import os
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


def decode_subscription(text: str) -> list[str]:
    """Accept plain VLESS or Base64 VLESS (standard / URL-safe)."""
    text = text.lstrip("\ufeff").strip()
    if not text:
        return []

    for _ in range(3):
        links = [line.strip() for line in text.splitlines() if line.strip().startswith("vless://")]
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


def canonical_key(uri: str):
    """Technical identity of a VLESS config, ignoring display name (#fragment)."""
    try:
        p = urllib.parse.urlsplit(uri)
        if p.scheme.lower() != "vless" or not p.hostname:
            return ("raw", uri.split("#", 1)[0])
        query = tuple(sorted(urllib.parse.parse_qsl(p.query, keep_blank_values=True)))
        return (
            p.scheme.lower(),
            urllib.parse.unquote(p.username or ""),
            (p.hostname or "").lower(),
            p.port,
            query,
        )
    except Exception:
        return ("raw", uri.split("#", 1)[0])


def dedupe(links: list[str]) -> list[str]:
    result = []
    seen = set()
    for link in links:
        key = canonical_key(link)
        if key in seen:
            continue
        seen.add(key)
        result.append(link)
    return result


def http_get(url: str, user_agent: str = "Mozilla/5.0") -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": user_agent, "Accept": "text/plain, */*"},
    )
    with urllib.request.urlopen(req, timeout=25) as response:
        body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError("response exceeds 2 MB")
        return body.decode("utf-8-sig")


def proxy_urls(source_url: str):
    # Documented path form: /p/params/subscription-url
    # HWID is deliberately disabled. Output is normalized to Base64 when possible.
    yield (
        "happy-path",
        "https://happy-decoder.cc/p/ua=happ,hwid=off,base64/" + source_url,
        "Mozilla/5.0",
    )

    # Documented query form; useful as a compatibility fallback.
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

    # Last fallback: direct request. This may legitimately fail or redirect-loop.
    yield ("direct", source_url, "Happ/4.3.0")


def fetch_remote(source_url: str):
    if not source_url.startswith("https://"):
        raise ValueError("SOURCE_SUB_URL must be HTTPS")

    errors = []
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
    if not fallback:
        raise SystemExit("sources.txt has no valid VLESS fallback configs")

    source_url = os.environ.get("SOURCE_SUB_URL", "").strip()
    remote = []
    adapter = "none"
    errors = []
    if source_url:
        remote, adapter, errors = fetch_remote(source_url)

    previous = existing_output_links()
    remote_ok = bool(remote)

    if remote_ok:
        # Remote source is authoritative when it can be fetched successfully.
        published = remote
        write_output(published)
    elif previous:
        # Never destroy the last known-good public subscription.
        published = previous
    else:
        published = fallback
        write_output(published)

    status = {
        "remote_ok": remote_ok,
        "adapter": adapter,
        "remote_count": len(remote),
        "published_count": len(published),
        "fallback_count": len(fallback),
        "errors": errors,
        "checked_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        f"REMOTE_OK={str(remote_ok).lower()} ADAPTER={adapter} "
        f"REMOTE={len(remote)} PUBLISHED={len(published)} FALLBACK={len(fallback)}"
    )
    if errors:
        print("ADAPTER_ERRORS=" + ",".join(errors))

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("### Subscription sync\n\n")
            f.write(f"Remote OK: **{remote_ok}**  \n")
            f.write(f"Adapter: **{adapter}**  \n")
            f.write(f"Remote: **{len(remote)}** | Published: **{len(published)}** | Fallback: **{len(fallback)}**  \n")
            if errors:
                f.write("Adapters tried: `" + "`, `".join(errors) + "`\n")


if __name__ == "__main__":
    main()
