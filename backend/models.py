"""Pydantic models for Market Sentiment Plugin"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class PostIn(BaseModel):
    """Incoming post from Chrome extension"""

    text: str
    handle: str
    timestamp: str = ""
    tickers: list[str] = Field(default_factory=list)
    url: str = ""
    screenshot: Optional[str] = None  # base64 PNG from tab capture


class Post(BaseModel):
    """Stored post with metadata"""

    text: str
    handle: str
    timestamp: str = ""
    tickers: list[str] = Field(default_factory=list)
    url: str = ""
    screenshot_file: Optional[str] = None  # filename in data/screenshots/
    saved_at: str = Field(default_factory=lambda: datetime.now().isoformat())
