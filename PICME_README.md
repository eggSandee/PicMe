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

- [ ] FastAPI backend that serves images from a local folder
- [ ] Fullscreen browser display with configurable slot grid
- [ ] Independent per-slot cycling timers
- [ ] Slot grouping (define groups that cycle together)
- [ ] 3–5 built-in layout templates (e.g., 2x2, 3x2, asymmetric hero)
- [ ] Basic config file (auto-managed JSON)

**Definition of done:** Open a browser on a TV, see a collage of local photos cycling independently.

---

### ✅ Milestone 2 — Metadata Overlays
*Goal: Optionally show photo context without cluttering the image.*

- [ ] EXIF date extraction from image files
- [ ] GPS coordinate extraction + reverse geocoding to city/region names
- [ ] Per-slot metadata overlay toggle (date, location, or both)
- [ ] Global metadata toggle (show/hide all overlays at once)
- [ ] Overlay styling: subtle, non-intrusive (semi-transparent pill at corner)

**Notes:**
- Reverse geocoding uses a free API (e.g., OpenStreetMap Nominatim) — no API key required
- Photos without GPS data simply show no location overlay
- Date format is user-configurable (e.g., "July 4, 2019" vs "7/4/19")

---

### ✅ Milestone 3 — Web Editor UI
*Goal: Configure everything without touching a config file.*

- [ ] Visual layout editor — see slot arrangement, click to configure each slot
- [ ] Per-slot settings: source folder, cycle timer, group assignment, metadata toggle
- [ ] Layout template picker with visual previews
- [ ] Live preview of changes before applying
- [ ] Settings saved automatically to config.json

---

### ✅ Milestone 4 — Video Slot Support
*Goal: Any slot can play a video on loop instead of cycling photos.*

- [ ] Per-slot mode toggle: photo cycling vs. video loop
- [ ] Video plays muted by default (optional audio toggle)
- [ ] Video loops for a configurable number of times before the slot advances
- [ ] Supported formats: MP4, MOV (browser-native playback)

---

### ✅ Milestone 5 — Google Photos Integration
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

### ✅ Milestone 6 — Additional Photo Sources
*Goal: Expand to other cloud providers.*

- [ ] Amazon Photos (note: API access is limited; may require workarounds)
- [ ] iCloud Photos (note: no official API; options include iCloud shared albums or local sync via iCloud Drive on Windows)
- [ ] Network folder / NAS (SMB/UNC path support)
- [ ] Source health indicators in the editor (connected / disconnected / error)

---

### ✅ Milestone 7 — Face Recognition & People Tags
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
    "show_people": false
  },
  "video_loops": 2             // For video mode: how many loops before advancing
}
```

---

## Tech Stack

| Layer | Technology | Reason |
|---|---|---|
| Backend | Python 3.11 + FastAPI | Simple, fast, great for file serving and APIs |
| Frontend display | Vanilla HTML/CSS/JS | No build step, runs on any browser including TVs |
| Frontend editor | React (lightweight) | Component model suits the slot editor UI |
| Photo metadata | `Pillow` + `piexif` | EXIF extraction from local files |
| Geocoding | OpenStreetMap Nominatim | Free, no API key required |
| Config storage | JSON file | Human-readable, no database needed for v1 |
| Auth (Google) | OAuth 2.0 via `google-auth` | Standard library, well documented |

---

## Running the App

```bash
# Install dependencies
pip install -r requirements.txt

# Start the server
python backend/main.py

# Open display on TV
http://[your-pc-ip]:8000/display

# Open editor on any device
http://[your-pc-ip]:8000/editor
```

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
