import base64
import json
import re
import urllib.parse
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUB = ROOT / "sub.txt"
STATUS = ROOT / "selection-status.json"
TARGET = 20

PRIORITY = [
    "auto", "germany", "sweden", "finland", "estonia", "poland", "russia",
    "lithuania", "latvia", "netherlands", "turkey", "usa", "france",
    "uk", "kazakhstan", "reserve", "lte1", "lte2", "lte3", "lte4", "lte5",
    "tg"
]

DISPLAY = {
    "auto": "⚡ Самый быстрый АВТО",
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
    "reserve": "🛟 Резерв",
    "lte1": "📶 LTE #1",
    "lte2": "📶 LTE #2",
    "lte3": "📶 LTE #3",
    "lte4": "📶 LTE #4",
    "lte5": "📶 LTE #5",
    "tg": "🆓 TG + Сайт",
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
    p = urllib.parse.urlsplit(uri)
    q = dict(urllib.parse.parse_qsl(p.query, keep_blank_values=True))
    host = p.hostname or ""
    s = 0
    if p.port == 443: s += 8
    if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", host): s += 5
    if q.get("security") == "reality": s += 4
    if q.get("flow") == "xtls-rprx-vision": s += 3
    if q.get("type") == "tcp": s += 3
    if q.get("type") == "grpc": s += 1
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

    selected = []
    used = defaultdict(int)

    # First pass: one best endpoint per distinct location/family.
    ordered_families = [x for x in PRIORITY if x in groups]
    ordered_families += [x for x in groups if x not in ordered_families]
    for fam in ordered_families:
        if len(selected) >= TARGET:
            break
        if groups[fam]:
            selected.append((fam, groups[fam][0]))
            used[fam] = 1

    # Second pass: add backups only for strongest/useful families until target is reached.
    backup_priority = ["auto", "germany", "finland", "sweden", "netherlands", "russia", "reserve", "lte1", "lte2", "lte3"]
    backup_priority += [x for x in ordered_families if x not in backup_priority]
    round_no = 1
    while len(selected) < min(TARGET, len(uniq)):
        added = False
        for fam in backup_priority:
            idx = used[fam]
            if idx < len(groups.get(fam, [])):
                selected.append((fam, groups[fam][idx]))
                used[fam] += 1
                added = True
                if len(selected) >= min(TARGET, len(uniq)):
                    break
        if not added:
            break
        round_no += 1
        if round_no > 5:
            break

    counts = defaultdict(int)
    output = []
    chosen = []
    for fam, uri in selected:
        counts[fam] += 1
        base_name = pretty_name(fam, original_name(uri))
        name = base_name if counts[fam] == 1 else f"{base_name} • Резерв {counts[fam]-1}"
        output.append(rename(uri, name))
        p = urllib.parse.urlsplit(uri)
        chosen.append({
            "name": name,
            "family": fam,
            "host": p.hostname,
            "port": p.port,
            "transport": dict(urllib.parse.parse_qsl(p.query)).get("type"),
            "score": score(uri),
        })

    assert len({canonical(x) for x in output}) == len(output)
    payload = ("\n".join(output) + "\n").encode("utf-8")
    SUB.write_text(base64.b64encode(payload).decode("ascii") + "\n", encoding="ascii")
    STATUS.write_text(json.dumps({
        "input_count": len(links),
        "technical_unique": len(uniq),
        "published_count": len(output),
        "families": len(groups),
        "selected": chosen,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"FILTERED input={len(links)} unique={len(uniq)} families={len(groups)} published={len(output)}")

if __name__ == "__main__":
    main()
