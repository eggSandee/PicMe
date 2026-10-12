import json
import os
import random
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from heic import HEIC_EXTENSIONS, HeicConversionError, get_converted, is_heic
from metadata.exif import get_metadata
from metadata.video import get_video_metadata

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config.json"
LAYOUTS_PATH = ROOT / "layouts"
FRONTEND_DISPLAY = ROOT / "frontend" / "display"
FRONTEND_EDITOR  = ROOT / "frontend" / "editor"

app = FastAPI(title="PicMe")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def save_config(config: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(config, indent=2))


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"} | HEIC_EXTENSIONS
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}
MEDIA_KINDS = {"photos": SUPPORTED_EXTENSIONS, "video": VIDEO_EXTENSIONS}


def list_media(folder: str, kind: str = "photos") -> list[str]:
    extensions = MEDIA_KINDS.get(kind)
    if extensions is None:
        raise HTTPException(status_code=400, detail=f"Unknown kind: {kind}")
    path = Path(folder)
    if not path.exists() or not path.is_dir():
        return []
    return [
        str(f)
        for f in path.iterdir()
        # Skip dotfiles — macOS AppleDouble files (._clip.mov) match the extension but aren't playable
        if f.suffix.lower() in extensions and not f.name.startswith(".") and f.is_file()
    ]


def resolve_media_file(path: str, extensions: set[str], label: str) -> Path:
    """Resolve symlinks and check both the requested name and the real target have an allowed
    extension — otherwise a link named clip.mp4 could expose any file it points to."""
    requested = Path(path)
    target = requested.resolve()
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"{label} not found")
    if requested.suffix.lower() not in extensions or target.suffix.lower() not in extensions:
        raise HTTPException(status_code=400, detail="Unsupported file type")
    return target


# --- API routes ---

@app.get("/api/config")
def get_config():
    return load_config()


@app.post("/api/config")
def set_config(config: dict):
    save_config(config)
    return {"ok": True}


@app.get("/api/browse")
def browse_directory(path: str = ""):
    start = Path(path).resolve() if path else Path.home()
    if not start.is_dir():
        start = start.parent
    entries = []
    try:
        for item in sorted(start.iterdir()):
            if item.is_dir() and not item.name.startswith('.'):
                entries.append({"name": item.name, "path": str(item)})
    except PermissionError:
        pass
    parent = str(start.parent) if start.parent != start else None
    return {"path": str(start), "parent": parent, "entries": entries}


@app.get("/api/layouts")
def get_layouts():
    layouts = []
    for f in sorted(LAYOUTS_PATH.glob("*.json")):
        layouts.append(json.loads(f.read_text()))
    return layouts


@app.get("/api/images/random")
def random_image(folder: str, kind: str = "photos"):
    """Return a random image (or video, with kind=video) file path from the given folder."""
    images = list_media(folder, kind)
    if not images:
        raise HTTPException(status_code=404, detail="No images found in folder")
    return {"path": random.choice(images)}


@app.get("/api/metadata")
def image_metadata(path: str):
    """Return EXIF date and reverse-geocoded location for an image, or the container date for a video."""
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    if file_path.suffix.lower() in VIDEO_EXTENSIONS:
        return get_video_metadata(str(file_path))
    return get_metadata(str(file_path))


@app.get("/api/images/list")
def list_images_api(folder: str, kind: str = "photos"):
    images = list_media(folder, kind)
    return {"images": images, "count": len(images)}


@app.get("/api/image")
def serve_image(path: str):
    """Serve an image file by absolute path."""
    file_path = resolve_media_file(path, SUPPORTED_EXTENSIONS, "Image")
    if is_heic(file_path):
        # Only Safari renders HEIC, so serve a cached JPEG conversion instead
        try:
            return FileResponse(str(get_converted(file_path)), media_type="image/jpeg")
        except HeicConversionError:
            raise HTTPException(status_code=415, detail="Could not decode HEIC image")
    return FileResponse(str(file_path))


@app.get("/api/video")
def serve_video(path: str):
    """Serve a video file by absolute path. FileResponse handles Range/If-Range (206/416)
    natively, which TV browsers need to stream and seek."""
    file_path = resolve_media_file(path, VIDEO_EXTENSIONS, "Video")
    # .mov is served as video/mp4 too: some Chromium-based TV browsers reject media labelled
    # video/quicktime, while their MP4 demuxer reads H.264 QuickTime containers fine.
    return FileResponse(str(file_path), media_type="video/mp4")


# Serve the display and editor frontends
app.mount("/display", StaticFiles(directory=str(FRONTEND_DISPLAY), html=True), name="display")
app.mount("/editor",  StaticFiles(directory=str(FRONTEND_EDITOR),  html=True), name="editor")

# Serve a simple root redirect
@app.get("/")
def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/display")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
