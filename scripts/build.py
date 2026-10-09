import base64
import binascii
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "sources.txt"
OUTPUT = ROOT / "sub.txt"
MAX_BYTES = 2_000_000

def parse(text):
    text = text.lstrip("\ufeff").strip()
    if not text:
        return []
    if not any(line.strip().startswith("vless://") for line in text.splitlines()):
        try:
            cleaned = "".join(text.split())
            raw = base64.b64decode(cleaned + "=" * (-len(cleaned) % 4), altchars=b"-_", validate=True)
            text = raw.decode("utf-8-sig")
        except (ValueError, UnicodeError, binascii.Error) as exc:
            raise ValueError("Not a VLESS or Base64 VLESS subscription") from exc
    return [line.strip() for line in text.splitlines() if line.strip().startswith("vless://")]

def get_subscription(url):
    current = url
    for hop in range(6):
        parsed = urllib.parse.urlsplit(current)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Subscription target is not HTTPS")
        request = urllib.request.Request(current, headers={"User-Agent": "V2Box/1.0", "Accept": "text/plain"})
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                body = response.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise ValueError("Subscription response too large")
                return body.decode("utf-8-sig")
        except urllib.error.HTTPError as exc:
            if exc.code not in (301, 302, 303, 307, 308):
                raise ValueError(f"Source returned HTTP {exc.code}") from exc
            location = exc.headers.get("Location", "")
            target = urllib.parse.urlsplit(urllib.parse.urljoin(current, location))
            print(f"Redirect HTTP {exc.code}: scheme={target.scheme or 'none'}, host={target.hostname or 'none'}")
            if target.scheme != "https":
                raise ValueError("Redirect is not HTTPS; may be a client-specific link")
            if not location:
                raise ValueError("Redirect has no Location header")
            current = urllib.parse.urljoin(current, location)
    raise ValueError("Too many redirects")

local = parse(SOURCE.read_text(encoding="utf-8"))
remote = []
url = os.environ.get("SOURCE_SUB_URL", "").strip()
if url:
    try:
        remote = parse(get_subscription(url))
        if not remote:
            raise ValueError("Remote subscription contains no VLESS links")
    except Exception as exc:
        print(f"Remote update failed: {type(exc).__name__}: {exc}; preserving previous subscription")
        raise SystemExit(0)

combined = list(dict.fromkeys(local + remote))
if not combined:
    print("No configurations; preserving previous subscription")
    raise SystemExit(0)
payload = ("\n".join(combined) + "\n").encode("utf-8")
OUTPUT.write_text(base64.b64encode(payload).decode("ascii") + "\n", encoding="ascii")
print(f"Subscription generated: {len(local)} local, {len(remote)} remote, {len(combined)} unique")
