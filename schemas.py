from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class AlbumBase(BaseModel):
    title: str = Field(..., min_length=1)                # 1. String
    track_count: int = Field(..., ge=1)                  # 2. Integer
    is_explicit: bool                                    # 3. Boolean
    genres: List[str] = Field(..., min_length=1)         # 4. List / Array of Strings


class AlbumCreate(AlbumBase):
    pass


class AlbumUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1)
    track_count: Optional[int] = Field(None, ge=1)
    is_explicit: Optional[bool] = None
    genres: Optional[List[str]] = Field(None, min_length=1)


class AlbumResponse(AlbumBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cover_image_url: Optional[str] = None
    dominant_color: Optional[str] = "#333333"