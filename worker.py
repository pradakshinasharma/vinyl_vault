import io
import urllib.request

from PIL import Image

from database import AlbumModel


def extract_dominant_color_task(album_id: int, image_url: str, db_session_factory):
    """Background operation: downloads image, finds dominant color, updates DB."""
    db = None
    try:
        # Download image into memory
        with urllib.request.urlopen(image_url, timeout=15) as resp:
            img_data = resp.read()

        image = Image.open(io.BytesIO(img_data)).convert("RGB")
        # BOX resampling to 1x1 gives a true average of all pixels
        r, g, b = image.resize((1, 1), resample=Image.Resampling.BOX).getpixel((0, 0))
        hex_color = f"#{r:02x}{g:02x}{b:02x}"

        # Update the album record in the database
        db = db_session_factory()
        album = db.query(AlbumModel).filter(AlbumModel.id == album_id).first()
        if album:
            album.dominant_color = hex_color
            db.commit()
    except Exception as e:
        print(f"Background task failed: {e}")
    finally:
        if db is not None:
            db.close()