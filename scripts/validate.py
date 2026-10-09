import base64
import json
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUB = ROOT / "sub.txt"
SELECTION = ROOT / "selection-status.json"
SYNC = ROOT / "sync-status.json"
MIN_COUNT = 19
MAX_COUNT = 22
CRITICAL_REALITY = ("pbk", "sni", "sid", "fp")


def canonical(uri: str):
    p = urllib.parse.urlsplit(uri)
    q = tuple(sorted(urllib.parse.parse_qsl(p.query, keep_blank_values=True)))
    return (p.username or "", (p.hostname or "").lower(), p.port, q)


def main():
    raw = SUB.read_text(encoding="ascii").strip()
    if not raw:
        raise SystemExit("sub.txt is empty")

    try:
        decoded = base64.b64decode(raw, validate=True).decode("utf-8")
    except Exception as exc:
        raise SystemExit(f"sub.txt is not valid Base64 UTF-8: {type(exc).__name__}") from exc

    links = [line.strip() for line in decoded.splitlines() if line.strip()]
    if not MIN_COUNT <= len(links) <= MAX_COUNT:
        raise SystemExit(f"Expected {MIN_COUNT}-{MAX_COUNT} entries, got {len(links)}")

    keys = []
    names = []
    for index, uri in enumerate(links, start=1):
        p = urllib.parse.urlsplit(uri)
        if p.scheme.lower() != "vless":
            raise SystemExit(f"Entry {index} is not VLESS")
        if not p.username:
            raise SystemExit(f"Entry {index} has no VLESS UUID/user")
        if not p.hostname:
            raise SystemExit(f"Entry {index} has no host")
        try:
            port = p.port
        except ValueError as exc:
            raise SystemExit(f"Entry {index} has invalid port") from exc
        if port is None or not 1 <= port <= 65535:
            raise SystemExit(f"Entry {index} has invalid port")

        q = dict(urllib.parse.parse_qsl(p.query, keep_blank_values=True))
        if q.get("security", "").lower() == "reality":
            missing = [k for k in CRITICAL_REALITY if not q.get(k)]
            if missing:
                raise SystemExit(
                    f"Entry {index} has incomplete REALITY settings: missing {','.join(missing)}"
                )
            if q.get("type", "tcp").lower() == "tcp" and not q.get("flow"):
                raise SystemExit(f"Entry {index} TCP REALITY profile has no flow")

        name = urllib.parse.unquote(p.fragment or "").strip()
        if not name:
            raise SystemExit(f"Entry {index} has no display name")
        keys.append(canonical(uri))
        names.append(name)

    if len(keys) != len(set(keys)):
        raise SystemExit("Technical duplicate VLESS configurations found")
    if len(names) != len(set(names)):
        raise SystemExit("Duplicate display names found")

    selection = json.loads(SELECTION.read_text(encoding="utf-8"))
    sync = json.loads(SYNC.read_text(encoding="utf-8"))

    if selection.get("published_count") != len(links):
        raise SystemExit("selection-status.json published_count does not match sub.txt")
    if len(selection.get("selected", [])) != len(links):
        raise SystemExit("selection-status.json selected list does not match sub.txt")
    if sync.get("published_count") != len(links):
        raise SystemExit("sync-status.json published_count does not match final sub.txt")

    print(
        f"VALID_SUBSCRIPTION={len(links)} "
        f"FAMILIES={selection.get('families')} REMOTE_OK={sync.get('remote_ok')}"
    )


if __name__ == "__main__":
    main()
