import base64
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = root / "sources.txt"
output = root / "sub.txt"

lines = [line.strip() for line in source.read_text(encoding="utf-8").splitlines()]
unique = list(dict.fromkeys(line for line in lines if line.startswith("vless://")))
if not unique:
    print("No VLESS links found; keeping previous subscription")
else:
    payload = ("\n".join(unique) + "\n").encode("utf-8")
    output.write_text(base64.b64encode(payload).decode("ascii") + "\n", encoding="ascii")
    print(f"Generated {len(unique)} entries")
