# PicMe
### A collage-style digital picture frame app for large screens

---

## What This Is

Most digital picture frame software shows one photo at a time on a large screen. PicMe treats the screen as a living collage — multiple photo slots, each cycling independently on its own schedule, with support for video, metadata overlays, and a web-based UI for configuration. It is designed to run on any screen with a browser: a Samsung Frame TV, a monitor, a tablet on the wall.

---

## Core Concept

The screen is divided into **slots**. Each slot:
- Displays a photo or video from a configured source
- Cycles to a new image on its own timer (independent of other slots)
- Can be grouped with other slots so they cycle together
- Can optionally show a metadata overlay (date, location, people)

Example: A 6-slot layout might have slots 1, 3, and 5 cycling every 30 seconds together, slot 2 cycling every 2 minutes on its own, and slot 6 looping a short video indefinitely. The viewer sees a constantly but gently changing collage — never a blank screen, never a jarring full-screen swap.

---

## Architecture Overview

```
PicMe/
├── backend/          # Python (FastAPI) — serves photos, config, source adapters
│   ├── main.py       # API server entry point
│   ├── sources/      # Photo source adapters (local, Google Photos, etc.)
│   ├── metadata/     # EXIF extraction, geocoding, face tag integration
│   └── config/       # Layout and slot configuration management
│
├── frontend/         # Browser UI (HTML/CSS/JavaScript)
│   ├── display/      # The fullscreen collage viewer (runs on the TV)
│   └── editor/       # Web-based layout and settings editor
│
├── layouts/          # JSON layout templates (predefined slot arrangements)
├── config.json       # User's saved configuration (auto-managed, not hand-edited)
└── README.md         # This file
```

**How it runs:**
1. The backend server starts on the user's PC (or a Raspberry Pi, etc.)
2. The TV browser opens `http://[local-ip]:8000/display` → fullscreen collage
3. Any device on the same network opens `http://[local-ip]:8000/editor` → configuration UI

---

## Milestone Plan

### ✅ Milestone 1 — Core Engine (Local Photos)
*Goal: A working collage viewer with local photos. No cloud, no complexity.*

- [x] FastAPI backend that serves images from a local folder
- [x] Fullscreen browser display with configurable slot grid
- [x] Independent per-slot cycling timers
- [x] Slot grouping (define groups that cycle together)
- [x] No duplicate photos across slots — shuffled per-folder queue ensures each image appears in at most one slot at a time
- [x] 3–5 built-in layout templates (e.g., 2x2, 3x2, asymmetric hero)
- [x] Basic config file (auto-managed JSON)

**Definition of done:** Open a browser on a TV, see a collage of local photos cycling independently.

---

### ✅ Milestone 2 — Metadata Overlays
*Goal: Optionally show photo context without cluttering the image.*

- [x] EXIF date extraction from image files
- [x] GPS coordinate extraction + reverse geocoding to city/region names
- [x] Per-slot metadata overlay toggle (date, location, camera, or any combination)
- [x] Global metadata toggle (`show_metadata: false` hides all overlays instantly)
- [x] Overlay styling: subtle, non-intrusive (semi-transparent pill at corner)

**Notes:**
- Reverse geocoding uses a free API (e.g., OpenStreetMap Nominatim) — no API key required
- Photos without GPS data simply show no location overlay
- Date format is configurable via `date_format`: `long`, `medium`, `short`, or `iso`

---

### ✅ Milestone 3 — Web Editor UI
*Goal: Configure everything without touching a config file.*

- [x] Visual layout editor — see slot arrangement, click to configure each slot
- [x] Per-slot settings: source folder, cycle timer, group assignment, metadata toggle
- [x] Per-slot fill mode toggle: **fill** (crop to fit, no bars) vs **fit** (full photo, letterboxed)
- [x] Per-slot and global frame settings: mat color, mat width, frame color, frame width
- [x] Layout template picker with visual previews
- [x] Live preview of changes before applying
- [x] Settings saved automatically to config.json

---

### Milestone 4 — Video Slot Support
*Goal: Any slot can play a video on loop instead of cycling photos.*

