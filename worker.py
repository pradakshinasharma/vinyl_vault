import colorsys
import io
import urllib.request

from PIL import Image

from database import AlbumModel


def pick_dominant_color(image: Image.Image) -> tuple[int, int, int]:
    """Return the most prominent *vivid* colour of an image.

    A plain average of every pixel turns busy covers into muddy brown/grey.
    Instead, reduce the image to a small palette (median cut), then score each
    palette colour by how much of the picture it covers, boosted by saturation
    and brightness so near-black, near-white and grey areas don't win.
    """
    small = image.convert("RGB")
    small.thumbnail((160, 160))

    quantized = small.quantize(colors=8, method=Image.Quantize.MEDIANCUT)
    palette = quantized.getpalette() or []
    counts = quantized.getcolors() or []
    total = sum(count for count, _ in counts) or 1

    best_rgb = None
    best_score = -1.0
    for count, index in counts:
        r, g, b = palette[index * 3: index * 3 + 3]
        _, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)

        share = count / total
        score = share * (0.10 + s) * (0.30 + v)
        if v < 0.18:                      # near black
            score *= 0.20
        if v > 0.85 and s < 0.12:         # near white / very light grey
            score *= 0.15

        if score > best_score:
            best_score = score
            best_rgb = (r, g, b)

    if best_rgb is None:                  # fallback: plain average
        return small.resize((1, 1), resample=Image.Resampling.BOX).getpixel((0, 0))
    return best_rgb


def extract_dominant_color_task(album_id: int, image_url: str, db_session_factory):
    """Background operation: downloads image, finds dominant color, updates DB."""
    db = None
    try:
        # Download image into memory
        # Cloudflare R2 public URLs reject Python's default User-Agent (HTTP 403), so send our own.
        req = urllib.request.Request(image_url, headers={"User-Agent": "vinyl-vault-worker"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            img_data = resp.read()

        image = Image.open(io.BytesIO(img_data)).convert("RGB")
        r, g, b = pick_dominant_color(image)
        hex_color = f"#{r:02x}{g:02x}{b:02x}"

        # Update the album record in the database
        db = db_session_factory()
        album = db.query(AlbumModel).filter(AlbumModel.id == album_id).first()
        if album:
            album.dominant_color = hex_color
            db.commit()
    except Exception as e:
        print(f"[worker] colour extraction failed for album {album_id} ({image_url}): {type(e).__name__}: {e}", flush=True)
    finally:
        if db is not None:
            db.close()