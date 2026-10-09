import base64
import json
import re
import urllib.parse
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUB = ROOT / "sub.txt"
STATUS = ROOT / "selection-status.json"
SYNC_STATUS = ROOT / "sync-status.json"
TARGET = 20
MIN_TARGET = 19
MAX_TARGET = 22

# Keep the familiar useful set first. Extra LTE families are only fallbacks and
# must never displace the free TG entry from the normal 20-profile selection.
PRIORITY = [
    "auto", "germany", "sweden", "finland", "estonia", "poland", "russia",
    "lithuania", "latvia", "netherlands", "turkey", "usa", "france",
    "uk", "kazakhstan", "reserve", "lte1", "lte2", "lte3", "tg", "lte4", "lte5",
]

DISPLAY = {
    "auto": "Самый Быстрый АВТО",
    "germany": "🇩🇪 Германия",
    "sweden": "🇸🇪 Швеция",
    "finland": "🇫🇮 Финляндия",
    "estonia": "🇪🇪 Эстония",
    "poland": "🇵🇱 Польша",
    "russia": "🇷🇺 Россия",
    "lithuania": "🇱🇹 Литва",
    "latvia": "🇱🇻 Латвия",
    "netherlands": "🇳🇱 Нидерланды",
    "turkey": "🇹🇷 Турция",
    "usa": "🇺🇸 США",
    "france": "🇫🇷 Франция",
    "uk": "🇬🇧 Великобритания",
    "kazakhstan": "🇰🇿 Казахстан",
    "reserve": "🇩🇪 Обход Резерв (только Wi-Fi)",
    "lte1": "🇫🇮 LTE #1",
    "lte2": "🇫🇮 LTE #2",
    "lte3": "🇫🇮 LTE #3",
    "lte4": "🇫🇮 LTE #4",
    "lte5": "🇫🇮 LTE #5",
    "tg": "[FREE] ТОЛЬКО TG БОТ + САЙТ",
}

KEYWORDS = [
    ("auto", ["самый быстрый", "авто", "auto"]),
    ("germany", ["германи", "germany", "deutsch"]),
    ("sweden", ["швец", "sweden"]),
    ("finland", ["финлянд", "finland"]),
    ("estonia", ["эстон", "estonia"]),
    ("poland", ["польш", "poland"]),
    ("russia", ["росси", "russia"]),
    ("lithuania", ["литв", "lithuania"]),
    ("latvia", ["латви", "latvia"]),
    ("netherlands", ["нидер", "netherlands", "holland"]),
    ("turkey", ["турц", "turkey"]),
    ("usa", ["сша", "usa", "united states"]),
    ("france", ["франц", "france"]),
    ("uk", ["великобрит", "united kingdom", "britain", " england"]),
    ("kazakhstan", ["казах", "kazakhstan"]),
    ("reserve", ["резерв", "reserve", "backup"]),
    ("tg", ["только tg", "tg бот", "telegram", "сайт"]),
]

HOST_HINTS = {
    "de": "germany", "fi": "finland", "se": "sweden", "ee": "estonia",
    "pl": "poland", "ru": "russia", "lt": "lithuania", "lv": "latvia",
    "nl": "netherlands", "tr": "turkey", "us": "usa", "fr": "france",
    "gb": "uk", "uk": "uk", "kz": "kazakhstan",
}


def decode_sub():
    raw = SUB.read_text(encoding="ascii").strip()
    data = base64.b64decode(raw, validate=True).decode("utf-8")
    return [x.strip() for x in data.splitlines() if x.strip().startswith("vless://")]


def canonical(uri):
    p = urllib.parse.urlsplit(uri)
    q = tuple(sorted(urllib.parse.parse_qsl(p.query, keep_blank_values=True)))
    return (p.username or "", (p.hostname or "").lower(), p.port, q)


def score(uri):
    """Heuristic profile quality; this is not a latency/speed measurement."""
    p = urllib.parse.urlsplit(uri)
    q = dict(urllib.parse.parse_qsl(p.query, keep_blank_values=True))
    host = p.hostname or ""
    s = 0
    if p.port == 443:
        s += 8
    if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", host):
        s += 5
    if q.get("security") == "reality":
        s += 4
    if q.get("flow") == "xtls-rprx-vision":
        s += 3
    if q.get("type") == "tcp":
        s += 3
    if q.get("type") == "grpc":
        s += 1
    return s


def original_name(uri):
    return urllib.parse.unquote(urllib.parse.urlsplit(uri).fragment or "").replace("+", " ").strip()