- [x] Per-slot mode toggle: photo cycling vs. video loop
- [x] Video plays muted by default (optional audio toggle)
- [x] Video loops for a configurable number of times before the slot advances
- [x] Supported formats: MP4, MOV (browser-native playback)
- [x] iPhone photos (HEIC/HEIF) — converted on the fly to JPEG so every browser can show them, with full metadata overlays
- [x] iPhone HEVC/HDR videos converted to H.264 automatically in the background (needs ffmpeg)
- [ ] Time-of-day mat/wall color sync — automatically shift colors between day and night (e.g. warm white during day, black at night)
- [x] Daily layout rotation: layout selection (choose which layouts are eligible to rotate)
- [ ] Decorative picture frames (wood, gold) using PNG assets and CSS `border-image`
- [ ] Per-slot drag-to-reorder in the editor
- [ ] Configurable metadata pill size and text size
- [ ] Basic / Advanced editor mode — Basic hides less-common settings so the editor stays approachable for users who don't need full configurability (exact parameter split TBD)

---

### Milestone 5 — Google Photos Integration
*Goal: Pull photos directly from a user's Google Photos library.*

- [ ] Google OAuth 2.0 login flow (user authorizes once, token stored locally)
- [ ] Browse Google Photos albums in the editor
- [ ] Assign a Google Photos album to any slot
- [ ] Incremental sync (new photos in the album appear automatically)
- [ ] Graceful offline fallback (cached thumbnails if internet is unavailable)

**Notes:**
- Uses the official Google Photos Library API
- No photos are uploaded or stored permanently — streamed/cached locally for display

---

### Milestone 6 — Additional Photo Sources
*Goal: Expand to other cloud providers.*

- [ ] Amazon Photos (note: API access is limited; may require workarounds)
- [ ] iCloud Photos (note: no official API; options include iCloud shared albums or local sync via iCloud Drive on Windows)
- [ ] Network folder / NAS (SMB/UNC path support)
- [ ] Source health indicators in the editor (connected / disconnected / error)

---

### Milestone 7 — Face Recognition & People Tags
*Goal: Optionally show who is in each photo as part of the metadata overlay.*

- [ ] Google Photos face/people tags pulled via API (if user has People & Pets enabled)
- [ ] Apple Photos face tags via local sync (if iCloud library is synced to Windows)
- [ ] "People in this photo" shown as part of slot metadata overlay
- [ ] Privacy toggle: disable people tags globally or per-slot
- [ ] Manual tag override (user can correct or add names locally)

**Notes:**
- Face recognition is intentionally not run locally — we leverage tags already created by Google/Apple
- This avoids biometric data processing and the complexity of running a local ML model
- If a source provides no people tags, this feature simply doesn't appear for that slot

---

## Layout Template Reference

Templates define the slot grid. Each template has a name, a visual description, and a number of slots.

| Template Name | Slots | Description |
|---|---|---|
| `classic-4` | 4 | 2×2 equal grid |
| `classic-6` | 6 | 3×2 equal grid |
| `hero-left` | 5 | Large slot left (2/3 width), 4 small slots right |
| `hero-center` | 5 | Large slot center, 2 small top, 2 small bottom |
| `landscape-strip` | 3 | One wide top slot, two equal bottom slots |
| `custom` | 1–12 | User-defined via drag-and-drop in the editor |

---

## Slot Configuration Reference

Each slot supports the following settings:

```json
{
  "id": 1,
  "mode": "photos",            // "photos" or "video"
  "source": "local",           // "local", "google_photos", "amazon", "icloud", "network"
  "source_path": "/Photos/Vacations",
  "cycle_seconds": 60,         // How often this slot advances (seconds)
  "group": "A",                // Slots in the same group cycle together (optional)
  "metadata": {
    "show_date": true,
    "show_location": true,
    "show_camera": false,
    "show_megapixels": false,
    "show_fstop": false,
    "show_shutter_speed": false,
    "show_iso": false,
    "show_focal_length": false,
    "show_people": false
  },
  "video_loops": 2,            // For video mode: how many times each video plays before advancing
  "video_audio": false         // For video mode: play sound (muted by default)
}
```

**Video slots** (`"mode": "video"`) play `.mp4`, `.mov` and `.m4v` files from the slot's media folder. Each video plays `video_loops` times, then the slot crossfades to the next video — using the same shuffled, no-duplicate queue as photos, so the same clip never plays in two slots at once. Notes:

