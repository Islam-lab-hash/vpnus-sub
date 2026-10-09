import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "short-url.txt"
TARGET = "https://raw.githubusercontent.com/Islam-lab-hash/vpnus-sub/main/sub.txt"
API = "https://clck.ru/--"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def resolve_once(url: str):
    opener = urllib.request.build_opener(NoRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with opener.open(req, timeout=20) as response:
            return response.getcode(), response.headers.get("Location")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Location")


def is_valid_short(url: str) -> bool:
    if not url.startswith("https://clck.ru/"):
        return False
    code, location = resolve_once(url)
    if code not in (301, 302, 303, 307, 308) or not location:
        return False
    return urllib.parse.urljoin(url, location) == TARGET


def create_short() -> str:
    query = urllib.parse.urlencode({"url": TARGET})
    req = urllib.request.Request(API + "?" + query, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as response:
        short = response.read(4096).decode("utf-8", "strict").strip()
    if not is_valid_short(short):
        raise RuntimeError(f"clck.ru returned an invalid short link: {short[:80]!r}")
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
