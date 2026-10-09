import base64
import os
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "sources.txt"
OUTPUT = ROOT / "sub.txt"

def parse(text):
    text = text.strip()
    if not text:
        return []
    if "vless://" not in text:
        try:
            raw = base64.b64decode(text + "=" * (-len(text) % 4), validate=True)
            text = raw.decode("utf-8")
        except (ValueError, UnicodeError) as exc:
            raise ValueError("Unsupported subscription format") from exc
    return [line.strip() for line in text.splitlines() if line.strip().startswith("vless://")]

local = parse(SOURCE.read_text(encoding="utf-8"))
remote = []
url = os.environ.get("SOURCE_SUB_URL", "").strip()
if url:
    if not url.startswith("https://"):
        raise SystemExit("SOURCE_SUB_URL must use HTTPS")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "V2Box/1.0", "Accept": "text/plain"})
        with urllib.request.urlopen(request, timeout=25) as response:
            body = response.read(2_000_001)
        if len(body) > 2_000_000:
            raise ValueError("Subscription response too large")
        remote = parse(body.decode("utf-8-sig"))
        if not remote:
            raise ValueError("Subscription has no VLESS configurations")
    except urllib.error.HTTPError as exc:
        print(f"Remote update failed (HTTP {exc.code}); preserving previous subscription")
        raise SystemExit(0)
    except Exception as exc:
        print(f"Remote update failed ({type(exc).__name__}); preserving previous subscription")
        raise SystemExit(0)

combined = list(dict.fromkeys(local + remote))
if not combined:
    print("No configurations; preserving previous subscription")
    raise SystemExit(0)

payload = ("\n".join(combined) + "\n").encode("utf-8")
OUTPUT.write_text(base64.b64encode(payload).decode("ascii") + "\n", encoding="ascii")
print(f"Subscription generated: {len(local)} local, {len(remote)} remote, {len(combined)} unique")
