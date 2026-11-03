"""Build one Happ JSON subscription containing real auto-balancing and country profiles."""
import base64
import json
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUTO = ROOT / "Автоматический поиск"
SOURCE = ROOT / "sub.txt"
OUTPUT = ROOT / "happ.json"


def decode_links(data):
    raw = data.strip()
    if "://" not in raw:
        raw = base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8-sig")
    return [line.strip() for line in raw.splitlines() if line.strip().startswith(("vless://", "hy2://", "hysteria2://"))]


def profile_from_link(link):
    p = urllib.parse.urlsplit(link)
    q = dict(urllib.parse.parse_qsl(p.query))
    name = urllib.parse.unquote(p.fragment) or p.hostname
    host = p.hostname
    port = p.port
    if not host or not port:
        raise ValueError("Invalid endpoint")
    if p.scheme == "vless":
        outbound = {
            "tag": "proxy", "protocol": "vless",
            "settings": {"vnext": [{"address": host, "port": port, "users": [{
                "id": urllib.parse.unquote(p.username or ""),
                "encryption": q.get("encryption", "none"),
                **({"flow": q["flow"]} if q.get("flow") else {}),
            }]}]},
            "streamSettings": {"network": q.get("type", "tcp"), "security": q.get("security", "none")},
        }
        stream = outbound["streamSettings"]
        if stream["security"] == "tls":
            stream["tlsSettings"] = {
                "serverName": q.get("sni", host), "fingerprint": q.get("fp", "chrome"),
                **({"alpn": q["alpn"].split(",")} if q.get("alpn") else {}),
            }
        elif stream["security"] == "reality":
            stream["realitySettings"] = {
                "serverName": q.get("sni", ""), "fingerprint": q.get("fp", "chrome"),
                "publicKey": q.get("pbk", ""), "shortId": q.get("sid", ""),
                "spiderX": q.get("spx", "/"),
            }
        net = stream["network"]
        if net == "ws":
            stream["wsSettings"] = {"path": q.get("path", "/"), "headers": {"Host": q.get("host", host)}}
        elif net == "grpc":
            stream["grpcSettings"] = {"serviceName": q.get("serviceName", "")}
        elif net == "httpupgrade":
            stream["httpupgradeSettings"] = {"path": q.get("path", "/"), "host": q.get("host", host)}
    else:
        outbound = {
            "tag": "proxy", "protocol": "hysteria",
            "settings": {"address": host, "port": port, "version": 2},
            "streamSettings": {
                "network": "hysteria", "security": "tls",
                "hysteriaSettings": {"version": 2, "auth": urllib.parse.unquote(p.username or "")},
                "tlsSettings": {"serverName": q.get("sni", host), "alpn": q.get("alpn", "h3").split(",")},
            },
        }
    return {
        "remarks": name, "log": {"loglevel": "warning"},
        "inbounds": [
            {"tag": "socks", "listen": "127.0.0.1", "port": 10808, "protocol": "socks", "settings": {"udp": True}},
            {"tag": "http", "listen": "127.0.0.1", "port": 10809, "protocol": "http"},
        ],
        "outbounds": [outbound, {"tag": "direct", "protocol": "freedom"}, {"tag": "block", "protocol": "blackhole"}],
        "routing": {"domainStrategy": "AsIs", "rules": [{"type": "field", "outboundTag": "proxy", "network": "tcp,udp"}]},
    }


def main():
    auto = json.loads(AUTO.read_text(encoding="utf-8-sig"))
    if not auto.get("burstObservatory") or not auto.get("routing", {}).get("balancers"):
        raise ValueError("Auto profile lacks a working Xray balancer")
    links = decode_links(SOURCE.read_text(encoding="utf-8-sig"))
    profiles = [auto]
    seen = set()
    for link in links:
        try:
            item = profile_from_link(link)
            name = item["remarks"]
            if name.startswith("🇪🇺 Автоматический поиск"):
                continue
            key = link.split("#", 1)[0]
            if key not in seen:
                profiles.append(item)
                seen.add(key)
        except (ValueError, KeyError) as exc:
            raise ValueError("Invalid link in sub.txt") from exc
    OUTPUT.write_text(json.dumps(profiles, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Built {OUTPUT.name}: {len(profiles)} profiles")


if __name__ == "__main__":
    main()
