import asyncio
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import boto3

from app.config import settings


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    bucket = parsed.netloc
    prefix = parsed.path.strip("/")
    return bucket, prefix


def _upload_text(bucket: str, key: str, text: str) -> None:
    kwargs = {}
    if settings.aws_access_key_id:
        kwargs["aws_access_key_id"] = settings.aws_access_key_id
    if settings.aws_secret_access_key:
        kwargs["aws_secret_access_key"] = settings.aws_secret_access_key

    client = boto3.client("s3", region_name=settings.s3_region, **kwargs)
    client.put_object(Bucket=bucket, Key=key, Body=text.encode("utf-8"))


async def upload_text(text: str, source_url: str) -> tuple[str, str]:
    bucket, prefix = _parse_s3_uri(settings.s3_uri)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_name = re.sub(r"[^a-zA-Z0-9]", "_", source_url).strip("_")[:80]
    key = f"{prefix}/{safe_name}_{timestamp}.txt"

    await asyncio.to_thread(_upload_text, bucket, key, text)
    return bucket, key
