import json
import os
import random
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from metadata.exif import get_metadata

ROOT = Path(__file__).parent.parent
CONFIG_PATH = ROOT / "config.json"
LAYOUTS_PATH = ROOT / "layouts"
FRONTEND_DISPLAY = ROOT / "frontend" / "display"

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


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


def list_images(folder: str) -> list[str]:
    path = Path(folder)
    if not path.exists() or not path.is_dir():
        return []
    return [
        str(f)
        for f in path.iterdir()
        if f.suffix.lower() in SUPPORTED_EXTENSIONS
    ]


# --- API routes ---

@app.get("/api/config")
def get_config():
    return load_config()


@app.post("/api/config")
def set_config(config: dict):
    save_config(config)
    return {"ok": True}


@app.get("/api/layouts")
def get_layouts():
    layouts = []
    for f in sorted(LAYOUTS_PATH.glob("*.json")):
        layouts.append(json.loads(f.read_text()))
    return layouts


@app.get("/api/images/random")
def random_image(folder: str):
    """Return a random image file path from the given folder."""
    images = list_images(folder)
    if not images:
        raise HTTPException(status_code=404, detail="No images found in folder")
    return {"path": random.choice(images)}


@app.get("/api/metadata")
def image_metadata(path: str):
    """Return EXIF date and reverse-geocoded location for an image."""
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    return get_metadata(str(file_path))


@app.get("/api/images/list")
def list_images_api(folder: str):
    images = list_images(folder)
    return {"images": images, "count": len(images)}


@app.get("/api/image")
def serve_image(path: str):
    """Serve an image file by absolute path."""
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")
    if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported file type")
    return FileResponse(str(file_path))


# Serve the display frontend
app.mount("/display", StaticFiles(directory=str(FRONTEND_DISPLAY), html=True), name="display")

# Serve a simple root redirect
@app.get("/")
def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/display")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
