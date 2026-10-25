import asyncio
from contextlib import asynccontextmanager
import json
from typing import List

from dotenv import load_dotenv

load_dotenv()

from fastapi import (
    FastAPI,
    BackgroundTasks,
    UploadFile,
    File,
    Form,
    HTTPException,
    Depends,
)
from pathlib import Path

from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import ValidationError

from sqlalchemy import text

from database import AlbumModel, SessionLocal, engine, get_db, init_db
from schemas import AlbumCreate, AlbumUpdate, AlbumResponse
from storage import upload_cover
from worker import extract_dominant_color_task


async def _try_init_db(attempt: int) -> bool:
    """Create the "albums" table if needed. Returns False (and logs why) instead of crashing."""
    try:
        await asyncio.to_thread(init_db)
        print("[startup] database ready", flush=True)
        return True
    except Exception as err:
        message = str(err).strip()
        first_line = message.splitlines()[0][:300] if message else ""
        print(f"[startup] database not ready (attempt {attempt}): {type(err).__name__}: {first_line}", flush=True)
        return False


async def _retry_init_db():
    """Keep trying in the background, e.g. while a sleeping Neon database wakes up."""
    attempt = 1
    while True:
        await asyncio.sleep(min(5 * attempt, 30))
        attempt += 1
        if await _try_init_db(attempt):
            return


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Normal case: the database answers straight away and the table is ready before any request.
    # If it doesn't, keep the server running (so the platform's health check passes and the logs
    # show the real reason) and keep retrying in the background.
    retry_task = None
    if not await _try_init_db(1):
        retry_task = asyncio.create_task(_retry_init_db())
    yield
    if retry_task:
        retry_task.cancel()


app = FastAPI(title="Vinyl Vault API", lifespan=lifespan)

# Allow the browser frontend (served from another origin) to call the API.
# Tighten allow_origins to your real frontend URL once deployed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


BASE_DIR = Path(__file__).resolve().parent


@app.get("/health")
def health():
    return {"status": "ok", "service": "Vinyl Vault API"}


@app.get("/health/db")
def health_db():
    """Quick check that the app can reach its database (shows only the error type, never secrets)."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"database": "ok"}
    except Exception as err:
        raise HTTPException(status_code=503, detail=f"database error: {type(err).__name__}")


# Serve the frontend from the same server, so one Render address gives you the page AND the API.
@app.get("/", include_in_schema=False)
def home():
    page = BASE_DIR / "index.html"
    if page.exists():
        return FileResponse(page)
    return {"status": "ok", "service": "Vinyl Vault API", "note": "index.html not found next to main.py"}


# 1. LIST (Read All)
@app.get("/api/albums", response_model=List[AlbumResponse])
def list_albums(db=Depends(get_db)):
    return db.query(AlbumModel).order_by(AlbumModel.id.desc()).all()


# 2. READ (Single Item)
@app.get("/api/albums/{album_id}", response_model=AlbumResponse)
def get_album(album_id: int, db=Depends(get_db)):
    album = db.query(AlbumModel).filter(AlbumModel.id == album_id).first()
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")
    return album


# 3. CREATE (Validates 4 types + uploads cover + triggers background task)
@app.post("/api/albums", response_model=AlbumResponse, status_code=201)
def create_album(
    background_tasks: BackgroundTasks,
    title: str = Form(...),
    track_count: int = Form(...),
    is_explicit: bool = Form(...),
    genres_json: str = Form(...),  # Passed as '["Rock", "Pop"]'
    file: UploadFile = File(...),
    db=Depends(get_db),
):
    # Validate everything BEFORE uploading, so bad input never leaves an orphan file
    # Accept a JSON array ('["Rock","Pop"]') or a plain comma-separated list ('Rock,Pop').
    # The Swagger /docs page sends the second form, so both must work.
    try:
        genres = json.loads(genres_json)
    except json.JSONDecodeError:
        genres = [g.strip() for g in genres_json.split(",") if g.strip()]

    if isinstance(genres, str):
        genres = [genres]

    if not isinstance(genres, list):
        raise HTTPException(
            status_code=422,
            detail='genres_json must be a JSON array like ["Rock","Pop"] or a comma-separated list',
        )

    try:
        album_data = AlbumCreate(
            title=title,
            track_count=track_count,
            is_explicit=is_explicit,
            genres=genres,
        )
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=json.loads(e.json()))

    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=400, detail="Cover must be an image file")

    # Upload the cover to the configured storage backend (Supabase Storage or an S3-compatible bucket)
    try:
        cover_url = upload_cover(file)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Cover upload failed: {e}")

    new_album = AlbumModel(**album_data.model_dump(), cover_image_url=cover_url)
    db.add(new_album)
    db.commit()
    db.refresh(new_album)

    # Trigger background operation (pass the session FACTORY, not get_db)
    background_tasks.add_task(
        extract_dominant_color_task, new_album.id, cover_url, SessionLocal
    )
    return new_album


# 4. UPDATE
@app.put("/api/albums/{album_id}", response_model=AlbumResponse)
def update_album(album_id: int, payload: AlbumUpdate, db=Depends(get_db)):
    album = db.query(AlbumModel).filter(AlbumModel.id == album_id).first()
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(album, field, value)
    db.commit()
    db.refresh(album)
    return album


# 5. DELETE
@app.delete("/api/albums/{album_id}")
def delete_album(album_id: int, db=Depends(get_db)):
    album = db.query(AlbumModel).filter(AlbumModel.id == album_id).first()
    if not album:
        raise HTTPException(status_code=404, detail="Album not found")
    db.delete(album)
    db.commit()
    return {"message": "Album deleted"}