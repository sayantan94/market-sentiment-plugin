"""
FastAPI backend for Market Sentiment Plugin
Collects posts from Chrome extension, stores in data/posts.json
Screenshots saved as PNGs in data/screenshots/
"""

import asyncio
import base64
import hashlib
import json
import os
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from backend.models import PostIn, Post
from config.settings import POSTS_FILE, SCAN_FILE, SEEN_FILE, DATA_DIR, SCREENSHOTS_DIR, BACKEND_PORT

app = FastAPI(title="Market Sentiment Collector")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _read_posts() -> list[dict]:
    if not os.path.exists(POSTS_FILE):
        return []
    with open(POSTS_FILE, "r") as f:
        return json.load(f)


def _write_posts(posts: list[dict]):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(POSTS_FILE, "w") as f:
        json.dump(posts, f, indent=2)


def _read_scan() -> dict:
    if not os.path.exists(SCAN_FILE):
        return {}
    with open(SCAN_FILE, "r") as f:
        return json.load(f)


def _write_scan(scan: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SCAN_FILE, "w") as f:
        json.dump(scan, f, indent=2)


def _read_seen() -> set:
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r") as f:
        return set(json.load(f))


def _add_seen(url: str):
    seen = _read_seen()
    seen.add(url)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)


def _save_screenshot(b64_data: str) -> str:
    """Save base64 PNG to data/screenshots/, return filename."""
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    raw = base64.b64decode(b64_data)
    # Use content hash as filename to dedupe
    name = hashlib.sha256(raw).hexdigest()[:16] + ".png"
    path = os.path.join(SCREENSHOTS_DIR, name)
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(raw)
    return name


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/save")
def save_post(post_in: PostIn):
    # Dedup: skip if we've already seen this URL
    if post_in.url:
        seen = _read_seen()
        if post_in.url in seen:
            posts = _read_posts()
            return {"saved": False, "duplicate": True, "count": len(posts)}

    screenshot_file = None
    if post_in.screenshot:
        screenshot_file = _save_screenshot(post_in.screenshot)

    post = Post(
        text=post_in.text,
        handle=post_in.handle,
        timestamp=post_in.timestamp,
        tickers=post_in.tickers,
        url=post_in.url,
        screenshot_file=screenshot_file,
    )
    posts = _read_posts()
    posts.append(post.model_dump())
    _write_posts(posts)

    # Mark as seen
    if post_in.url:
        _add_seen(post_in.url)

    return {"saved": True, "count": len(posts), "screenshot": screenshot_file}


@app.get("/posts")
def get_posts(ticker: str = Query(None)):
    posts = _read_posts()
    if ticker:
        ticker_upper = ticker.upper()
        posts = [p for p in posts if ticker_upper in p.get("tickers", [])]
    return {"posts": posts, "count": len(posts)}


@app.delete("/posts")
def clear_posts():
    _write_posts([])
    return {"cleared": True}


# --- Scan relay ---

@app.post("/scan")
def create_scan(request_body: dict):
    from datetime import datetime
    scan = {
        "tickers": request_body.get("tickers", []),
        "keywords": request_body.get("keywords", []),
        "maxPosts": request_body.get("maxPosts", 50),
        "query": request_body.get("query", ""),
        "status": "pending",
        "count": 0,
        "created_at": datetime.now().isoformat(),
    }
    _write_scan(scan)
    return scan


@app.get("/scan")
def get_scan():
    scan = _read_scan()
    if not scan:
        return {"status": "none"}
    return scan


@app.patch("/scan")
def patch_scan(request_body: dict):
    scan = _read_scan()
    if not scan:
        return {"status": "none"}
    for key in ("status", "count", "currentTicker", "phase"):
        if key in request_body:
            scan[key] = request_body[key]
    _write_scan(scan)
    return scan


@app.get("/scan/stream")
async def scan_stream(request: Request):
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            scan = _read_scan()
            if not scan:
                yield {"data": json.dumps({"status": "none"})}
                break
            yield {"data": json.dumps(scan)}
            if scan.get("status") in ("done", "error", "none"):
                break
            await asyncio.sleep(1)

    return EventSourceResponse(event_generator())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.server:app", host="0.0.0.0", port=BACKEND_PORT, reload=True)
