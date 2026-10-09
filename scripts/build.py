"""Build a Base64 VLESS subscription from local and authorized remote sources."""
import base64
import binascii
import hashlib
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "sources.txt"
OUTPUT = ROOT / "sub.txt"
MAX_BYTES = 2_000_000
MAX_REDIRECTS = 8

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None

OPENER = urllib.request.build_opener(NoRedirect)

def parse_subscription(text):
    text = text.lstrip("\ufeff").strip()
    if not text:
        return []
    if not any(line.strip().startswith("vless://") for line in text.splitlines()):
        try:
            compact = "".join(text.split())
            decoded = base64.b64decode(compact + "=" * (-len(compact) % 4), altchars=b"-_", validate=True)
            text = decoded.decode("utf-8-sig")
        except (binascii.Error, UnicodeError, ValueError) as exc:
            raise ValueError("Source is not a supported plain-text/Base64 VLESS subscription") from exc
    return [line.strip() for line in text.splitlines() if line.strip().startswith("vless://")]

def fingerprint(url):
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]

def fetch_subscription(url):
    seen = set()
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        parsed = urllib.parse.urlsplit(current)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Source or redirect URL must be HTTPS")
        fp = fingerprint(current)
        if fp in seen:
            raise ValueError(f"Redirect loop detected at URL fingerprint {fp}")
        seen.add(fp)
        req = urllib.request.Request(current, headers={
            "User-Agent": "Happ/4.3.0",
            "Accept": "text/plain, */*",
        })
        try:
            with OPENER.open(req, timeout=25) as response:
                body = response.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise ValueError("Subscription response exceeds 2 MB")
                return body.decode("utf-8-sig")
        except urllib.error.HTTPError as exc:
            if exc.code not in (301, 302, 303, 307, 308):
                raise ValueError(f"Source returned HTTP {exc.code}") from None
            location = exc.headers.get("Location")
            if not location:
                raise ValueError(f"HTTP {exc.code} has no Location header")
            target = urllib.parse.urljoin(current, location)
            target_parts = urllib.parse.urlsplit(target)
            print(f"HTTP {exc.code} redirect: {fp} -> {fingerprint(target)}, host={target_parts.hostname}", flush=True)
            if target_parts.scheme != "https":
                raise ValueError("Source redirects to a non-HTTPS link")
            current = target
    raise ValueError("Exceeded redirect limit")

def main():
    local = parse_subscription(SOURCE.read_text(encoding="utf-8"))
    source_url = os.environ.get("SOURCE_SUB_URL", "").strip()
    if not source_url:
        print("ERROR: SOURCE_SUB_URL secret is missing; existing sub.txt retained", file=sys.stderr)
        return 1
    try:
        remote = parse_subscription(fetch_subscription(source_url))
        if not remote:
            raise ValueError("Remote response has no VLESS links")
    except Exception as exc:
        print(f"ERROR: Remote sync failed ({type(exc).__name__}: {exc}); existing sub.txt retained", file=sys.stderr)
        return 1
    unique = list(dict.fromkeys(local + remote))
    payload = ("\n".join(unique) + "\n").encode("utf-8")
    new_text = base64.b64encode(payload).decode("ascii") + "\n"
    if not OUTPUT.exists() or OUTPUT.read_text(encoding="ascii") != new_text:
        OUTPUT.write_text(new_text, encoding="ascii")
    print(f"Subscription generated: {len(local)} local, {len(remote)} remote, {len(unique)} unique")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
