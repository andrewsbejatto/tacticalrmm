"""S3 (or compatible) access with boto3, imported only when needed."""

import uuid
from datetime import datetime

from .models import RecordingConfig


class StorageError(Exception):
    pass


def client(cfg: RecordingConfig):
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise StorageError("boto3 is not installed: /rmm/api/env/bin/pip install boto3")
    if not cfg.s3_configured:
        raise StorageError("S3 bucket and credentials are not configured")
    return boto3.client(
        "s3",
        endpoint_url=_endpoint(cfg.s3_endpoint),
        region_name=cfg.s3_region or None,
        aws_access_key_id=cfg.s3_access_key,
        aws_secret_access_key=cfg.s3_secret,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path" if cfg.s3_path_style else "auto"},
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


def _endpoint(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    if not value.lower().startswith(("https://", "http://")):
        value = "https://" + value
    return value


def _safe(part: str) -> str:
    keep = "".join(c if c.isalnum() or c in "-_." else "_" for c in (part or ""))
    return keep.strip("._")[:80] or "_"


def object_key(
    cfg: RecordingConfig, started: datetime, parts: list[str], filename: str
) -> str:
    prefix = cfg.s3_prefix.strip("/")
    items = [prefix] if prefix else []
    items += [f"{started:%Y}", f"{started:%m}"]
    items += [_safe(p) for p in parts]
    items.append(_safe(filename))
    return "/".join(items)


def upload(cfg: RecordingConfig, path: str, key: str, content_type: str) -> None:
    from boto3.s3.transfer import TransferConfig

    extra = {"ContentType": content_type}
    if cfg.s3_sse:
        extra["ServerSideEncryption"] = "AES256"
    s3 = client(cfg)
    try:
        s3.upload_file(
            path,
            cfg.s3_bucket,
            key,
            ExtraArgs=extra,
            Config=TransferConfig(multipart_chunksize=64 * 1024 * 1024),
        )
    except Exception as e:  # botocore errors
        raise StorageError(str(e))


def object_size(cfg: RecordingConfig, key: str) -> int:
    try:
        return int(
            client(cfg).head_object(Bucket=cfg.s3_bucket, Key=key)["ContentLength"]
        )
    except StorageError:
        raise
    except Exception as e:
        raise StorageError(str(e))


def presigned_url(cfg: RecordingConfig, key: str, filename: str) -> str:
    try:
        return client(cfg).generate_presigned_url(
            "get_object",
            Params={
                "Bucket": cfg.s3_bucket,
                "Key": key,
                "ResponseContentDisposition": f'inline; filename="{_safe(filename)}"',
            },
            ExpiresIn=max(1, cfg.presign_minutes) * 60,
        )
    except StorageError:
        raise
    except Exception as e:
        raise StorageError(str(e))


def test(cfg: RecordingConfig, cors_origins: list[str]) -> dict:
    """Writes, reads and deletes a small object; applies the CORS rule."""
    s3 = client(cfg)
    prefix = cfg.s3_prefix.strip("/")
    key = (prefix + "/" if prefix else "") + f".trmm-test-{uuid.uuid4().hex}"
    extra = {"ServerSideEncryption": "AES256"} if cfg.s3_sse else {}
    try:
        s3.put_object(Bucket=cfg.s3_bucket, Key=key, Body=b"trmm", **extra)
        s3.head_object(Bucket=cfg.s3_bucket, Key=key)
        s3.delete_object(Bucket=cfg.s3_bucket, Key=key)
    except Exception as e:
        raise StorageError(str(e))

    # the MeshCentral player of the dashboard downloads the .mcrec with
    # fetch(): the bucket must allow GET from the dashboard origin
    cors = "skipped"
    if cors_origins:
        try:
            s3.put_bucket_cors(
                Bucket=cfg.s3_bucket,
                CORSConfiguration={
                    "CORSRules": [
                        {
                            "AllowedMethods": ["GET", "HEAD"],
                            "AllowedOrigins": cors_origins,
                            "AllowedHeaders": ["*"],
                            "ExposeHeaders": ["Content-Length", "Content-Range"],
                            "MaxAgeSeconds": 3600,
                        }
                    ]
                },
            )
            cors = "ok"
        except Exception as e:
            cors = f"not applied ({e}); allow GET from {', '.join(cors_origins)} in the bucket CORS"
    return {"write": "ok", "cors": cors}