- Video slots ignore `cycle_seconds` and `group` — they advance when their loops finish, and group timers never cut a video off.
- Only the date pill is shown, read from the clip's recording date (MP4/MOV `mvhd` creation time). Clips without a plausible date show no pill.
- Browsers usually block autoplay with sound until someone interacts with the page. If sound is blocked, the video keeps playing muted.
- iPhone (HEVC), HDR and ProRes clips are converted to H.264 in the background so every browser can play them. Until a clip is converted it's left out of the rotation. See **iPhone videos (HEVC)** below.
- Each playing video uses a hardware decoder; on TVs keep the number of video slots small. Clips larger than `video_max_edge` (default 1080p) are scaled down for this reason.

---

## Tech Stack

| Layer | Technology | Reason |
|---|---|---|
| Backend | Python 3.13 + FastAPI | Simple, fast, great for file serving and APIs |
| Frontend display | Vanilla HTML/CSS/JS | No build step, runs on any browser including TVs |
| Frontend editor | Vanilla HTML/CSS/JS | No build step, same as display — simpler to host and iterate |
| Photo metadata | `Pillow` + `piexif` | EXIF extraction from local files |
| iPhone photos | `pillow-heif` | Decodes HEIC/HEIF (prebuilt wheels bundle libheif) |
| Geocoding | OpenStreetMap Nominatim | Free, no API key required |
| Config storage | JSON file | Human-readable, no database needed for v1 |
| Auth (Google) | OAuth 2.0 via `google-auth` | Standard library, well documented |

---

## Getting Started

### What you need

