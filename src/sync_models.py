"""Copy the local models/ folder to or from S3, so new machines (containers, autoscaled instances) can fetch them.

    python src/sync_models.py --upload s3://my-bucket/models/     # once, from a machine that ran download_models.py
    python src/sync_models.py s3://my-bucket/models/              # on each new machine (also MODELS_S3_URI)

Files already present with the same size are skipped. Uses the normal AWS credential chain (instance role, env).
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Tuple

from config import settings


def split_uri(uri: str) -> Tuple[str, str]:
    if not uri.startswith("s3://"):
        raise ValueError(f"expected s3://bucket/prefix/, got {uri}")
    bucket, _, prefix = uri[len("s3://"):].partition("/")
    return bucket, (prefix.rstrip("/") + "/") if prefix else ""


def client():
    import boto3  # pylint: disable=import-outside-toplevel

    return boto3.client("s3", region_name=settings.s3_region or None, endpoint_url=settings.s3_endpoint_url or None)


def download(uri: str, target: str) -> int:
    bucket, prefix = split_uri(uri)
    s3 = client()
    copied = 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            relative = item["Key"][len(prefix):]
            if not relative or relative.endswith("/"):
                continue
            local = os.path.join(target, relative)
            if os.path.isfile(local) and os.path.getsize(local) == item["Size"]:
                continue
            os.makedirs(os.path.dirname(local), exist_ok=True)
            s3.download_file(bucket, item["Key"], local)
            copied += 1
    return copied


def upload(source: str, uri: str) -> int:
    bucket, prefix = split_uri(uri)
    s3 = client()
    existing = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        existing.update({item["Key"]: item["Size"] for item in page.get("Contents", [])})
    copied = 0
    for root, _, files in os.walk(source):
        for name in files:
            local = os.path.join(root, name)
            key = prefix + os.path.relpath(local, source).replace(os.sep, "/")
            if existing.get(key) == os.path.getsize(local):
                continue
            s3.upload_file(local, bucket, key)
            copied += 1
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy models/ to or from S3")
    parser.add_argument("uri", nargs="?", default=os.getenv("MODELS_S3_URI", ""), help="s3://bucket/prefix/")
    parser.add_argument("--upload", action="store_true", help="copy local models/ to S3 instead")
    parser.add_argument("--models-dir", default=settings.models_dir)
    args = parser.parse_args()
    if not args.uri:
        parser.error("give an s3:// URI or set MODELS_S3_URI")
    if args.upload:
        print(f"[OSISS] Uploaded {upload(args.models_dir, args.uri)} model file(s) to {args.uri}")
    else:
        print(f"[OSISS] Downloaded {download(args.uri, args.models_dir)} model file(s) from {args.uri}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
