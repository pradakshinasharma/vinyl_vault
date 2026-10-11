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

from database import AlbumModel, SessionLocal, get_db, init_db
from schemas import AlbumCreate, AlbumUpdate, AlbumResponse
from storage import upload_file_to_supabase
from worker import extract_dominant_color_task


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create the "albums" table on startup if it doesn't exist
    init_db()
    yield


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

    # Upload file to Supabase Storage
    try:
        cover_url = upload_file_to_supabase(file)
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