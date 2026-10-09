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

# Country order in the public subscription. Families with no client-confirmed
# reachable profile are intentionally omitted until a later health sample proves
# that they work again.
PRIORITY = [
    "auto", "germany", "sweden", "finland", "estonia", "poland", "russia",
    "lithuania", "latvia", "netherlands", "turkey", "usa", "france",
    "uk", "kazakhstan", "reserve", "lte", "tg",
]
DISPLAY = {
    "germany": "🇩🇪 Германия", "sweden": "🇸🇪 Швеция", "finland": "🇫🇮 Финляндия",
    "estonia": "🇪🇪 Эстония", "poland": "🇵🇱 Польша", "russia": "🇷🇺 Россия",
    "lithuania": "🇱🇹 Литва", "latvia": "🇱🇻 Латвия", "netherlands": "🇳🇱 Нидерланды",
    "turkey": "🇹🇷 Турция", "usa": "🇺🇸 США", "france": "🇫🇷 Франция",
    "uk": "🇬🇧 Великобритания", "kazakhstan": "🇰🇿 Казахстан",
    "reserve": "🇩🇪 Обход Резерв", "lte": "📶 LTE", "tg": "🆓 TG БОТ + САЙТ",
}
KEYWORDS = [
    ("auto", ["самый быстрый", "авто", "auto"]),
    ("germany", ["германи", "germany", "deutsch"]),
    ("sweden", ["швец", "sweden"]), ("finland", ["финлянд", "finland"]),
    ("estonia", ["эстон", "estonia"]), ("poland", ["польш", "poland"]),
    ("russia", ["росси", "russia"]), ("lithuania", ["литв", "lithuania"]),
    ("latvia", ["латви", "latvia"]),
    ("netherlands", ["нидер", "netherlands", "holland"]),
    ("turkey", ["турц", "turkey"]), ("usa", ["сша", "usa", "united states"]),
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

# Measured in the user's actual client on 2026-10-09. Only rows with a numeric
# latency were admitted. n/a rows are deliberately absent. The key excludes the
# display name and UUID, so provider renaming/credential rotation does not break
# the health decision; the complete original VLESS URI is still published.
# key = (host, port, transport, reality/tls SNI) -> observed latency in ms
HEALTHY = {
    "auto": {
        ("31.177.111.37", 443, "grpc", "www.gazeta-business.ru"): 681,
    },
    "germany": {
        ("31.177.111.37", 443, "grpc", "www.gazeta-business.ru"): 812,
        ("91.240.87.221", 443, "grpc", "www.gazeta-business.ru"): 384,
    },
    "sweden": {
        ("test.fast-heron.test-cdn-kkk.com", 443, "tcp", "quick.lizeg.ru"): 548,
        ("91.240.87.221", 443, "grpc", "www.gazeta-business.ru"): 3166,
    },
    "finland": {
        ("91.240.87.221", 443, "grpc", "www.gazeta-business.ru"): 501,
    },
    "estonia": {
        ("31.177.111.37", 443, "grpc", "www.gazeta-business.ru"): 2106,
        ("91.240.87.221", 443, "grpc", "www.gazeta-business.ru"): 585,
    },
    "poland": {
        ("test.wild-vault.test-cdn-kkk.com", 443, "tcp", "cdn-pl.lizeg.ru"): 638,
        ("31.177.111.37", 443, "grpc", "www.gazeta-business.ru"): 797,
        ("91.240.87.221", 443, "grpc", "www.gazeta-business.ru"): 728,
    },
    "russia": {
        ("test.tall-blaze.test-cdn-kkk.com", 443, "tcp", "keycdn.com"): 500,
        ("31.177.111.37", 443, "grpc", "www.gazeta-business.ru"): 2245,
    },
    "lithuania": {
        ("test.pure-cedar.test-cdn-kkk.com", 443, "tcp", "li.cdn-tech-images.com"): 639,
        ("31.177.111.37", 443, "grpc", "www.gazeta-business.ru"): 848,
    },
    "latvia": {
        ("test.wolf-moose.test-cdn-kkk.com", 443, "tcp", "la.cdn-tech-images.com"): 1193,
        ("31.177.111.37", 443, "grpc", "www.gazeta-business.ru"): 1551,
        ("91.240.87.221", 443, "grpc", "www.gazeta-business.ru"): 1748,
    },
    "netherlands": {
        ("test.dawn-blade.test-cdn-kkk.com", 443, "tcp", "nl.lizeg.ru"): 892,
    },
    "usa": {
        ("test.mint-lance.test-cdn-kkk.com", 443, "tcp", "us.cdn-tech-images.com"): 1613,
        ("91.240.87.221", 443, "grpc", "www.gazeta-business.ru"): 4920,
    },
    "uk": {
        ("test.bright-cloud.test-cdn-kkk.com", 443, "tcp", "gb.lizeg.ru"): 678,
    },
    "reserve": {
        ("test.pure-vault.test-cdn-kkk.com", 9443, "xhttp", "de-new.lizeg.ru"): 817,
    },
    "lte": {
        ("test.rare-glen.test-cdn-kkk.com", 443, "tcp", "ads.x5.ru"): 474,
        ("test.bay-glep.test-cdn-kkk.com", 443, "grpc", "gazeta-business.ru"): 598,
        ("test.gold-cliff.test-cdn-kkk.com", 443, "grpc", "gazeta-business.ru"): 613,
    },
}
MIN_HEALTHY_TECHNICAL = 20


def decode_sub():
    raw = SUB.read_text(encoding="ascii").strip()
    text = base64.b64decode(raw, validate=True).decode("utf-8")
    return [x.strip() for x in text.splitlines() if x.strip().startswith("vless://")]


def parts(uri):
    p = urllib.parse.urlsplit(uri)
    q = dict(urllib.parse.parse_qsl(p.query, keep_blank_values=True))
    return p, q


def canonical(uri):
    p, q = parts(uri)
    return (p.username or "", (p.hostname or "").lower(), p.port, tuple(sorted(q.items())))


def original_name(uri):
    return urllib.parse.unquote(urllib.parse.urlsplit(uri).fragment or "").replace("+", " ").strip()


def family(uri):
    name = original_name(uri)
    low = name.lower()
    if re.search(r"\blte\b", low):
        return "lte"
    for key, words in KEYWORDS:
        if any(word in low for word in words):
            return key
    host = (urllib.parse.urlsplit(uri).hostname or "").lower()
    for code, key in HOST_HINTS.items():
        if re.search(rf"(^|[.\-_]){re.escape(code)}([.\-_]|$)", host):
            return key
    clean = re.sub(r"\s*[-–—]\s*\d+(?:\.\d+)?\s*$", "", name).strip()
    clean = re.sub(r"\s*#\s*\d+\s*$", "", clean).strip()
    return clean.lower() or "other"


def health_key(uri):
    p, q = parts(uri)
    return (
        (p.hostname or "").lower(),
        p.port,
        q.get("type", "tcp").lower(),
        q.get("sni", "").lower(),
    )


def observed_ms(fam, uri):
    return HEALTHY.get(fam, {}).get(health_key(uri))


def base_display(fam, original):
    if fam in DISPLAY:
        return DISPLAY[fam]
    clean = re.sub(r"\s*[-–—]\s*\d+(?:\.\d+)?\s*$", "", original).strip()
    clean = re.sub(r"\s*#\s*\d+\s*$", "", clean).strip()
    return "🌐 " + (clean or "Сервер")


def rename(uri, name):
    return uri.split("#", 1)[0] + "#" + urllib.parse.quote(name, safe="")


def validate_uri(uri):
    p, q = parts(uri)
    if p.scheme.lower() != "vless" or not p.username or not p.hostname:
        raise ValueError("invalid VLESS entry")
    if p.port is None or not 1 <= p.port <= 65535:
        raise ValueError("invalid VLESS port")
    if q.get("security", "").lower() == "reality":
        for key in ("pbk", "sni", "sid", "fp"):
            if not q.get(key):
                raise ValueError(f"incomplete REALITY entry: missing {key}")
        if q.get("type", "tcp").lower() == "tcp" and not q.get("flow"):
            raise ValueError("TCP REALITY entry has no flow")


def metadata(uri, name, fam, ms, role="server", alias_of=None):
    p, q = parts(uri)
    row = {
        "name": name,
        "family": fam,
        "role": role,
        "host": p.hostname,
        "port": p.port,
        "transport": q.get("type", "tcp"),
        "sni": q.get("sni"),
        "observed_ms": ms,
        "health_source": "client-2026-10-09",
    }
    if alias_of:
        row["alias_of"] = alias_of
    return row


def main():
    links = decode_sub()

    # Preserve every parameter of the provider's original URI. Only exact technical
    # duplicates are removed before applying the client-confirmed reachability set.
    uniq, seen = [], set()
    for link in links:
        key = canonical(link)
        if key not in seen:
            seen.add(key)
            uniq.append(link)

    source_groups = defaultdict(list)
    for link in uniq:
        source_groups[family(link)].append(link)

    healthy_groups = defaultdict(list)
    rejected = []
    for fam, vals in source_groups.items():
        for uri in vals:
            ms = observed_ms(fam, uri)
            if ms is None:
                rejected.append({
                    "family": fam,
                    "host": urllib.parse.urlsplit(uri).hostname,
                    "reason": "n/a-or-not-client-confirmed",
                })
                continue
            validate_uri(uri)
            healthy_groups[fam].append((ms, uri))

    for vals in healthy_groups.values():
        vals.sort(key=lambda item: (item[0], health_key(item[1])))

    technical_count = sum(len(v) for v in healthy_groups.values())
    if technical_count < MIN_HEALTHY_TECHNICAL:
        raise SystemExit(
            f"Only {technical_count} client-confirmed technical profiles remain; "
            f"refusing to overwrite last-known-good subscription"
        )

    output = []
    selected = []

    # Collapse all provider AUTO variants into exactly ONE profile. The chosen AUTO
    # is the only provider-AUTO endpoint that returned a real latency in the user's
    # client snapshot (681 ms); all four n/a AUTO variants are removed.
    if healthy_groups.get("auto"):
        ms, uri = healthy_groups["auto"][0]
        name = "⚡ Самый быстрый АВТО"
        output.append(rename(uri, name))
        selected.append(metadata(uri, name, "auto", ms, role="provider-auto"))

    # LTE AUTO is one convenience alias pointing to the fastest client-confirmed
    # LTE profile. The real numbered LTE profiles are still kept below it.
    if healthy_groups.get("lte"):
        ms, uri = healthy_groups["lte"][0]
        alias = "📶 LTE АВТО"
        output.append(rename(uri, alias))
        selected.append(metadata(uri, alias, "lte", ms, role="lte-auto", alias_of="📶 LTE — 1"))

    ordered = [fam for fam in PRIORITY if fam in healthy_groups and fam != "auto"]
    ordered += sorted(fam for fam in healthy_groups if fam not in PRIORITY and fam != "auto")

    for fam in ordered:
        vals = healthy_groups[fam]
        base = base_display(fam, original_name(vals[0][1]))
        for idx, (ms, uri) in enumerate(vals, start=1):
            name = f"{base} — {idx}"
            output.append(rename(uri, name))
            selected.append(metadata(uri, name, fam, ms))

    names = [urllib.parse.unquote(urllib.parse.urlsplit(x).fragment or "") for x in output]
    if len(names) != len(set(names)):
        raise SystemExit("Duplicate display names remain after cleanup")

    payload = ("\n".join(output) + "\n").encode("utf-8")
    SUB.write_text(base64.b64encode(payload).decode("ascii") + "\n", encoding="ascii")

    family_counts = {fam: len(healthy_groups[fam]) for fam in ordered}
    family_counts["auto"] = len(healthy_groups.get("auto", []))
    status = {
        "input_count": len(links),
        "technical_unique": len(uniq),
        "client_confirmed_technical": technical_count,
        "removed_unreachable_or_unconfirmed": len(rejected),
        "published_count": len(output),
        "families": len(healthy_groups),
        "family_counts": family_counts,
        "auto_target": next((x for x in selected if x.get("role") == "provider-auto"), None),
        "lte_auto_target": next((x for x in selected if x.get("role") == "lte-auto"), None),
        "selected": selected,
        "curated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if SYNC_STATUS.exists():
        sync = json.loads(SYNC_STATUS.read_text(encoding="utf-8"))
        sync["published_count"] = len(output)
        sync["client_confirmed_technical"] = technical_count
        sync["removed_unreachable_or_unconfirmed"] = len(rejected)
        sync["distinct_families"] = len(healthy_groups)
        sync["health_source"] = "client-2026-10-09"
        sync["curated_at_utc"] = status["curated_at_utc"]
        SYNC_STATUS.write_text(json.dumps(sync, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        f"HEALTH_FILTER input={len(links)} unique={len(uniq)} "
        f"healthy_technical={technical_count} removed={len(rejected)} published={len(output)}"
    )
    for row in selected:
        if row["role"] in ("provider-auto", "lte-auto"):
            print(f"{row['role'].upper()}={row['host']}:{row['port']} {row['observed_ms']}ms")


if __name__ == "__main__":
    main()