| Requirement | Notes |
|---|---|
| **Python 3.13** | Download from [python.org](https://python.org/downloads) (Windows) or `brew install python@3.13` (macOS). |
| **A modern browser** | Chrome, Edge, Firefox, or Safari. The display is designed for full-screen use on a TV browser. |
| **Your photos** | JPEG, PNG, GIF, WebP, or HEIC/HEIF (iPhone) files in a folder on your machine. Extensions are matched case-insensitively (`IMG_1234.HEIC` works). |
| **Videos (optional)** | MP4 or MOV files for video slots. H.264 clips play as-is. iPhone (HEVC/HDR) and ProRes clips are converted automatically, which needs ffmpeg (below). |
| **ffmpeg (optional, recommended for iPhone videos)** | macOS: `brew install ffmpeg`. Windows: `winget install Gyan.FFmpeg`, then open a new terminal so `PATH` updates. Without ffmpeg PicMe runs normally, but iPhone videos are skipped. |

---

### First-time setup

**1. Get the code**

Download or clone this repository. All commands below should be run from the project root folder (`PicMe/`).

**2. Create a virtual environment and install dependencies**

**Windows:**
```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```

**macOS** (macOS 15+ / Tahoe): macOS 16 ships with a system `libexpat` that conflicts with Homebrew Python's pip bootstrapper. Use [`uv`](https://github.com/astral-sh/uv) (a fast Rust-based package manager) instead — install it once with `brew install uv`, then:
```bash
export DYLD_LIBRARY_PATH="/opt/homebrew/opt/expat/lib"
uv venv --python /opt/homebrew/bin/python3.13 .venv
uv pip install -r requirements.txt
```
This is a one-time setup step. The `DYLD_LIBRARY_PATH` workaround is only needed during package installation; starting the server works normally after that.

**3. Add your photos**

Copy or move your photos into the `photos/` folder inside the project:

```
PicMe/
└── photos/
    ├── vacation.jpg
    ├── birthday.png
    └── ...
```

You can use any folder on your machine — see the **Configuration** section below to point slots at a different location.

---

### Starting, stopping, and restarting

The server runs on **port 8000** and hot-reloads on file changes (no restart needed for code edits).

**Using the launch helpers (recommended):**
```bash
# macOS / Linux
./run.sh

# Windows
run.bat
```

**Or manually:**
```bash
# macOS / Linux
.venv/bin/python backend/main.py

# Windows
.\.venv\Scripts\python backend\main.py
```

**Stop:** Press `Ctrl+C` in the terminal. If running in the background:
```bash
# macOS / Linux — kill whatever is on port 8000
lsof -ti:8000 | xargs kill
```

**Restart:** Stop (above), then start again. Hot-reload (`reload=True`) handles most code changes automatically.

**Background (macOS / Linux, logs to `picme.log`):**
```bash
nohup .venv/bin/python backend/main.py > picme.log 2>&1 &
```

---

### Accessing from other devices on your network

| Device | URL |
|--------|-----|
| Same machine (browser) | `http://localhost:8000/display` |
| TV or any LAN device | `http://[mac-mini-ip]:8000/display` |
| Editor (any device) | `http://[mac-mini-ip]:8000/editor` |

Find the Mac Mini's LAN IP:
```bash
ipconfig getifaddr en0
```

Example: if that returns `192.168.68.50`, open `http://192.168.68.50:8000/display` on your Windows PC or TV.

> **Note:** The macOS firewall must allow incoming connections on port 8000. The built-in app firewall can be checked in **System Settings → Network → Firewall**. If it is off (default), no action needed.

---

### Configuration

The file `config.json` in the project root controls which layout is used and how each slot behaves. It is created automatically with defaults on first run — the first time the display or editor is opened — using the classic-4 layout with every slot showing the `photos` folder. You can edit it directly: open displays re-read it every 3 seconds and apply changes on their own. If you change a video slot's folder by hand, restart the server (or open the folder in the editor) so its iPhone clips get converted.

Top-level settings:

```json
{
  "layout": "classic-4",
  "wall_color": "#1a1a1a",       // background color shown between and around frames (the "wall")
  "show_metadata": true,         // global kill switch — false hides all overlays on all slots instantly
  "date_format": "long",         // "long" (July 4, 2019) | "medium" (Jul 4, 2019) | "short" (7/4/2019) | "iso" (2019-07-04)
  "max_metadata_pills": 3,       // max overlays shown per slot (excluding date); omit for no limit
  "frame_defaults": {            // mat and frame style applied to every slot; per-slot frame: {} overrides
    "mat_color": "#000000",      // mat color behind the photo (any CSS color)
    "mat_width": 16,             // mat thickness in pixels
    "show_frame": true,          // show outer frame border
    "frame_color": "#2c2318",    // dark wood frame (any CSS color)
    "frame_width": 8             // frame border thickness in pixels
  },
  "metadata_defaults": {         // applied to every slot; per-slot metadata overrides these
    "show_date": true,
    "show_location": false,
    "show_camera": false,
    "show_megapixels": false,
    "show_fstop": false,
    "show_shutter_speed": false,
    "show_iso": false,
    "show_focal_length": false,
    "show_people": false
  },
  "video_max_edge": 1920,        // long-edge cap for converted videos: 1280 | 1920 (default, 1080p) | 3840 | 0 = original size
  "include_live_photos": false,  // true = also play the short .MOV clips saved with Live Photos
  "layout_rotation": {           // show a different layout each day (see Daily layout rotation below)
    "enabled": false,
    "layouts": [],               // layout ids to rotate through; [] = every layout
    "change_at": "04:00"         // local time on the display when the next day's layout appears
  }
}
```

Per-slot `metadata` and `frame` are both optional. If omitted, the slot inherits the global defaults. If present, only the fields listed override the defaults.

```json
{
  "id": 1,
  "source_path": "photos",
  "cycle_seconds": 30,
  "metadata": { "show_camera": true },       // inherits everything else from metadata_defaults
  "frame": { "mat_color": "#ffffff" }        // white mat on this slot only; inherits frame_defaults for the rest
}
```

A video slot sets `mode` to `"video"` and points at a folder of MP4/MOV clips (see **Video slots** in the Slot Configuration Reference above):

```json
{
  "id": 2,
  "mode": "video",
  "source_path": "/Videos/Clips",
  "use_global": { "source": false },
  "video_loops": 3,                          // play each clip 3 times, then advance
  "video_audio": false                       // muted (default)
}
```

Available metadata fields:

| Field | Shows |
|---|---|
| `show_date` | Photo date from EXIF — always pinned to the **bottom** of the overlay, not counted against the cap |
| `show_location` | City/region from GPS data (requires internet for reverse geocoding) |
| `show_camera` | Camera make/model from EXIF |
| `show_megapixels` | Resolution, e.g. `12.2 MP` |
| `show_fstop` | Aperture, e.g. `f/2.8` |
| `show_shutter_speed` | Shutter speed, e.g. `1/250s` |
| `show_iso` | ISO sensitivity, e.g. `ISO 400` |
| `show_focal_length` | Focal length, e.g. `50mm` |

Non-date pills are shown in the table order above. If `max_metadata_pills` is set, only the first N enabled non-date fields that have data are shown — put your highest-priority fields first in your slot config.

To use a different photo folder (e.g. a folder elsewhere on your PC), set `source_path` to the full path:

```json
"source_path": "C:/Users/yourname/Pictures/Vacations"
```

#### Daily layout rotation

Turn on **Rotate layout daily** in the editor's Layout section, then tick the layouts to rotate through. The display shows one of them each day:

- **Which layout:** the ticked layouts take turns in the order the editor lists them, so two days in a row never show the same layout (when 2 or more are ticked). Ticking every layout is saved as `"layouts": []`, which also includes layouts added later. With only one ticked, that layout is shown every day.
- **When it changes:** at `change_at` (default 04:00) by the display's own clock and time zone. An open display switches by itself within 30 seconds, and a TV that was asleep switches as soon as it wakes. Before `change_at` it is still the previous day's layout.
- **Stays the same all day:** reloading the page, opening a second TV or opening the editor gives the same layout for the same day. The editor marks today's layout with a **Today** badge and says which layout comes next. Changing the ticked layouts can change today's layout straight away.
- **Base layout:** clicking a layout still sets `layout`, which is shown when rotation is off and is the layout the editor preview shows.
- **Slots:** slot *N* is always the same slot on every layout. A layout with fewer slots shows slots 1 to its slot count; the rest are kept with their settings and come back on days with a bigger layout. When rotation is on, the editor creates slots up to the biggest ticked layout (as photo slots using the global settings) and lists the extra slots under the preview so you can edit them.
- **Video slots and groups:** a video slot plays only on days its position is visible, but its folder is still converted in the background so it's ready. A larger layout can show more video slots at once, so keep their number small. A group only cycles the slots that are visible that day, at the timer of its first visible slot.
- **Old configs:** `"daily_layout_rotation": true` from earlier versions still works (all layouts, switching at midnight). The editor converts it to `layout_rotation` the next time it saves.

---

### iPhone photos (HEIC/HEIF)

iPhones save photos as HEIC, which only Safari can display. PicMe lists `.heic`/`.heif` files like any other photo and, when a slot shows one, converts it on the server to a JPEG:

- Orientation is applied (portrait shots aren't sideways).
- Display P3 / wide-gamut colors are converted to sRGB so they don't look washed out. HDR photos show their standard (SDR) image.
- Large photos (e.g. 48MP) are scaled down to at most 3840px on the long edge so TVs stay fast. Metadata overlays (including megapixels) are read from the original.

The first time a large HEIC is shown it takes about a second to convert. The converted JPEGs are cached, keyed by the file's path, modified time, and size, so an edited photo is converted again. The cache lives here:

| OS | Cache folder |
|---|---|
| macOS | `~/Library/Caches/PicMe/heic` |
| Windows | `%LOCALAPPDATA%\PicMe\Cache\heic` |
| Linux | `~/.cache/picme/heic` (or `$XDG_CACHE_HOME/picme/heic`) |

Set the `PICME_CACHE_DIR` environment variable to use a different folder. Your original photos are only ever read, never modified, and nothing is written into your photo folders. The cache is safe to delete at any time. A HEIC that can't be decoded is skipped like any other broken image.

Live Photos export as a pair: `IMG_1234.HEIC` plus a short `IMG_1234.MOV`. The HEIC shows up in photo slots. The `.MOV` is hidden from video slots by default (see below).

---

### iPhone videos (HEVC)

iPhones record video in HEVC (H.265), usually 10-bit HDR, which Chrome and most TV browsers (e.g. Samsung) can't play. PicMe converts these clips in the background to H.264 MP4, which plays everywhere:

- **Which clips are converted:** anything that isn't plain 8-bit H.264 — HEVC, HDR (HLG / Dolby Vision), ProRes, uncommon audio (e.g. iPhone 16 spatial audio) — plus H.264 clips larger than `video_max_edge`. Ordinary H.264 clips are streamed untouched.
- **Look:** HDR (HLG, PQ, Dolby Vision) is tone-mapped to standard range so clips don't look dark, flat or washed out, and portrait clips stay upright. PicMe generates a tone-mapping lookup table (BT.2100 → SDR with highlight roll-off) into its cache and applies it with ffmpeg's `lut3d` filter, which every ffmpeg build has. On a Mac, decoding and encoding use the GPU (VideoToolbox); SDR clips are converted entirely on the GPU.
- **Size:** converted clips are scaled down to `video_max_edge` (default 1920, i.e. 1080p; set in the editor under *Videos → Max video size*). Several 4K videos at once overwhelm most TV decoders. Clips are never scaled up, and frame rates above 60 fps (slo-mo) are capped at 60.
- **Background, one at a time:** conversion starts when PicMe starts (for every video slot's folder) and whenever a folder is opened in the editor or saved. It runs one clip at a time at low priority, shortest first. Until a clip is ready it's left out of the rotation; the display picks it up within a few minutes without a reload. The editor shows progress for the selected video slot, e.g. *"12 of 40 videos ready — converting IMG_1234.MOV (45%)"*.
- **Cache:** converted copies are stored next to the HEIC cache, in a `video` folder (`~/Library/Caches/PicMe/video` on macOS, `%LOCALAPPDATA%\PicMe\Cache\video` on Windows, `~/.cache/picme/video` on Linux, or `$PICME_CACHE_DIR/video`). Your originals are only ever read, and nothing is written into your video folders. Expect roughly 45 MB per minute of 1080p video. Copies whose original is deleted or edited are cleaned up at startup. The folder is safe to delete; it's rebuilt as needed.
- **Live Photos:** the 2–3 second `.MOV` an iPhone saves with each Live Photo would flicker by in a video slot, so a `.MOV`/`.MP4` of 4 seconds or less with the same name as a HEIC/JPEG in the same folder is hidden. A longer clip that happens to share a photo's name is still shown. Turn on *Include Live Photo clips* in the editor (`include_live_photos`) to show them anyway.
- **A clip that can't be converted** (e.g. a damaged file) is skipped, and the editor counts it. Replacing or editing the file retries it.

**Installing ffmpeg.** PicMe looks for `ffmpeg` and `ffprobe` on your `PATH`, then in Homebrew's folders (`/opt/homebrew/bin`, `/usr/local/bin`), so it's found even when PicMe is started at login.

- **macOS:** `brew install ffmpeg`
- **Windows:** `winget install Gyan.FFmpeg`, then open a new terminal (or log out and back in) so `PATH` picks it up. Or download the "essentials" build from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/), unzip it, and add its `bin` folder to `PATH`.
- **Anywhere:** set `PICME_FFMPEG` and `PICME_FFPROBE` to the full paths of the two programs.

If ffmpeg is missing, PicMe logs a warning, keeps playing H.264 clips, skips iPhone clips, and the editor shows how to install it. Once ffmpeg is installed PicMe picks it up within a few minutes (or when you open the video slot in the editor); no restart needed.

---

### Upgrading

After pulling new code, re-run the dependency install step to pick up any new packages:

> **HEIC support adds a new dependency, `pillow-heif`.** If you're upgrading from an earlier version, re-run the install below (on macOS, the `uv` command with the `DYLD_LIBRARY_PATH` export). It installs from a prebuilt wheel, so you don't need Homebrew's `libheif`. Until you do, the server won't start (`ModuleNotFoundError: No module named 'pillow_heif'`).

```bash
# Windows
.\.venv\Scripts\pip install -r requirements.txt

# macOS (use uv — see First-time setup)
export DYLD_LIBRARY_PATH="/opt/homebrew/opt/expat/lib"
uv pip install -r requirements.txt
```

---

## Running tests

Install the dev dependencies (`uv pip install -r requirements-dev.txt`), then from the project root:

```bash
.venv/bin/python -m pytest              # full suite, including browser tests
.venv/bin/python -m pytest -m "not e2e" # fast subset (skips Playwright browser tests)
```

The browser tests (`tests/e2e/`) drive a locally installed Chrome via Playwright and are skipped automatically if Chrome can't launch.

---

## Development Notes

- **Branch strategy:** Each milestone gets a feature branch (`milestone-1-core`, `milestone-2-metadata`, etc.). Merge to `main` when the milestone is complete and working.
- **Issues:** Use GitHub Issues to track individual tasks within each milestone. One issue per feature or bug.
- **No breaking changes in `main`:** `main` should always be a working, runnable app. Experimental work stays on branches.
- **Claude Code usage:** Open Claude Code in the project root. Hand it a specific task from the current milestone. Review what it produces before committing.

---

## Out of Scope (Intentionally)

- Mobile app (browser on TV is sufficient for v1–v3)
- Samsung SmartThings SDK / native TV app (adds significant platform complexity)
- Local face recognition ML (leverage cloud provider tags instead)
- Multi-user accounts (single-household app for now)
- Paid cloud storage of user photos (photos stay on user's own services)

---

*Project started: 2026*
*License: MIT (open source — others with Frame TVs will want this too)*
