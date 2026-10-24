"""Compare two deployments of Vinyl Vault to show the migration kept the data and behaviour the same.

Usage:
    python compare_deployments.py https://old-app.onrender.com https://new-app.koyeb.app

For every album it compares title, track count, explicit flag, genres and colour, and it downloads both
cover images to check the files are byte-for-byte identical. The cover URLs themselves are expected to
differ after the migration (they point at the new storage), so those are only reported.

Exit code 0 means identical; 1 means something differs or a deployment is unreachable.
Uses only the Python standard library. The first request to a sleeping free-tier app can take ~1 minute.
"""
import hashlib
import json
import sys
import urllib.request

FIELDS = ("title", "track_count", "is_explicit", "genres", "dominant_color")


def get(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "vinyl-vault-compare"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def digest(url) -> str:
    if not url:
        return "no cover"
    try:
        return hashlib.sha256(get(url, 60)).hexdigest()[:12]
    except Exception as err:
        return f"unreachable ({err})"


def host(url) -> str:
    return url.split("/")[2] if url and "//" in url else "-"


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 1

    old_base, new_base = (u.rstrip("/") for u in sys.argv[1:3])
    albums = {}
    for label, base in (("OLD", old_base), ("NEW", new_base)):
        try:
            data = json.loads(get(base + "/api/albums"))
            albums[label] = {a["id"]: a for a in data}
            print(f"{label}  {base}  ->  {len(data)} albums")
        except Exception as err:
            print(f"{label}  {base}  ->  FAILED: {err}")
            return 1

    old, new = albums["OLD"], albums["NEW"]
    problems = 0
    print()
    for album_id in sorted(set(old) | set(new)):
        a, b = old.get(album_id), new.get(album_id)
        if a is None or b is None:
            print(f"id {album_id}: only on {'NEW' if a is None else 'OLD'}")
            problems += 1
            continue

        differing = [f for f in FIELDS if a.get(f) != b.get(f)]
        old_img, new_img = digest(a.get("cover_image_url")), digest(b.get("cover_image_url"))
        same_image = old_img == new_img and not old_img.startswith("unreachable")

        status = "identical" if not differing and same_image else "DIFFERENT"
        print(f"id {album_id} ({a['title']}): {status}")
        print(f"    data    {'same' if not differing else 'differs in ' + ', '.join(differing)}")
        print(f"    image   {old_img} on {host(a.get('cover_image_url'))}  vs  {new_img} on {host(b.get('cover_image_url'))}"
              f"  ->  {'same file' if same_image else 'DIFFERENT'}")
        if status != "identical":
            problems += 1

    print("\nRESULT:", "identical data and identical cover files" if problems == 0 else f"{problems} problem(s) found")
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    sys.exit(main())