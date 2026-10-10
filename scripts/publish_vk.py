import os
from pathlib import Path

import boto3
from botocore.config import Config

ROOT = Path(__file__).resolve().parents[1]
SUB = ROOT / "sub.txt"
DEFAULT_ENDPOINT = "https://hb.ru-msk.vkcs.cloud"
DEFAULT_REGION = "ru-msk"
DEFAULT_OBJECT_KEY = "assets/cache/v3/6f29a1c4.dat"


def main():
    bucket = os.environ["VK_S3_BUCKET"].strip()
    endpoint = os.environ.get("VK_S3_ENDPOINT", DEFAULT_ENDPOINT).strip() or DEFAULT_ENDPOINT
    region = os.environ.get("VK_S3_REGION", DEFAULT_REGION).strip() or DEFAULT_REGION
    key = os.environ.get("VK_OBJECT_KEY", DEFAULT_OBJECT_KEY).strip() or DEFAULT_OBJECT_KEY
    if not bucket:
        raise SystemExit("VK_S3_BUCKET is empty")

    body = SUB.read_bytes()
    if not body:
        raise SystemExit("sub.txt is empty")

    session = boto3.session.Session()
    s3 = session.client(
        service_name="s3",
        endpoint_url=endpoint,
        region_name=region,
        config=Config(s3={"addressing_style": "path"}),
    )
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ACL="public-read",
        ContentType="application/octet-stream",
        CacheControl="no-cache, no-store, must-revalidate",
    )

    public_url = f"{endpoint.rstrip('/')}/{bucket}/{key}"
    print(f"VK_MIRROR_OK={public_url}")


if __name__ == "__main__":
    main()
