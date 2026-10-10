import os
import uuid

from fastapi import UploadFile
from supabase import create_client, Client

BUCKET_NAME = "album-covers"

_client: Client | None = None


def get_supabase() -> Client:
    """Create the Supabase client lazily so the app can import without credentials."""
    global _client
    if _client is None:
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_KEY", "")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set")
        _client = create_client(url, key)
    return _client


def upload_file_to_supabase(file: UploadFile) -> str:
    """Uploads file buffer to Supabase Storage and returns the public CDN URL."""
    client = get_supabase()

    filename = file.filename or ""
    file_extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    unique_filename = f"{uuid.uuid4()}.{file_extension}"

    file_bytes = file.file.read()

    client.storage.from_(BUCKET_NAME).upload(
        path=unique_filename,
        file=file_bytes,
        file_options={"content-type": file.content_type or "image/jpeg"},
    )

    return client.storage.from_(BUCKET_NAME).get_public_url(unique_filename)