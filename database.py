import os

from sqlalchemy import create_engine, Column, Integer, String, Boolean, JSON
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:password@localhost:5432/postgres",
)

def normalize_database_url(url: str) -> str:
    """Normalise the URL scheme and force the psycopg2 driver (the one in requirements.txt).

    Handles "postgres://", "postgresql://" and "postgresql+psycopg://" alike, so connection
    strings copied from Supabase, Neon or any other host work unchanged.
    """
    url = url.strip().strip("\"'")
    # Tolerate common copy-paste slips: "psql 'postgresql://...'" (Neon) or "DATABASE_URL=postgresql://..."
    if "://" in url and not url.startswith(("postgres", "sqlite")):
        url = url[url.index("://") - len(url.split("://")[0].split()[-1]):]
    if url.upper().startswith("DATABASE_URL="):
        url = url.split("=", 1)[1]
    url = url.strip().strip("\"'")
    for prefix in ("postgres://", "postgresql://", "postgresql+psycopg://"):
        if url.startswith(prefix):
            return "postgresql+psycopg2://" + url[len(prefix):]
    return url


DATABASE_URL = normalize_database_url(DATABASE_URL)

# pool_pre_ping avoids "connection closed" errors after idle periods
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class AlbumModel(Base):
    __tablename__ = "albums"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)               # 1. String
    track_count = Column(Integer, nullable=False)        # 2. Integer
    is_explicit = Column(Boolean, default=False)         # 3. Boolean
    genres = Column(JSON, nullable=False)                # 4. JSON / Array of Strings
    cover_image_url = Column(String, nullable=True)
    dominant_color = Column(String, default="#333333")


def get_db():
    """Dependency provider for FastAPI route endpoints."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create the table in Supabase Postgres on startup (no-op if it exists)."""
    Base.metadata.create_all(bind=engine)