def family(uri):
    name = original_name(uri)
    low = name.lower()
    m = re.search(r"\blte\s*#?\s*([1-5])\b", low)
    if m:
        return "lte" + m.group(1)
    for key, words in KEYWORDS:
        if any(w in low for w in words):
            return key

    p = urllib.parse.urlsplit(uri)
    q = dict(urllib.parse.parse_qsl(p.query, keep_blank_values=True))
    hay = " ".join([p.hostname or "", q.get("sni", "")]).lower()
    for code, key in HOST_HINTS.items():
        if re.search(rf"(^|[.\-_]){re.escape(code)}([.\-_]|$)", hay):
            return key

    cleaned = re.sub(r"\s*[-–—]\s*\d+\s*$", "", name).strip()
    cleaned = re.sub(r"\s*#\s*\d+\s*$", "", cleaned).strip()
    return cleaned.lower() or "other"


def pretty_name(fam, original):
    if fam in DISPLAY:
        return DISPLAY[fam]
    clean = re.sub(r"\s*[-–—]\s*\d+\s*$", "", original).strip()
    clean = re.sub(r"\s*#\s*\d+\s*$", "", clean).strip()
    return clean or "🌐 Сервер"


def rename(uri, name):
    base = uri.split("#", 1)[0]
    return base + "#" + urllib.parse.quote(name, safe="")


def validate_selected_uri(uri):
    p = urllib.parse.urlsplit(uri)
    if p.scheme.lower() != "vless":
        raise ValueError("non-VLESS entry selected")
    if not p.username:
        raise ValueError("VLESS UUID/user is empty")
    if not p.hostname:
        raise ValueError("VLESS host is empty")
    if p.port is None or not 1 <= p.port <= 65535:
        raise ValueError("VLESS port is invalid")


def main():
    links = decode_sub()
    uniq = []
    seen = set()
    for link in links:
        k = canonical(link)
        if k not in seen:
            seen.add(k)
            uniq.append(link)

    groups = defaultdict(list)
    for link in uniq:
        groups[family(link)].append(link)
    for vals in groups.values():
        vals.sort(key=score, reverse=True)

    ordered_families = [x for x in PRIORITY if x in groups]
    ordered_families += sorted(x for x in groups if x not in ordered_families)

    # One best technical endpoint per distinct location/family.
    selected = [(fam, groups[fam][0]) for fam in ordered_families[:MAX_TARGET]]
    if len(selected) > TARGET:
        selected = selected[:TARGET]

    # Never replace the public file with an unexpectedly small refresh.
    if len(selected) < MIN_TARGET:
        raise SystemExit(
            f"Only {len(selected)} distinct location families found; need at least {MIN_TARGET}"
        )

    output = []
    chosen = []
    for fam, uri in selected:
        validate_selected_uri(uri)
        name = pretty_name(fam, original_name(uri))
        output.append(rename(uri, name))
        p = urllib.parse.urlsplit(uri)
        chosen.append(
            {
                "name": name,
                "family": fam,
                "host": p.hostname,
                "port": p.port,
                "transport": dict(urllib.parse.parse_qsl(p.query)).get("type"),
                "score": score(uri),
            }
        )

    technical = [canonical(x) for x in output]
    names = [urllib.parse.unquote(urllib.parse.urlsplit(x).fragment or "") for x in output]
    if len(technical) != len(set(technical)):
        raise SystemExit("Technical duplicates remain after curation")
    if len(names) != len(set(names)):
        raise SystemExit("Duplicate display names remain after curation")

    payload = ("\n".join(output) + "\n").encode("utf-8")
    SUB.write_text(base64.b64encode(payload).decode("ascii") + "\n", encoding="ascii")

    selection_status = {
        "input_count": len(links),
        "technical_unique": len(uniq),
        "published_count": len(output),
        "families": len(groups),
        "selected": chosen,
        "curated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    STATUS.write_text(
        json.dumps(selection_status, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # Keep sync-status semantically correct after the curation stage.
    if SYNC_STATUS.exists():
        sync_status = json.loads(SYNC_STATUS.read_text(encoding="utf-8"))
        sync_status["published_count"] = len(output)
        sync_status["distinct_families"] = len(groups)
        sync_status["curated_at_utc"] = selection_status["curated_at_utc"]
        SYNC_STATUS.write_text(
            json.dumps(sync_status, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    print(
        f"FILTERED input={len(links)} unique={len(uniq)} "
        f"families={len(groups)} published={len(output)}"
    )


if __name__ == "__main__":
    main()
