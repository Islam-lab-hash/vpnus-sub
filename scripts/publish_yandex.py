import os
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
SUB = ROOT / "sub.txt"


def main():
    bucket = os.environ["YC_BUCKET"].strip()
    key = os.environ.get("YC_OBJECT_KEY", "s").strip() or "s"
    if not bucket:
        raise SystemExit("YC_BUCKET is empty")

    body = SUB.read_bytes()
    if not body:
        raise SystemExit("sub.txt is empty")

    session = boto3.session.Session()
    s3 = session.client(
        service_name="s3",
        endpoint_url="https://storage.yandexcloud.net",
        region_name="ru-central1",
    )
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ACL="public-read",
        ContentType="text/plain; charset=utf-8",
        CacheControl="no-cache, no-store, must-revalidate",
    )
    print(f"YANDEX_MIRROR_OK=https://{bucket}.storage.yandexcloud.net/{key}")


if __name__ == "__main__":
    main()
