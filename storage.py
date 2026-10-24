"""File storage for album covers.

Two interchangeable backends, chosen with the STORAGE_BACKEND environment variable:

  supabase (default)  Supabase Storage. Needs SUPABASE_URL and SUPABASE_KEY.
  s3                  Any S3-compatible bucket (Cloudflare R2, Backblaze B2, AWS S3, MinIO...).
                      Needs S3_ENDPOINT_URL, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY, S3_BUCKET
                      and S3_PUBLIC_BASE_URL (the public address the bucket's files are served from).
                      Optional: S3_REGION (default "auto", which is what Cloudflare R2 expects).

Both return the public URL of the stored file, which is what the database keeps.
"""
import os
import uuid

from fastapi import UploadFile

BUCKET_NAME = "album-covers"   # Supabase bucket name (the S3 backend uses S3_BUCKET instead)

_supabase_client = None
_s3 = None


def _clean(value: str) -> str:
    """Remove stray spaces, line breaks and quotes that sneak in when pasting env vars."""
    return "".join(value.split()).strip("\"'")


def storage_backend() -> str:
    return _clean(os.getenv("STORAGE_BACKEND", "supabase")).lower()


# ---------------------------------------------------------------- Supabase

def get_supabase():
    """Create the Supabase client lazily so the app can import without credentials."""
    global _supabase_client
    if _supabase_client is None:
        from supabase import create_client   # imported here so the s3 backend doesn't need the package

        url = _clean(os.getenv("SUPABASE_URL", ""))
        key = _clean(os.getenv("SUPABASE_KEY", ""))
        if not url or not key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set")
        # Old-style keys are JWTs: "eyJ..." with exactly two dots. A different dot count means it was cut off or mixed up.
        if key.startswith("eyJ") and key.count(".") != 2:
            raise RuntimeError(
                "SUPABASE_KEY looks cut off. Copy the whole key again, on one line, with no quotes."
            )
        if not (key.startswith("eyJ") or key.startswith("sb_")):
            raise RuntimeError(
                "SUPABASE_KEY is not a Supabase key. Use the secret key (starts with sb_secret_) "
                "or the legacy service_role key (starts with eyJ)."
            )
        # Safe diagnostic for the logs: only the key's first 7 characters and its shape, never the key itself.
        try:
            from importlib.metadata import version
            lib = version("supabase")
        except Exception:
            lib = "unknown"
        print(
            f"[storage] backend=supabase supabase-py {lib}; key starts '{key[:7]}', length {len(key)}, dots {key.count('.')}",
            flush=True,
        )
        _supabase_client = create_client(url, key)
    return _supabase_client


def _upload_supabase(data: bytes, key: str, content_type: str) -> str:
    client = get_supabase()
    client.storage.from_(BUCKET_NAME).upload(
        path=key, file=data, file_options={"content-type": content_type}
    )
    return client.storage.from_(BUCKET_NAME).get_public_url(key)


# ---------------------------------------------------------------- S3-compatible (Cloudflare R2, ...)

def get_s3():
    """Return (boto3 client, bucket name, public base URL), created lazily on first use."""
    global _s3
    if _s3 is None:
        import boto3
        from botocore.config import Config

        endpoint = _clean(os.getenv("S3_ENDPOINT_URL", ""))
        key_id = _clean(os.getenv("S3_ACCESS_KEY_ID", ""))
        secret = _clean(os.getenv("S3_SECRET_ACCESS_KEY", ""))
        bucket = _clean(os.getenv("S3_BUCKET", ""))
        public_base = _clean(os.getenv("S3_PUBLIC_BASE_URL", "")).rstrip("/")
        if not (endpoint and key_id and secret and bucket and public_base):
            raise RuntimeError(
                "S3 storage needs S3_ENDPOINT_URL, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY, "
                "S3_BUCKET and S3_PUBLIC_BASE_URL to be set"
            )
        if not public_base.startswith("http"):
            raise RuntimeError("S3_PUBLIC_BASE_URL must start with https:// (for example https://pub-xxxx.r2.dev)")

        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=key_id,
            aws_secret_access_key=secret,
            region_name=_clean(os.getenv("S3_REGION", "auto")) or "auto",
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        print(f"[storage] backend=s3 endpoint={endpoint} bucket={bucket} public={public_base}", flush=True)
        _s3 = (client, bucket, public_base)
    return _s3


def _upload_s3(data: bytes, key: str, content_type: str) -> str:
    client, bucket, public_base = get_s3()
    client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
    return f"{public_base}/{key}"


# ---------------------------------------------------------------- public API used by the app and migrate.py

def upload_bytes(data: bytes, key: str, content_type: str) -> str:
    """Store `data` under the file name `key` and return its public URL."""
    backend = storage_backend()
    if backend == "s3":
        return _upload_s3(data, key, content_type)
    if backend == "supabase":
        return _upload_supabase(data, key, content_type)
    raise RuntimeError(f"Unknown STORAGE_BACKEND '{backend}'. Use 'supabase' or 's3'.")


def upload_cover(file: UploadFile) -> str:
    """Upload an album cover with a unique file name and return its public URL."""
    filename = file.filename or ""
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    unique_filename = f"{uuid.uuid4()}.{extension}"
    return upload_bytes(file.file.read(), unique_filename, file.content_type or "image/jpeg")