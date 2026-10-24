"""One-time migration of Vinyl Vault to new services.

Copies every album row from the OLD database (Supabase Postgres) to the NEW database (for example Neon),
and every cover image from the OLD public storage (Supabase Storage) to the NEW bucket (for example
Cloudflare R2). Row ids, titles, track counts, genres, explicit flags and colours are kept exactly;
only `cover_image_url` is rewritten to point at the new storage. File names are kept too.

It only READS from the old services, so it is safe to run more than once: existing rows are updated
and files are overwritten with identical content.

Set these in your .env (or the terminal) before running:

    OLD_DATABASE_URL     connection string of the old database (Supabase pooler string)
    NEW_DATABASE_URL     connection string of the new database (Neon)
    STORAGE_BACKEND=s3
    S3_ENDPOINT_URL, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY, S3_BUCKET, S3_PUBLIC_BASE_URL   (new bucket)

Usage:
    python migrate.py --dry-run     # read the old data and check the old files download; change nothing
    python migrate.py               # do the migration, then verify it
"""
import argparse
import hashlib
import mimetypes
import os
import sys
import urllib.parse
import urllib.request

from dotenv import load_dotenv

load_dotenv()

# database.py builds its own engine from DATABASE_URL when imported. This script uses its own two
# engines (OLD_ and NEW_), so make sure that unused one can never fail the import.
os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy import create_engine, insert, select, text, update

import database
import storage


def download(url: str):
    """Return (bytes, content_type) for a public file."""
    req = urllib.request.Request(url, headers={"User-Agent": "vinyl-vault-migration"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read(), resp.headers.get_content_type()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="read and check only; change nothing")
    args = parser.parse_args()

    old_url = os.getenv("OLD_DATABASE_URL", "").strip()
    new_url = os.getenv("NEW_DATABASE_URL", "").strip()
    if not old_url or not new_url:
        print("Set OLD_DATABASE_URL and NEW_DATABASE_URL first.")
        return 1
    if database.normalize_database_url(old_url) == database.normalize_database_url(new_url):
        print("OLD_DATABASE_URL and NEW_DATABASE_URL are the same database. Refusing to continue.")
        return 1
    if storage.storage_backend() != "s3":
        print("Set STORAGE_BACKEND=s3 (and the S3_* variables) so files are copied to the NEW storage.")
        return 1

    old_engine = create_engine(database.normalize_database_url(old_url), pool_pre_ping=True)
    new_engine = create_engine(database.normalize_database_url(new_url), pool_pre_ping=True)
    table = database.AlbumModel.__table__

    # 1. Read everything from the old database
    with old_engine.connect() as conn:
        rows = [dict(r._mapping) for r in conn.execute(select(table).order_by(table.c.id))]
    print(f"Old database: {len(rows)} album(s)")
    if not rows:
        print("Nothing to migrate.")
        return 0

    if not args.dry_run:
        database.Base.metadata.create_all(bind=new_engine)   # creates the albums table in the NEW database
        print("New database: albums table ready")

    # 2. Copy each album and its cover
    hashes = {}
    failures = 0
    for row in rows:
        label = f"id {row['id']} ({row['title']})"
        old_cover = row.get("cover_image_url")
        new_row = dict(row)
        try:
            if old_cover:
                data, content_type = download(old_cover)
                key = os.path.basename(urllib.parse.urlparse(old_cover).path)
                content_type = content_type if content_type.startswith("image/") else (
                    mimetypes.guess_type(key)[0] or "image/jpeg"
                )
                hashes[row["id"]] = sha(data)
                if args.dry_run:
                    print(f"  {label}: cover downloads OK ({len(data)} bytes) -> would upload as {key}")
                    continue
                new_row["cover_image_url"] = storage.upload_bytes(data, key, content_type)
            elif args.dry_run:
                print(f"  {label}: no cover image")
                continue

            with new_engine.begin() as conn:
                exists = conn.execute(select(table.c.id).where(table.c.id == row["id"])).first()
                if exists:
                    conn.execute(update(table).where(table.c.id == row["id"]).values(**new_row))
                else:
                    conn.execute(insert(table).values(**new_row))
            print(f"  {label}: copied -> {new_row['cover_image_url']}")
        except Exception as err:
            failures += 1
            print(f"  {label}: FAILED: {err}")

    if args.dry_run:
        print("\nDry run finished. Nothing was changed.")
        return 1 if failures else 0

    # 3. Make new ids continue after the highest copied id (Postgres only)
    if new_engine.dialect.name == "postgresql":
        with new_engine.begin() as conn:
            conn.execute(text(
                "SELECT setval(pg_get_serial_sequence('albums', 'id'), (SELECT COALESCE(MAX(id), 1) FROM albums))"
            ))
        print("New database: id counter reset")

    # 4. Verify: same rows, same data, same image bytes
    print("\nVerifying...")
    with new_engine.connect() as conn:
        copied = {r.id: dict(r._mapping) for r in conn.execute(select(table))}
    problems = 0
    for row in rows:
        label = f"id {row['id']} ({row['title']})"
        new = copied.get(row["id"])
        if new is None:
            print(f"  {label}: MISSING in new database")
            problems += 1
            continue
        changed = [k for k in row if k != "cover_image_url" and row[k] != new[k]]
        if changed:
            print(f"  {label}: fields differ: {', '.join(changed)}")
            problems += 1
            continue
        if row.get("cover_image_url"):
            try:
                new_data, _ = download(new["cover_image_url"])
                if sha(new_data) != hashes.get(row["id"]):
                    print(f"  {label}: image content differs")
                    problems += 1
                    continue
            except Exception as err:
                print(f"  {label}: new image does not load: {err}")
                if any(code in str(err) for code in ("401", "403", "404")):
                    print("      Hint: the file was uploaded, but its public address doesn't work. Check that the bucket's "
                          "public access is turned on and that S3_PUBLIC_BASE_URL is exactly the bucket's public address.")
                problems += 1
                continue
        print(f"  {label}: identical (image {hashes.get(row['id'], '-')})")

    extra = set(copied) - {r["id"] for r in rows}
    if extra:
        print(f"  note: new database also has ids {sorted(extra)} that are not in the old one")

    problems += failures
    print(f"\nRESULT: {len(rows)} album(s) in old database, {len(copied)} in new database, "
          f"{'all identical' if problems == 0 else f'{problems} problem(s)'}")
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    sys.exit(main())