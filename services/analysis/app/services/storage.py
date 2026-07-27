"""Storage abstraction: local filesystem for dev, S3-compatible for production.
Original uploads are preserved verbatim under an originals/ prefix."""
from __future__ import annotations

import abc
import shutil
from pathlib import Path


class Storage(abc.ABC):
    @abc.abstractmethod
    def save(self, project_id: str, filename: str, data: bytes) -> str: ...

    @abc.abstractmethod
    def read(self, key: str) -> bytes: ...

    @abc.abstractmethod
    def list(self, project_id: str) -> list[str]: ...

    @abc.abstractmethod
    def delete_project(self, project_id: str) -> None: ...


class LocalStorage(Storage):
    def __init__(self, base_dir: str):
        self.base = Path(base_dir).resolve()
        self.base.mkdir(parents=True, exist_ok=True)

    def _project_dir(self, project_id: str) -> Path:
        # project_id is a server-generated UUID, not user input, but guard anyway
        safe = "".join(c for c in project_id if c.isalnum() or c in "-_")
        d = (self.base / "originals" / safe).resolve()
        try:
            d.relative_to(self.base)
        except ValueError:
            raise ValueError("invalid project id")
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save(self, project_id: str, filename: str, data: bytes) -> str:
        d = self._project_dir(project_id)
        target = (d / filename).resolve()
        try:
            target.relative_to(d)
        except ValueError:
            raise ValueError("invalid filename")
        target.write_bytes(data)
        return str(target.relative_to(self.base))

    def read(self, key: str) -> bytes:
        target = (self.base / key).resolve()
        try:
            target.relative_to(self.base)
        except ValueError:
            raise ValueError("invalid storage key")
        return target.read_bytes()

    def list(self, project_id: str) -> list[str]:
        d = self._project_dir(project_id)
        return [str(p.relative_to(self.base)) for p in d.iterdir() if p.is_file()]

    def delete_project(self, project_id: str) -> None:
        d = self._project_dir(project_id)
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)


class S3Storage(Storage):
    """S3-compatible storage (AWS S3, MinIO, R2). boto3 is imported lazily so the dev path
    never needs it. This is infrastructure only — it stores files, not AI outputs."""

    def __init__(self, bucket: str, endpoint_url: str = "", **credentials):
        try:
            import boto3  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "S3Storage requires boto3 (pip install 'kale-analysis[s3]'); "
                "use STORAGE_BACKEND=local for development"
            ) from exc
        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            aws_access_key_id=credentials.get("access_key_id") or None,
            aws_secret_access_key=credentials.get("secret_access_key") or None,
        )

    def _key(self, project_id: str, filename: str) -> str:
        return f"originals/{project_id}/{filename}"

    def save(self, project_id: str, filename: str, data: bytes) -> str:
        key = self._key(project_id, filename)
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)
        return key

    def read(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def list(self, project_id: str) -> list[str]:
        resp = self.client.list_objects_v2(Bucket=self.bucket, Prefix=f"originals/{project_id}/")
        return [obj["Key"] for obj in resp.get("Contents", [])]

    def delete_project(self, project_id: str) -> None:
        for key in self.list(project_id):
            self.client.delete_object(Bucket=self.bucket, Key=key)


def get_storage(settings) -> Storage:
    if settings.storage_backend == "s3":
        return S3Storage(
            bucket=settings.s3_bucket,
            endpoint_url=settings.s3_endpoint_url,
            access_key_id=settings.s3_access_key_id,
            secret_access_key=settings.s3_secret_access_key,
        )
    return LocalStorage(settings.storage_dir)
