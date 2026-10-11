import os
import uuid

from fastapi import UploadFile
from supabase import create_client, Client

BUCKET_NAME = "album-covers"

_client: Client | None = None


def _clean(value: str) -> str:
    """Remove stray spaces, line breaks and quotes that sneak in when pasting env vars."""
    return "".join(value.split()).strip("\"'")


def get_supabase() -> Client:
    """Create the Supabase client lazily so the app can import without credentials."""
    global _client
    if _client is None:
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
        # Safe diagnostic for the Render logs: shows only the key's first 7 characters and its shape, never the key itself.
        try:
            from importlib.metadata import version
            lib = version("supabase")
        except Exception:
            lib = "unknown"
        print(
            f"[storage] supabase-py {lib}; key starts '{key[:7]}', length {len(key)}, dots {key.count('.')}",
            flush=True,
        )
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