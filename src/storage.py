"""Where the PDFs live: a local folder (STORAGE_BACKEND=local) or an S3 bucket (STORAGE_BACKEND=s3).

Book paths stored in PostgreSQL / Elasticsearch are `./data/pdfs/name.pdf` style paths for local storage and
`s3://bucket/prefix/name.pdf` URIs for S3. Several API copies can share local storage when PDF_DIR and UPLOAD_DIR
are on a shared mount (NFS, EFS); S3 needs nothing shared on disk.

Uploads are first staged (UPLOAD_DIR, or the S3_UPLOAD_PREFIX), so the ingestion worker, possibly on another
machine, can pick them up; `promote` then gives them their final, unique name.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Iterator, List, Optional, Set

from config import settings

PDF_URL_PREFIX = "/data/pdfs/"
SHA256_METADATA_KEY = "sha256"


@dataclass(frozen=True)
class StoredFile:
    path: str
    # S3 ETag: lets the poller skip unchanged objects without downloading them. None for local files.
    version: Optional[str] = None


def _hash_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _unique_name(name: str, taken) -> str:
    """`name`, or `name (2).pdf`, `name (3).pdf`, ... when `taken(candidate)` says it exists."""
    stem, ext = os.path.splitext(name)
    candidate, counter = name, 2
    while taken(candidate):
        candidate = f"{stem} ({counter}){ext}"
        counter += 1
    return candidate


class LocalStorage:
    kind = "local"

    @property
    def root(self) -> str:
        return settings.pdf_dir

    def path_for(self, file_name: str) -> str:
        return os.path.join(settings.pdf_dir, file_name)

    def list_pdfs(self) -> List[StoredFile]:
        if not os.path.isdir(settings.pdf_dir):
            return []
        names = sorted(name for name in os.listdir(settings.pdf_dir) if name.lower().endswith(".pdf"))
        return [StoredFile(self.path_for(name)) for name in names]

    def exists(self, path: str) -> bool:
        return os.path.isfile(path)

    def exists_many(self, paths: Iterable[str]) -> Set[str]:
        return {path for path in paths if os.path.isfile(path)}

    def file_hash(self, path: str) -> str:
        return _hash_file(path)

    def version(self, path: str) -> Optional[str]:  # pylint: disable=unused-argument
        return None

    @contextmanager
    def local_copy(self, path: str) -> Iterator[str]:
        yield path

    def new_staging_file(self) -> str:
        os.makedirs(settings.upload_dir, exist_ok=True)
        return os.path.join(settings.upload_dir, f".upload-{os.urandom(8).hex()}.pdf")

    def stage(self, local_file: str, file_hash: str) -> str:  # pylint: disable=unused-argument
        """The staging file already is in UPLOAD_DIR; the staged reference is its path."""
        return local_file

    def promote(self, staged: str, file_name: str, file_hash: str) -> str:  # pylint: disable=unused-argument
        os.makedirs(settings.pdf_dir, exist_ok=True)
        target = self.path_for(_unique_name(file_name, lambda name: os.path.exists(self.path_for(name))))
        shutil.move(staged, target)
        return target

    def discard(self, staged: str) -> None:
        if staged and os.path.exists(staged):
            os.remove(staged)

    def staged_exists(self, staged: str) -> bool:
        return bool(staged) and os.path.exists(staged)


class S3Storage:
    kind = "s3"

    def __init__(self) -> None:
        if not settings.s3_bucket:
            raise RuntimeError("STORAGE_BACKEND=s3 needs S3_BUCKET")
        self.bucket = settings.s3_bucket
        self.prefix = settings.s3_prefix.strip("/") + "/" if settings.s3_prefix.strip("/") else ""
        self.upload_prefix = settings.s3_upload_prefix.strip("/") + "/"
        self.client = _s3_client(settings.s3_region, settings.s3_endpoint_url)

    @property
    def root(self) -> str:
        return f"s3://{self.bucket}/{self.prefix}"

    def path_for(self, file_name: str) -> str:
        return f"s3://{self.bucket}/{self.prefix}{file_name}"

    def key(self, path: str) -> str:
        prefix = f"s3://{self.bucket}/"
        if not path.startswith(prefix):
            raise ValueError(f"{path} is not in bucket {self.bucket}")
        return path[len(prefix):]

    def _missing(self, error) -> bool:
        return error.response.get("Error", {}).get("Code") in {"404", "NoSuchKey", "NotFound"}

    def list_pdfs(self) -> List[StoredFile]:
        files = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
            for item in page.get("Contents", []):
                key = item["Key"]
                if not key.lower().endswith(".pdf") or key.startswith(self.upload_prefix):
                    continue
                files.append(StoredFile(f"s3://{self.bucket}/{key}", item.get("ETag")))
        return sorted(files, key=lambda stored: stored.path)

    def exists(self, path: str) -> bool:
        from botocore.exceptions import ClientError  # pylint: disable=import-outside-toplevel

        try:
            self.client.head_object(Bucket=self.bucket, Key=self.key(path))
            return True
        except ClientError as error:
            if self._missing(error):
                return False
            raise

    def exists_many(self, paths: Iterable[str]) -> Set[str]:
        """One listing instead of a request per book."""
        wanted = set(paths)
        return {stored.path for stored in self.list_pdfs() if stored.path in wanted} if wanted else set()

    def version(self, path: str) -> Optional[str]:
        return self.client.head_object(Bucket=self.bucket, Key=self.key(path)).get("ETag")

    def file_hash(self, path: str) -> str:
        """SHA-256 from the object's metadata (set on upload), else by downloading it once."""
        head = self.client.head_object(Bucket=self.bucket, Key=self.key(path))
        known = (head.get("Metadata") or {}).get(SHA256_METADATA_KEY)
        if known:
            return known
        with self.local_copy(path) as local:
            return _hash_file(local)

    @contextmanager
    def local_copy(self, path: str) -> Iterator[str]:
        """Download to a temporary file for PDF extraction / OCR, removed afterwards."""
        handle, local = tempfile.mkstemp(suffix=".pdf", prefix="osiss-")
        os.close(handle)
        try:
            self.client.download_file(self.bucket, self.key(path), local)
            yield local
        finally:
            os.remove(local)

    def new_staging_file(self) -> str:
        handle, local = tempfile.mkstemp(suffix=".pdf", prefix="osiss-upload-")
        os.close(handle)
        return local

    def stage(self, local_file: str, file_hash: str) -> str:
        """Upload the received file to the staging prefix (any worker can reach it) and drop the local copy."""
        key = f"{self.upload_prefix}{os.urandom(8).hex()}.pdf"
        try:
            self.client.upload_file(
                local_file, self.bucket, key,
                ExtraArgs={"ContentType": "application/pdf", "Metadata": {SHA256_METADATA_KEY: file_hash}},
            )
        finally:
            os.remove(local_file)
        return f"s3://{self.bucket}/{key}"

    def promote(self, staged: str, file_name: str, file_hash: str) -> str:
        target = self.path_for(_unique_name(file_name, lambda name: self.exists(self.path_for(name))))
        self.client.copy_object(
            Bucket=self.bucket, Key=self.key(target), CopySource={"Bucket": self.bucket, "Key": self.key(staged)},
            MetadataDirective="REPLACE", ContentType="application/pdf", Metadata={SHA256_METADATA_KEY: file_hash},
        )
        self.discard(staged)
        return target

    def discard(self, staged: str) -> None:
        if staged:
            self.client.delete_object(Bucket=self.bucket, Key=self.key(staged))

    def staged_exists(self, staged: str) -> bool:
        return bool(staged) and self.exists(staged)

    def signed_url(self, path: str) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": self.key(path), "ResponseContentType": "application/pdf",
                    "ResponseContentDisposition": "inline"},
            ExpiresIn=settings.s3_url_expiry_seconds,
        )

    def stream(self, path: str):
        """(iterator over the object's bytes, size)."""
        response = self.client.get_object(Bucket=self.bucket, Key=self.key(path))
        return response["Body"].iter_chunks(1 << 16), response.get("ContentLength")


@lru_cache(maxsize=4)
def _s3_client(region: str, endpoint_url: str):
    import boto3  # pylint: disable=import-outside-toplevel

    return boto3.client("s3", region_name=region or None, endpoint_url=endpoint_url or None)


def get_storage():
    """The storage backend for the current settings."""
    return S3Storage() if settings.storage_backend == "s3" else LocalStorage()


def is_s3_path(path: str) -> bool:
    return path.startswith("s3://")


def pdf_url_path(file_path: str) -> str:
    """URL path a stored PDF is served at by this API (`/data/pdfs/...`), for both backends."""
    if is_s3_path(file_path):
        bucket_and_key = file_path[len("s3://"):]
        key = bucket_and_key.split("/", 1)[1] if "/" in bucket_and_key else ""
        prefix = settings.s3_prefix.strip("/") + "/" if settings.s3_prefix.strip("/") else ""
        relative = key[len(prefix):] if key.startswith(prefix) else key
        return PDF_URL_PREFIX + relative
    normalized = file_path[2:] if file_path.startswith("./") else file_path
    if normalized and not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized
