import copy
import json
import logging
import os
import random
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import media_index
import transcode
from layouts import load_layouts
from heic import HEIC_EXTENSIONS, HeicConversionError, get_converted, is_heic
from metadata.exif import get_metadata
from metadata.video import get_video_metadata

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config.json"
LAYOUTS_PATH = ROOT / "layouts"
FRONTEND_DISPLAY = ROOT / "frontend" / "display"
FRONTEND_EDITOR  = ROOT / "frontend" / "editor"
FRONTEND_ASSETS  = ROOT / "frontend" / "assets"  # decorative frame PNGs + frames.js (tools/frames)

def start_transcoder() -> None:
    try:
        config = load_config()
    except (OSError, ValueError) as e:
        logging.getLogger(__name__).warning("Could not read config to queue videos: %s", e)
        config = {}
    media_index.configure(config)
    transcode.transcoder.configure(config)
    if os.environ.get("PICME_TRANSCODE_AUTOSTART", "1") == "0":
        return  # tests drive the transcoder themselves
    transcode.transcoder.start()
    refresh_transcoder(config)


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_transcoder()
    yield
    transcode.transcoder.stop()  # kills a running ffmpeg (uvicorn reload restarts the process)


app = FastAPI(title="PicMe", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# Written on first run (see README → Configuration): classic-4, every slot showing ./photos
DEFAULT_CONFIG = {
    "layout": "classic-4",
    "wall_color": "#1a1a1a",
    "show_metadata": True,
    "date_format": "long",
    "max_metadata_pills": 3,
    # Pills on the photo, or in a caption on the mat under it; auto = on the mat for gallery mats
    "label_position": "auto",  # auto | photo | mat (README → Metadata pills)
    # cover = Fill (crop, no black bars) for new installs; a config without it means contain
    # (Fit). A slot's own fill_mode overrides it (README → Fit and Fill).
    "fill_mode": "cover",
    # Also the outer frame of gallery mats, unless gallery.frame_override (README → Gallery mat).
    # New installs start with wood; existing config.json files keep their own frame_defaults.
    "frame_defaults": {
        "mat_color": "#000000",
        "mat_width": 16,
        "show_frame": True,
        "frame_color": "#2c2318",
        "frame_width": 28,  # wood's default_width in frontend/assets/frames/frames.js
        "frame_style": "wood",  # solid, or a decorative style from frames.js
    },
    "metadata_defaults": {
        "show_date": True,
        "show_location": False,
        "show_camera": False,
        "show_megapixels": False,
        "show_fstop": False,
        "show_shutter_speed": False,
        "show_iso": False,
        "show_focal_length": False,
        "show_people": False,
        "text_size": "medium",  # small | medium | large | x-large — scales with the slot (README → Metadata pills)
        "pill_size": "medium",  # small | medium | large — padding around the text
    },
    # Daily layout rotation (README → Daily layout rotation); an empty list means every layout
    "layout_rotation": {"enabled": False, "layouts": [], "change_at": "04:00"},
    # Gallery mat (README → Gallery mat): one frame and mat, slots are openings. Used by gallery
    # layouts and any layout layout_modes switches to "gallery"; px at 1920x1080
    "gallery": {
        "mat_color": "#f4f1ea",
        "outer_margin": 64,
        "gutter": 28,
        "wall_margin": 12,
        "bevel": True,
        "bevel_width": 4,
        "bevel_color": "#fbfaf6",
        "show_frame": True,  # show_frame, frame_style and frame_color apply only with frame_override
        "frame_style": "wood",
        "frame_color": "#2c2318",
        "frame_width": 48,
        "frame_override": False,  # False: the outer frame's look follows frame_defaults
    },
    "layout_modes": {},  # layout id -> "gallery" | "slots", overriding the layout's own mode
    "source_path": "photos",  # relative to the server's cwd — run.sh / run.bat start in the repo root
    "include_subfolders": True,  # media folders include their subfolders (README → Subfolders)
    "slots": [
        {"id": i, "mode": "photos", "source": "local", "source_path": "photos", "cycle_seconds": 30}
        for i in range(1, 5)
    ],
}


def default_config() -> dict:
    return copy.deepcopy(DEFAULT_CONFIG)


def load_config() -> dict:
    """The saved config, or the defaults if there is none yet. Never writes."""
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return default_config()


def write_json_atomic(path: Path, data) -> None:
    """Write via a temp file and rename, so a reader (the display polls config.json) never sees
    a half-written file."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def save_config(config: dict) -> None:
    write_json_atomic(CONFIG_PATH, config)


def effective_subfolders(slot: dict, config: dict) -> bool:
    """Whether a slot's folder includes its subfolders (same rule as the display's
    slotSubfolders). A slot adopting the global folder follows the global setting; one with its
    own folder may set its own, else follows the global setting too."""
    adopted = (slot.get("use_global") or {}).get("source") is not False
    if adopted or slot.get("include_subfolders") is None:
        return config.get("include_subfolders", True) is not False
    return slot["include_subfolders"] is not False


def video_folders(config: dict) -> list[tuple[str, bool]]:
    """``(folder, include_subfolders)`` of video-mode slots (same rule as the display's slotFolder)."""
    folders = []
    for slot in config.get("slots") or []:
        if slot.get("mode") != "video":
            continue
        adopted = (slot.get("use_global") or {}).get("source") is not False
        folder = (config.get("source_path") or slot.get("source_path")) if adopted else slot.get("source_path")
        if folder:
            folders.append((folder, effective_subfolders(slot, config)))
    return folders


def refresh_transcoder(config: dict) -> None:
    """Apply video and listing settings and queue every video slot's folder for conversion."""
    media_index.configure(config)
    transcode.transcoder.configure(config)
    transcode.transcoder.enqueue_folders(video_folders(config))


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"} | HEIC_EXTENSIONS
VIDEO_EXTENSIONS = transcode.VIDEO_EXTENSIONS
MEDIA_KINDS = {"photos": SUPPORTED_EXTENSIONS, "video": VIDEO_EXTENSIONS}


def list_media(folder: str, kind: str = "photos", recursive: bool = True) -> list[str]:
    """Media in ``folder`` and (``recursive``) its subfolders — see media_index for what's skipped."""
    extensions = MEDIA_KINDS.get(kind)
    if extensions is None:
        raise HTTPException(status_code=400, detail=f"Unknown kind: {kind}")
    path = Path(folder)
    if not path.exists() or not path.is_dir():
        return []
    if kind == "video":
        # Only clips the browser can play now: as-is H.264, or an already-converted copy
        return [str(c.path) for c in transcode.transcoder.scan(path, recursive=recursive) if c.state == transcode.READY]
    return list(media_index.walk(path, recursive, extensions=extensions).files)


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
    if CONFIG_PATH.exists():
        return load_config()  # never rewritten here, even if it's {} — the display copes
    # First run: the display or editor is the first to ask, so write the defaults now
    config = default_config()
    try:
        save_config(config)
    except OSError as e:
        logging.getLogger(__name__).warning("Could not write default config to %s: %s", CONFIG_PATH, e)
    return config


@app.post("/api/config")
def set_config(config: dict):
    save_config(config)
    media_index.invalidate()  # a just-added folder or a toggled include_subfolders lists afresh
    refresh_transcoder(config)
    return {"ok": True}


@app.get("/api/browse")
def browse_directory(path: str = ""):
    start = Path(path).resolve() if path else Path.home()
    while not start.is_dir() and start.parent != start:
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
    return load_layouts(LAYOUTS_PATH)  # invalid files are skipped with a warning


@app.get("/api/images/random")
def random_image(folder: str, kind: str = "photos", recursive: bool = True):
    """Return a random image (or video, with kind=video) file path from the given folder."""
    images = list_media(folder, kind, recursive)
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
def list_images_api(folder: str, kind: str = "photos", peek: bool = False, recursive: bool = True):
    """``truncated``: only part of the folder is listed — it holds more than media_max_files, or
    has folders nested deeper than media_max_depth."""
    path = Path(folder)
    if kind != "video":
        images = list_media(folder, kind, recursive)
        walk = media_index.walk(path, recursive, extensions=SUPPORTED_EXTENSIONS)
        truncated = walk.truncated or walk.depth_capped
        return {"images": images, "count": len(images), "truncated": truncated}
    # Listing also queues clips that need converting, so a folder opened in the editor starts
    # converting. `pending` lets the editor tell "no videos" from "videos still converting".
    # `peek` only looks (the editor's hint while a folder path is still being typed).
    is_dir = path.is_dir()
    clips = transcode.transcoder.scan(path, enqueue=not peek, recursive=recursive) if is_dir else []
    images = [str(c.path) for c in clips if c.state == transcode.READY]
    return {"images": images, "count": len(images),
            "pending": sum(c.state == transcode.PENDING for c in clips),
            "truncated": is_dir and _partial(transcode.walk_videos(path, recursive))}


def _partial(walk: media_index.Walk) -> bool:
    return walk.truncated or walk.depth_capped


@app.get("/api/videos/status")
def videos_status(folder: str = "", peek: bool = False, recursive: bool = True):
    """Conversion progress for a folder's videos (or every video slot's folder). ``peek``
    reports without queueing anything."""
    folders = [(folder, recursive)] if folder else video_folders(load_config())
    return transcode.transcoder.status(folders, enqueue=not peek)


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
    # The requested path is validated first (a video extension, also after resolving symlinks).
    # The cache dir itself is never addressable: at most the converted copy of the file that
    # was asked for is served.
    file_path = resolve_media_file(path, VIDEO_EXTENSIONS, "Video")
    clip = transcode.transcoder.clip(file_path)
    if clip.state == transcode.LIVE_PHOTO:
        raise HTTPException(status_code=404, detail="Live Photo clips are hidden (include_live_photos)")
    if clip.state == transcode.PENDING:
        raise HTTPException(status_code=409, detail="Video is still being converted")
    if clip.state != transcode.READY:
        raise HTTPException(status_code=415, detail="Video can't be played in a browser and couldn't be converted")
    # Either the original (H.264) or its converted copy. .mov is served as video/mp4 too: some
    # Chromium-based TV browsers reject media labelled video/quicktime, while their MP4 demuxer
    # reads H.264 QuickTime containers fine.
    return FileResponse(str(clip.serve), media_type="video/mp4")


# Serve the display and editor frontends
app.mount("/display", StaticFiles(directory=str(FRONTEND_DISPLAY), html=True), name="display")
app.mount("/editor",  StaticFiles(directory=str(FRONTEND_EDITOR),  html=True), name="editor")
app.mount("/assets",  StaticFiles(directory=str(FRONTEND_ASSETS)), name="assets")

# Serve a simple root redirect
@app.get("/")
def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/display")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
