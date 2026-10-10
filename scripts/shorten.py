import base64
import binascii
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "short-url.txt"
TARGET = "https://raw.githubusercontent.com/Islam-lab-hash/vpnus-sub/main/sub.txt"
API = "https://clck.ru/--"
MAX_BYTES = 200_000


def fetch_text(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "text/plain, */*"},
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError("response too large")
        return body.decode("utf-8-sig").strip()


def is_subscription_payload(text: str) -> bool:
    try:
        compact = "".join(text.split())
        decoded = base64.b64decode(
            compact + "=" * (-len(compact) % 4),
            altchars=b"-_",
            validate=True,
        ).decode("utf-8")
    except (binascii.Error, UnicodeError, ValueError):
        return False
    links = [line.strip() for line in decoded.splitlines() if line.strip().startswith("vless://")]
    return 1 <= len(links) <= 120


def is_valid_short(url: str) -> bool:
    if not url.startswith("https://clck.ru/"):
        return False
    try:
        return is_subscription_payload(fetch_text(url))
    except Exception:
        return False


def create_short() -> str:
    query = urllib.parse.urlencode({"url": TARGET})
    short = fetch_text(API + "?" + query)
    if not short.startswith("https://clck.ru/"):
        raise RuntimeError(f"Unexpected clck.ru response: {short[:120]!r}")
    if not is_valid_short(short):
        raise RuntimeError(f"Created clck.ru URL does not return a valid subscription: {short}")
    return short


def main():
    existing = OUT.read_text(encoding="utf-8").strip() if OUT.exists() else ""
    if existing and is_valid_short(existing):
        print(f"SHORT_URL_REUSED={existing}")
        return

    short = create_short()
    OUT.write_text(short + "\n", encoding="utf-8")
    print(f"SHORT_URL_CREATED={short}")


if __name__ == "__main__":
    main()
