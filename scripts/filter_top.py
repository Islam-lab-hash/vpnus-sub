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

PRIORITY = [
    "auto", "germany", "sweden", "finland", "estonia", "poland", "russia",
    "lithuania", "latvia", "netherlands", "turkey", "usa", "france",
    "uk", "kazakhstan", "reserve", "lte", "tg",
]

DISPLAY = {
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
    "reserve": "🇩🇪 Обход Резерв",
    "lte": "📶 LTE",
    "tg": "🆓 TG БОТ + САЙТ",
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

PREFERRED_AUTO_HOST = "test999.cool-raven.test-cdn-kkk.com"
PREFERRED_AUTO_SNI = "faster.lizeg.ru"


def decode_sub():
    raw = SUB.read_text(encoding="ascii").strip()
    data = base64.b64decode(raw, validate=True).decode("utf-8")
    return [x.strip() for x in data.splitlines() if x.strip().startswith("vless://")]


def canonical(uri):
    p = urllib.parse.urlsplit(uri)
    q = tuple(sorted(urllib.parse.parse_qsl(p.query, keep_blank_values=True)))
    return (p.username or "", (p.hostname or "").lower(), p.port, q)


def original_name(uri):
    return urllib.parse.unquote(urllib.parse.urlsplit(uri).fragment or "").replace("+", " ").strip()


def score(uri):
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
    elif q.get("type") == "grpc":
        s += 2
    elif q.get("type") in ("xhttp", "splithttp"):
        s += 1
    return s


def auto_rank(uri):
    """Prefer the provider's known Fast AUTO endpoint before generic AUTO variants."""
    p = urllib.parse.urlsplit(uri)
    q = dict(urllib.parse.parse_qsl(p.query, keep_blank_values=True))
    name = original_name(uri).lower()
    host = (p.hostname or "").lower()
    sni = q.get("sni", "").lower()
    preferred = int(
        host == PREFERRED_AUTO_HOST
        or sni == PREFERRED_AUTO_SNI
        or "самый быстрый" in name
    )
    return (-preferred, -score(uri), host, sni, name)


def family(uri):
    name = original_name(uri)
    low = name.lower()
    if re.search(r"\blte\b", low):
        return "lte"
    for key, words in KEYWORDS:
        if any(w in low for w in words):
            return key

    # Fallback is intentionally hostname-only. Generic REALITY SNI values such as
    # *.ru must not relabel an otherwise unknown profile as Russia.
    p = urllib.parse.urlsplit(uri)
    hay = (p.hostname or "").lower()
    for code, key in HOST_HINTS.items():
        if re.search(rf"(^|[.\-_]){re.escape(code)}([.\-_]|$)", hay):
            return key

    clean = re.sub(r"\s*[-–—]\s*\d+(?:\.\d+)?\s*$", "", name).strip()
    clean = re.sub(r"\s*#\s*\d+\s*$", "", clean).strip()
    return clean.lower() or "other"


def base_display(fam, original):
    if fam in DISPLAY:
        return DISPLAY[fam]
    clean = re.sub(r"\s*[-–—]\s*\d+(?:\.\d+)?\s*$", "", original).strip()
    clean = re.sub(r"\s*#\s*\d+\s*$", "", clean).strip()
    return "🌐 " + (clean or "Сервер")


def rename(uri, name):
    return uri.split("#", 1)[0] + "#" + urllib.parse.quote(name, safe="")


def validate_uri(uri):
    p = urllib.parse.urlsplit(uri)
    if p.scheme.lower() != "vless" or not p.username or not p.hostname:
        raise ValueError("invalid VLESS entry")
    if p.port is None or not 1 <= p.port <= 65535:
        raise ValueError("invalid VLESS port")


def metadata(uri, name, fam, role="server", alias_of=None):
    p = urllib.parse.urlsplit(uri)
    q = dict(urllib.parse.parse_qsl(p.query, keep_blank_values=True))
    row = {
        "name": name,
        "family": fam,
        "role": role,
        "host": p.hostname,
        "port": p.port,
        "transport": q.get("type", "tcp"),
        "sni": q.get("sni"),
        "score": score(uri),
    }
    if alias_of:
        row["alias_of"] = alias_of
    return row


def main():
    links = decode_sub()

    # Keep all distinct technical variants; remove only exact technical duplicates.
    uniq, seen = [], set()
    for link in links:
        key = canonical(link)
        if key not in seen:
            seen.add(key)
            uniq.append(link)

    groups = defaultdict(list)
    for link in uniq:
        groups[family(link)].append(link)

    for fam, vals in groups.items():
        if fam == "auto":
            vals.sort(key=auto_rank)
        else:
            vals.sort(key=lambda x: (-score(x), (urllib.parse.urlsplit(x).hostname or ""), original_name(x)))

    ordered_families = [x for x in PRIORITY if x in groups]
    ordered_families += sorted(x for x in groups if x not in ordered_families)

    output, chosen = [], []

    if groups.get("auto"):
        for idx, uri in enumerate(groups["auto"], start=1):
            validate_uri(uri)
            name = "⚡ Самый быстрый АВТО" if idx == 1 else f"⚡ AUTO провайдера — {idx}"
            output.append(rename(uri, name))
            chosen.append(metadata(uri, name, "auto", role="provider-auto"))

    # Plain VLESS subscriptions have no native latency-based proxy group. LTE AUTO
    # is therefore a stable alias to the strongest complete LTE profile at build time.
    if groups.get("lte"):
        lte_target = groups["lte"][0]
        alias_name = "📶 LTE АВТО"
        output.append(rename(lte_target, alias_name))
        chosen.append(metadata(uri=lte_target, name=alias_name, fam="lte", role="lte-auto", alias_of="📶 LTE — 1"))

    for fam in ordered_families:
        if fam == "auto":
            continue
        vals = groups[fam]
        base = base_display(fam, original_name(vals[0]))
        for idx, uri in enumerate(vals, start=1):
            validate_uri(uri)
            name = f"{base} — {idx}"
            output.append(rename(uri, name))
            chosen.append(metadata(uri, name, fam))

    if not output:
        raise SystemExit("No usable configurations after grouping")

    names = [urllib.parse.unquote(urllib.parse.urlsplit(x).fragment or "") for x in output]
    if len(names) != len(set(names)):
        raise SystemExit("Duplicate display names remain after numbering")

    SUB.write_text(
        base64.b64encode(("\n".join(output) + "\n").encode("utf-8")).decode("ascii") + "\n",
        encoding="ascii",
    )

    auto_target = chosen[0] if chosen and chosen[0].get("role") == "provider-auto" else None
    lte_auto_target = next((x for x in chosen if x.get("role") == "lte-auto"), None)
    status = {
        "input_count": len(links),
        "technical_unique": len(uniq),
        "published_count": len(output),
        "families": len(groups),
        "family_counts": {fam: len(groups[fam]) for fam in ordered_families},
        "auto_target": auto_target,
        "lte_auto_target": lte_auto_target,
        "selected": chosen,
        "curated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if SYNC_STATUS.exists():
        sync_status = json.loads(SYNC_STATUS.read_text(encoding="utf-8"))
        sync_status["published_count"] = len(output)
        sync_status["distinct_families"] = len(groups)
        sync_status["curated_at_utc"] = status["curated_at_utc"]
        SYNC_STATUS.write_text(json.dumps(sync_status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        f"CURATED input={len(links)} technical_unique={len(uniq)} "
        f"families={len(groups)} published={len(output)}"
    )
    if auto_target:
        print(f"AUTO_TARGET={auto_target['host']}:{auto_target['port']} sni={auto_target.get('sni')}")
    if lte_auto_target:
        print(f"LTE_AUTO_TARGET={lte_auto_target['host']}:{lte_auto_target['port']} sni={lte_auto_target.get('sni')}")
    print("FAMILY_COUNTS=" + json.dumps(status["family_counts"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
