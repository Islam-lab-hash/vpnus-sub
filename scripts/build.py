"""Build a stable Base64 VLESS subscription from sources.txt.

Optional remote source is used only when it returns valid configurations.
Remote failures never erase the last published subscription.
"""
import base64
import binascii
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "sources.txt"
OUTPUT = ROOT / "sub.txt"

def parse(text):
    text = text.lstrip("\ufeff").strip()
    if not text:
        return []
    if not any(line.strip().startswith("vless://") for line in text.splitlines()):
        try:
            compact = "".join(text.split())
            text = base64.b64decode(compact + "=" * (-len(compact) % 4), altchars=b"-_", validate=True).decode("utf-8-sig")
        except (binascii.Error, UnicodeError, ValueError) as exc:
            raise ValueError("Not a supported VLESS text/Base64 subscription") from exc
    return [line.strip() for line in text.splitlines() if line.strip().startswith("vless://")]

def fetch_remote(url):
    if not url.startswith("https://"):
        raise ValueError("Source URL must be HTTPS")
    request = urllib.request.Request(url, headers={"User-Agent": "Happ/4.3.0", "Accept": "text/plain, */*"})
    with urllib.request.urlopen(request, timeout=20) as response:
        data = response.read(2_000_001)
        if len(data) > 2_000_000:
            raise ValueError("Remote response too large")
        return parse(data.decode("utf-8-sig"))

def main():
    local = parse(SOURCE.read_text(encoding="utf-8"))
    if not local:
        raise ValueError("sources.txt has no VLESS links")
    remote = []
    status = "NOT_CONFIGURED"
    url = os.environ.get("SOURCE_SUB_URL", "").strip()
    if url:
        try:
            remote = fetch_remote(url)
            if not remote:
                raise ValueError("No VLESS links in remote response")
            status = "OK"
        except urllib.error.HTTPError as exc:
            status = f"HTTP_{exc.code}"
        except Exception as exc:
            status = f"ERROR_{type(exc).__name__}"
    unique = list(dict.fromkeys(local + remote))
    data = base64.b64encode(("\n".join(unique) + "\n").encode("utf-8")).decode("ascii") + "\n"
    if not OUTPUT.exists() or OUTPUT.read_text(encoding="ascii") != data:
        OUTPUT.write_text(data, encoding="ascii")
    print(f"LOCAL={len(local)} REMOTE={len(remote)} UNIQUE={len(unique)} REMOTE_STATUS={status}")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(f"### Subscription build\n\nLocal: {len(local)} | Remote: {len(remote)} | Total: {len(unique)}\n\nRemote status: **{status}**\n")
    if status not in ("OK", "NOT_CONFIGURED"):
        print("WARNING: Remote source unavailable. Publishing local configurations only.", file=sys.stderr)

if __name__ == "__main__":
    main()
