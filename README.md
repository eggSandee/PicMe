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
- [x] No duplicate photos across slots — shuffled per-folder queue ensures a photo is never in two slots at once. When every slot ticks together and every photo is taken, a slot waits for another slot to finish its swap or keeps its current photo for that cycle; a photo repeats only when there are fewer usable photos than slots. (While a slot crossfades, its outgoing photo can still be fading out as another slot fades it in — that overlap is the crossfade, not a duplicate.)
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
- [x] Time-of-day mat/wall color sync — automatically shift colors between day and night (e.g. warm white during day, black at night)
- [x] Daily layout rotation: layout selection (choose which layouts are eligible to rotate)
- [x] Decorative picture frames (wood, gold, black gallery, brushed silver, white modern, rustic barnwood) using PNG assets and CSS `border-image`
- [x] Per-slot drag-to-reorder in the editor
- [x] Configurable metadata pill size and text size
- [x] Basic / Advanced editor mode — Basic hides less-common settings so the editor stays approachable for users who don't need full configurability (see [Editor modes](#editor-modes))
- [x] Media folders include their subfolders (opt-out globally or per slot; see [Subfolders](#subfolders))
- [x] Tap a photo on the display to see it larger with all its metadata (see [Photo details](#photo-details))

---

### Milestone 4.5 — Gallery Mat
*Goal: One big frame around the whole display, with the photos in openings cut into a single mat — some larger than others, some in fun shapes.*

- [x] Gallery mat mode: one outer frame (solid or any decorative style) and one mat; slots become openings separated only by mat (see [Gallery mat](#gallery-mat))
- [x] Mat colour, margin around the openings and gutter between them; optional bevel-cut edge on each opening
- [x] Six gallery layouts with asymmetric openings and preset shapes (`gallery-12`, `-feature`, `-mosaic`, `-cameo`, `-arches`, `-salon`)
- [x] Opening shapes: rectangle, rounded, oval, circle, arch, hexagon, heart — per layout, and per slot in the editor
- [x] Any layout can use separate frames or a gallery mat; daily rotation can mix both
- [x] Editor: Frames switch, Gallery Mat settings, shape picker, and a preview drawn by the display's own renderer
- [x] Layout files are checked when loaded; an invalid file is skipped instead of breaking the display
- [x] Polish: metadata as a caption on the mat (never over the photo), one pill size for every slot on screen, one Frame section for both modes, rotation checkboxes in Basic
- [ ] Later: custom opening sizes (a geometry editor), the `custom` layout, a scalloped frame style

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
| `gallery-12` | 12 | Gallery mat: one frame and mat, 4×3 equal openings |
| `gallery-feature` | 7 | Gallery mat: a large centre opening, four small, two wide below |
| `gallery-mosaic` | 6 | Gallery mat: a tall opening left, five mixed sizes right |
| `gallery-cameo` | 5 | Gallery mat: an arched centre opening between four ovals |
| `gallery-arches` | 3 | Gallery mat: three arched openings in a row |
| `gallery-salon` | 8 | Gallery mat, salon-style: mixed sizes, a heart, a circle, a hexagon |
| `custom` | 1–12 | User-defined via drag-and-drop in the editor (planned — any layout that passes the layout check renders as separate frames or a gallery mat) |

---

## Slot Configuration Reference

Each slot supports the following settings:

```json
{
  "id": 1,
  "mode": "photos",            // "photos" or "video"
  "source": "local",           // "local", "google_photos", "amazon", "icloud", "network"
  "source_path": "/Photos/Vacations",
  "include_subfolders": false, // Only for a slot with its own folder: overrides the global setting (optional)
  "cycle_seconds": 60,         // How often this slot advances (seconds)
  "group": "A",                // Slots in the same group cycle together (optional)
  "fill_mode": "contain",      // This slot's own Fit ("contain") or Fill ("cover"); omit to follow the global fill_mode (optional)
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
  "show_metadata": true,         // global kill switch — false hides all overlays on all slots instantly (the editor's Default Metadata → Show metadata)
  "date_format": "long",         // "long" (July 4, 2019) | "medium" (Jul 4, 2019) | "short" (7/4/2019) | "iso" (2019-07-04)
  "max_metadata_pills": 3,       // max overlays shown per slot (excluding date); omit for no limit
  "label_position": "auto",      // "auto" | "photo" | "mat" — pills on the photo or in a caption on the mat (see Metadata pills below)
  "label_style": "pill",         // "pill" | "caption" | "placard" | "whisper" — how the metadata looks (see Metadata label styles below)
  "fill_mode": "cover",          // "cover" = Fill (crop, no black bars) | "contain" = Fit (whole photo, bars); missing = contain (see Fit and Fill below)
  "tap_for_details": true,       // click, tap or Enter on a photo shows it larger with its metadata; false for kiosk screens; missing = true (see Photo details below)
  "frame_defaults": {            // mat and frame style applied to every slot; per-slot frame: {} overrides.
                                 // show_frame, frame_style and frame_color also style the gallery's outer frame
    "mat_color": "#000000",      // mat color behind the photo (any CSS color)
    "mat_width": 16,             // mat thickness in pixels
    "show_frame": true,          // show outer frame border
    "frame_color": "#2c2318",    // dark wood frame (any CSS color)
    "frame_width": 28,           // frame border thickness in pixels (28 is wood's own width)
    "frame_style": "wood"        // "solid" (uses frame_color) | "wood" | "gold" | "black" | "silver" | "white" | "barnwood" — decorative frames ignore frame_color
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
    "show_people": false,
    "text_size": "medium",       // "small" | "medium" | "large" | "x-large" — one size for every slot on screen (see Metadata pills below)
    "pill_size": "medium"        // "small" | "medium" | "large" — padding around the text
  },
  "video_max_edge": 1920,        // long-edge cap for converted videos: 1280 | 1920 (default, 1080p) | 3840 | 0 = original size
  "include_live_photos": false,  // true = also play the short .MOV clips saved with Live Photos
  "include_subfolders": true,    // media folders include their subfolders (see Subfolders below); false = top level only
  "layout_rotation": {           // show a different layout each day (see Daily layout rotation below)
    "enabled": false,
    "layouts": [],               // layout ids to rotate through; [] = every layout
    "change_at": "04:00"         // local time on the display when the next day's layout appears
  },
  "gallery": {                   // one frame and mat for gallery-mat layouts (see Gallery mat below); px at 1920x1080
    "mat_color": "#f4f1ea",
    "outer_margin": 64,          // mat between the frame and the openings (0–120)
    "gutter": 28,                // mat between neighbouring openings (0–80)
    "wall_margin": 12,           // wall around the frame (0–64)
    "bevel": true,               // a light bevel-cut edge around each opening
    "bevel_width": 4,            // (0–16)
    "bevel_color": "#fbfaf6",
    "frame_width": 48,           // the outer frame's width (0–160)
    "frame_override": false,     // true = the three below style the outer frame instead of frame_defaults
    "show_frame": true,
    "frame_style": "wood",       // "solid" or a decorative style, as in frame_defaults
    "frame_color": "#2c2318"     // solid frames only
  },
  "layout_modes": {}             // layout id -> "gallery" | "slots", e.g. { "classic-4": "gallery" }
}
```

Per-slot `metadata` and `frame` are both optional. If omitted, the slot inherits the global defaults. If present, only the fields listed override the defaults.

```json
{
  "id": 1,
  "source_path": "photos",
  "cycle_seconds": 30,
  "use_global": { "metadata": false },      // needed for the slot's own metadata settings to apply
  "metadata": { "show_camera": true, "text_size": "large" },  // inherits everything else from metadata_defaults
  "frame": { "mat_color": "#ffffff", "frame_style": "gold" },  // white mat and a gold frame on this slot only; inherits frame_defaults for the rest
  "shape": "heart"                           // this position's opening shape on gallery-mat layouts (see Gallery mat below)
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

#### Metadata pills

`text_size` and `pill_size` set how big the metadata pills are. Set them under **Default Metadata** in the editor, or per slot in the slot's **Metadata Overlays** section (in **Advanced** mode; turn **Use global** off first, as with the other metadata settings). Hand-edited values other than the ones below are treated as `"medium"`.

- **`text_size`** sets one size for every slot on screen, so pills look the same in a big opening and a small one. The text is 4% of the shorter side of the **smallest** pill area on screen (at least 14 px and at most 32 px), multiplied by the preset: `small` 0.8, `medium` 1, `large` 1.25, `x-large` 1.5. The result is always between 12 px and 48 px. At 1920×1080, classic-4 gets 20 px text with `medium`, about 13 mm tall on a 55" TV and comfortable from the couch. On a layout with a big hero slot and small ones, the big slot gets the same pills as the small ones. The smallest area is measured where the pills go: on the photo, the slot (or a shaped opening's inner part) inside the frame, less 10 px on each side; on the mat, the photo's opening. Slots that show no pills don't count.
- **A slot's own `text_size`** (with `use_global.metadata: false`) still wins: it scales the screen's size instead of the default, so a `large` slot is 1.25 × the size every other slot gets.
- **`pill_size`** sets the padding around the text, in proportion to the text: `small` is snug, `medium` matches the original look and `large` is roomier. Pills are always fully rounded.

**Label position** (`label_position`, **Default Metadata → Label position** in the editor) says where the pills go:

- **On the photo** (`"photo"`): the original overlay, a column in the photo's bottom-left corner. In a shaped gallery opening it sits in the part of the shape it can use.
- **On the mat** (`"mat"`): a caption under the photo, like a label in a gallery. The pills sit in one row, with the date first. On a gallery mat the row starts at the photo's left edge (for a heart, circle, hexagon or arch, the left edge of its bounding box), like pills at the bottom left of a photo; it may run on past a narrow shape into the mat beside it, but never past the opening's own share of the mat. On separate frames it is centred under the photo. They never cover the photo. On a gallery mat, each opening gets a band of mat under it, and the photo fits in the space above. On separate frames, the bottom mat of every slot grows by the band, like a bottom-weighted mat in real framing. The band is one pill tall, the same on every slot, and at most a quarter of the smallest slot's height.
- **Auto** (`"auto"`, the default): on the mat on gallery-mat layouts, on the photo on separate-frame layouts. On a rotation that mixes both, each day follows its own layout.

When a slot is too small for all its pills:

- `max_metadata_pills` applies first, exactly as before.
- On the photo: if the pills are still too tall, the lowest-priority ones (the last in the table above) are hidden until the rest fit. The date is never hidden and always stays at the bottom.
- In a caption: if the row is too wide, the lowest-priority pills are hidden until it fits, so captions stay crisp. If the last one left is long (a long location, say), it shrinks to about 6 characters and scrolls before it is hidden too. The date is never hidden; if it alone is too wide, it scrolls.
- Hidden pills come back if the window gets bigger.
- A pill whose text is too wide scrolls slowly (about 25 px a second) back and forth, pausing for 2 seconds at each end. Text is never cut off with "…". If the TV or computer is set to reduce motion, the text doesn't move and is clipped at the pill's edge instead.

**Upgrading:** `medium` pills are larger than the fixed 12 px pills of earlier versions. Choose **Small** for something closer to the old look.

#### Metadata label styles

`label_style` (**Default Metadata → Label style** in the editor, in Basic mode too) picks how the metadata looks. Each option in the editor is a small live preview on your current mat, and the layout preview draws the chosen style with sample text. The display switches as soon as the editor saves, without reloading.

| Style | Looks like | Where |
|-------|------------|-------|
| `pill` (default) | Today's dark pills | Where **Label position** says |
| `caption` | One serif line: the date in italic, then the other fields upright at the same size, with dark hairlines between | Always on the mat, under the photo |
| `placard` | A small printed museum label: a warm card with a hairline border and no shadow, the place in italic, then the date and details | Always on the mat, under the photo |
| `whisper` | Bare type at the bottom of the photo: the place in small capitals, a large date, the camera details underneath, over a soft dark gradient | Always on the photo |

- **The style decides where it goes.** Caption and placard are labels on the mat, and whisper is type on the photo, so **Label position** applies to pills only. A placard with `label_position: "photo"` still sits on the mat, for example. The editor greys out Label position and Pill size while another style is chosen and says why.
- **On the mat** (caption, placard): in the caption band under each photo, starting at the photo's left edge, on gallery mats and separate frames alike. The placard needs two lines, so its band is taller (at most 32% of the smallest slot's height, against 25% for pills and captions). It never touches the photo, the bevel, the frame or another opening.
- **Ink on the mat.** The caption's ink follows the mat: dark on light mats, light on dark mats. On a mid-tone mat, where neither reads well, it switches to pure black or white and a heavier weight. The text always has a contrast of at least 4.5:1 against the mat. The placard is a light card on any mat.
- **On the photo** (whisper): in the bottom-right corner of a rectangle. In a heart, circle, hexagon or arch it is centred low inside the part of the shape it can use, and the gradient is clipped to the shape. If the date alone is too wide there, the date shrinks to the size of the other lines, and then to 16 px, like a pill date that doesn't fit.
- **Fit.** With Fit (`contain`), labels follow the photo you see rather than the opening: caption and placard start at the visible photo's left edge, and a whisper and its gradient stay on the photo, off the bars. Pill captions on a gallery mat start there too. The editor's preview has no photo, so it shows them at the opening's edge.
- **Sizes.** One size per screen, as for pills: the style's text is the screen's pill size (text size included) times about 1.1, but never smaller than the size that keeps every line at least 16 px on a 1920×1080 screen, so it reads from the couch.
- **When it doesn't fit**, the lowest-priority fields are hidden first (the order in the table above), and the date is never hidden. A line that is still too wide scrolls slowly, like a long pill. A placard whose place is hidden makes the date its title. `show_metadata` and `max_metadata_pills` work as they do for pills, and video slots show the date only.
- **Font.** Caption and placard use [Source Serif 4](https://github.com/adobe-fonts/source-serif), bundled in `frontend/assets/fonts` (SIL Open Font License, `SourceSerif4-OFL.txt`), because TV browsers have no Georgia. Whisper uses the TV's sans-serif.
- **One style for the whole screen.** There is no per-slot style: a screen's labels share one caption band and one size, so mixing styles on one screen would break that.

To use a different photo folder (e.g. a folder elsewhere on your PC), set `source_path` to the full path:

```json
"source_path": "C:/Users/yourname/Pictures/Vacations"
```

#### Subfolders

A media folder includes the photos and videos in its subfolders too, however deeply nested (trips, years, albums). Slots shuffle through all of them as one list, with the same no-repeat and no-duplicate rules as before.

- **Turning it off.** In **Advanced** mode, turn off **Include subfolders** under the default media folder (`"include_subfolders": false`) to use only the files directly in the folder. A slot with its own folder (**Use global** off in its Media Source section) can set its own **Include subfolders** (`include_subfolders` in the slot). Slots that follow the default folder follow the global setting. The display switches within a few seconds of the change.
- **What's skipped.** Hidden files and folders (names starting with `.`, including macOS `._` files) are skipped. So are NAS and system folders such as `@eaDir`, `#recycle`, `$RECYCLE.BIN` and `System Volume Information`, and macOS packages: the Photos and Lightroom libraries, apps, and any folder with `Contents/Info.plist`. Pointing PicMe at `~/Pictures` never reads inside `Photos Library.photoslibrary`.
- **Links.** Symlinked folders (and Windows junctions) are not followed, so a link can't loop or pull in another tree. Symlinked files are shown as before.
- **Very large folders.** Listings stop after 100,000 photos/videos (`"media_max_files"`) or 32 folder levels (`"media_max_depth"`). Both are top-level settings in `config.json`, not shown in the editor. When a limit is hit the server logs a warning, and a video slot's editor panel says only part of the folder is used. The same files are used each time (folders in name order).
- **New files.** Folder listings are cached and re-checked cheaply: one check per folder, not per file. A folder of 50,000 photos lists in well under a second. New files are seen the next time a slot re-lists its folder (videos every few minutes). On some network drives, which report changes lazily, this can take up to 5 minutes. Saving in the editor always re-lists.

#### Frames

Each slot has a mat (`mat_color`, `mat_width`) around the photo or video and a frame around the mat. The mat always shows: the photo sits inside it, cropped there in **Fill** mode and letterboxed there in **Fit** mode.

`frame_style` picks the frame: `"solid"` draws a plain border in `frame_color`, while the decorative styles draw a moulding (`frame_color` is ignored for those):

| `frame_style` | Editor name | Default width | Look |
|---|---|---|---|
| `"wood"` | Wood | 28 px | Walnut cushion moulding, satin finish |
| `"gold"` | Gold | 36 px | Gilded torus, cove and bead row |
| `"black"` | Black gallery | 24 px | Matte soft-black flat face with a light hairline, so it still shows on a dark wall or at night |
| `"silver"` | Brushed silver | 16 px | Thin aluminium profile with a visible step and a brushed sheen |
| `"white"` | White modern | 24 px | Satin white with a tinted inner lip and a soft shadow, so it stands apart from an ivory mat |
| `"barnwood"` | Rustic barnwood | 36 px | Weathered reclaimed wood with cracks and a knot; each side is one long board stretched to fit, so nothing repeats along an edge |

Set it under **Frame** in the editor, per slot in the slot's Frame section, or per time-of-day period (**Same as default** keeps the slot's usual style). `frame_width` sets the moulding's thickness, too. Choosing a decorative style when the width is under 16 px sets it to that style's own default (the table above), since a thinner moulding loses its detail. The default widths are for separate frames; a gallery mat's outer frame keeps its own width (48 px unless changed under Gallery Mat), and about 30 px suits brushed silver there. The frame never takes more than a sixth of a small slot's shortest side, and corners keep their shape at any size. Time-of-day periods have their own frame width (8 px unless changed), so raise it there if a decorative frame looks thin at night.

**Frame assets.** The frame images in `frontend/assets/frames/` are original renders made by `tools/frames/generate_frames.py` (Pillow only, no third-party images; MIT like the rest of PicMe). It also writes `frames.js`, the list of styles the display and editor load, with each style's edge tile and how it fills an edge (`repeat`: `round` tiles it; barnwood's `stretch` stretches one long board). To change or add a style, edit the script and re-run it; `--check` verifies the committed files match, and `--preview /tmp/frames.png` writes a contact sheet to look at:

```bash
.venv/bin/python tools/frames/generate_frames.py
.venv/bin/python tools/frames/generate_frames.py --check
```

New installs start with wood frames (`frame_style: "wood"`, `frame_width: 28`). An existing `config.json` keeps the frame it has; to switch to wood, pick **Wood** in the editor's **Frame → Frame style** (a thin frame widens to 28 px by itself).

`.gitignore` ignores `*.png` to keep photos out of the repo, with an exception for `frontend/assets/frames/`.

#### Fit and Fill

A photo or video rarely has exactly its frame's shape. **Fit** (`"contain"`) shows all of it, with bars of the mat backing where the shapes differ. **Fill** (`"cover"`) fills the frame and crops what doesn't fit, so there are no black bars. This helps most with layouts whose frames have unusual shapes (a wide strip, say).

- **Fill frames.** The editor's Global Settings has **Fill frames (crop, no black bars)** (`"fill_mode"` at the top level). It applies to photos and videos, to separate frames and gallery mats, and inside shaped openings (a heart is filled edge to edge).
- **New installs fill.** The first-run config has `"fill_mode": "cover"`. A `config.json` from before this setting has no `fill_mode`, which means Fit, so an existing setup looks the same until you turn Fill frames on.
- **Per slot.** A slot can pick its own in its **Fit** section (Advanced): **Default** follows Fill frames, or choose Fit or Fill for that slot only (`"fill_mode"` in the slot; Default removes it). **Reset slots to global settings** puts every slot back on Default.
- **In the preview.** Each slot shows **Fit** or **Fill** under its number, next to its size (see [Editor layout](#editor-layout)).

#### Photo details

Click or tap a photo or video on the display to see it larger, with everything PicMe knows about it.

- **What it shows.** The whole photo, about 85% of the screen, over the dimmed collage. Gallery shapes and Fill don't apply here, so a heart opening's photo shows as the full rectangle. Beside it (under it on a portrait screen) are the photo's Date, Location, Camera, Megapixels, Aperture, Shutter speed, ISO and Focal length, and then its file name. Only the fields the file has are listed. A video shows its date. The list ignores the pill settings, so it shows everything even with **Show metadata** off.
- **TV remotes and keyboards.** The arrow keys move a white ring between the slots (the first press picks the top-left one), and Enter opens the ringed slot. The ring goes away after 45 seconds without a key press.
- **Videos keep playing.** A video loops in the details instead of moving on.
- **Cycling pauses.** While the details are open, that slot and the slots in its group keep their photos. The other slots carry on. Cycling resumes at the next change after closing.
- **Closing.** Click or tap outside the photo, press **×**, or press Esc, Backspace or the remote's Back button. With no input for 45 seconds it closes by itself, so a TV never gets stuck on it.
- **Turning it off.** For a kiosk screen, turn off **Tap a photo for details** in the editor's Global Settings (`"tap_for_details": false`). The display picks the change up without reloading.

#### Gallery mat

Instead of a frame around every slot, a layout can show **one frame around the whole display and one mat** filling it, with each slot an opening cut into the mat — like a multi-photo frame from a shop. The openings are separated only by mat.

- **Which layouts.** The `gallery-*` layouts are designed for it: some openings are bigger than others, and some have shapes. Any other layout can use it too: pick the layout and set **Frames** (under the layout picker) to **Gallery mat**, which saves `"layout_modes": { "classic-4": "gallery" }`. **Separate** sets a gallery layout back to a frame per slot (empty cells then show the wall, and shapes are ignored). With daily rotation on, Advanced mode adds a **Mat** chip on each layout, so a rotation can mix gallery-mat days and separate-frame days.
- **One frame control.** The outer frame's style, colour and on/off come from the editor's **Frame** section (`frame_defaults`), the same controls as separate frames, so the frame you set is the frame you see in either mode. The Frame section says so on a gallery screen. To give gallery mats a different frame, turn on **Use a different frame for gallery mats** under Gallery Mat (Advanced, `"frame_override": true`); its own style, colour and on/off then apply to the gallery. Mat colour, mat width and frame width in the Frame section are for separate frames only. When nothing the display can show uses a Frame control (for example mat width on a gallery-only screen), it is greyed out with the reason under it.
- **Mat and sizes.** The **Gallery Mat** section in the editor (or `"gallery"` in `config.json`) sets the mat colour, and in Advanced mode the outer frame width (`frame_width`, separate from slots' frame width because it frames the whole screen), the mat around the openings (`outer_margin`), the mat between openings (`gutter`), the wall around the frame (`wall_margin`) and the bevel. Sizes are px on a 1920×1080 screen. Gallery mode is designed for TVs and 1080p-or-larger monitors. On a smaller screen the margins, gutter and frame shrink somewhat (margin up to 12%, gutter 8%, frame a sixth of the shortest side), but the mat still takes a large share of the screen, so small displays are better served by a regular layout with fewer slots.
- **Bevel.** Each opening gets a thin light edge, like the slanted cut of real mat board: the same on every side, with no shadow on the photo. A faint, even line where the cut meets the mat keeps it visible on a white or light mat. Turn it off with **Bevelled openings** (`"bevel": false`).
- **Shapes.** `rect` (default), `rounded`, `ellipse` (oval), `circle`, `arch`, `hexagon` and `heart`. A gallery layout can give an opening a shape; pick **Opening shape** in a slot's settings to change it (`"shape"` in the slot), or **Layout default** to go back. Circles, hexagons and hearts keep their proportions and sit centred in their space; arches are never much wider than tall. The photo or video fills the shape; **Fill** usually looks best, since **Fit** keeps the whole photo inside the shape's box and the corners outside the shape are hidden.
- **Shapes stay with the position.** The shape is the hole in the mat, not part of the photo, so dragging slots to swap them moves their photos and settings but not the shapes (see [Rearranging slots](#rearranging-slots)). **Reset slots to global settings** leaves shapes alone too.
- **Pills.** By default (label position **Auto**) the metadata is a caption on the mat under each opening, so it never covers a heart or a circle; see [Metadata pills](#metadata-pills). With **On the photo**, pills sit inside the shape and are fitted to the part of it they can use: a small heart shows fewer pills (never dropping the date), and long text scrolls inside the shape as on any slot.
- **Upgrading.** The outer frame now follows the Frame section. If you had picked a different gallery frame (`gallery.frame_style`), it is kept but no longer used. To get it back, turn on **Use a different frame for gallery mats**, or pick the same style in the Frame section.
- **Time of day.** Day and night periods can change the gallery's mat colour, frame colour and frame style; widths stay as set in the Gallery Mat section. See [Time of day](#time-of-day).
- **Per-slot frames.** A slot's own Frame settings apply on separate-frame layouts and are ignored on gallery-mat days (there is one mat). Fit / Fill applies in both.
- **Videos in shapes.** Video slots work in any opening. Rectangles are never clipped; a shaped video opening is, which a TV may draw less efficiently — prefer rectangles for video on older TVs. As with any layout, keep the number of video slots small (`gallery-12` has 12 openings).
- **Custom layouts.** Layout files are checked when the server loads them (fr-only tracks, 1–12 slots numbered in order, no overlaps, every cell covered unless `"gallery": true`, known shapes); a file that fails is skipped with a warning in the server log rather than breaking the display. Custom opening sizes and the `custom` layout are planned for later.

#### Time of day

Turn on **Time-of-day colors** under Global Settings in the editor (Advanced) and choose **Configure day / night colors…** to give the day and the night their own look. `day_start` (07:00) and `night_start` (20:00) are the display's local times.

- **What a period changes.** Each period can set the wall colour, mat colour, mat width, frame on/off, frame style, frame colour and frame width. A period only changes the keys it sets; a slot's own frame settings still win. On gallery-mat layouts only the colours and the style apply (widths stay as set under Gallery Mat).
- **Same as default.** Every colour (wall, mat, frame) and the frame style has a **Same as default** option. It leaves the key out of the template (`"night": { "wall_color": "#000000" }` with no `mat_color`), so the colour set under Global Settings, Frame or Gallery Mat shows in that period.
- **New installs and Reset.** Day and night change the wall and frame colours (warm white wall and walnut frame by day, black wall and near-black frame at night). The mat is left at **Same as default**, so the mat you pick is the mat you see all day: a mat suits the photos more than the room's light. Saved configs keep their templates; one from an earlier version may still set a mat colour for each period.
- **The editor says when a period overrides you.** While time of day is on, a control whose colour the current period replaces gets a note under it, for example *Night time-of-day is showing #3a3a3a until 07:00 · Edit · Use my colour*. This covers the wall colour, the Frame section's mat colour, frame style and frame colour, and the Gallery Mat colour and frame. **Edit** opens the templates; **Use my colour** (or **Use my style**) sets that key to **Same as default** for this period, and open displays change within a few seconds. The note is shown in Basic too; there the frame style's note also names the period's frame colour, since Basic hides the colour. The editor works out the period the same way as the display, and updates the note within a minute when the period changes.

#### Daily layout rotation

Turn on **Rotate layout daily** in the editor's Layout section. A small checkbox appears in the bottom-right corner of every layout thumbnail, in Basic and Advanced mode: tick the layouts to rotate through. Ticking a box doesn't change the base layout; clicking the thumbnail does. **Select all** and **Deselect all**, on the right of the Layout heading, tick every layout or only the base layout (at least one must stay ticked, and the status line asks for a second). Both save straight away. The display shows one of the ticked layouts each day:

- **Which layout:** the ticked layouts take turns in the order the editor lists them, so two days in a row never show the same layout (when 2 or more are ticked). Ticking every layout is saved as `"layouts": []`, which also includes layouts added later. With only one ticked, that layout is shown every day.
- **When it changes:** at `change_at` (default 04:00) by the display's own clock and time zone. An open display switches by itself within 30 seconds, and a TV that was asleep switches as soon as it wakes. Before `change_at` it is still the previous day's layout.
- **Stays the same all day:** reloading the page, opening a second TV or opening the editor gives the same layout for the same day. The editor marks today's layout with a **Today** badge and says which layout comes next. Changing the ticked layouts can change today's layout straight away.
- **Base layout:** clicking a layout still sets `layout`, which is shown when rotation is off and is the layout the editor preview shows.
- **Slots:** slot *N* is always the same slot on every layout. A layout with fewer slots shows slots 1 to its slot count; the rest are kept with their settings and come back on days with a bigger layout. When rotation is on, the editor creates slots up to the biggest ticked layout (as photo slots using the global settings) and lists the extra slots under the preview so you can edit them.
- **Video slots and groups:** a video slot plays only on days its position is visible, but its folder is still converted in the background so it's ready. A larger layout can show more video slots at once, so keep their number small. A group only cycles the slots that are visible that day, at the timer of its first visible slot.
- **Gallery mat days:** a rotation can mix gallery-mat and separate-frame layouts; each day uses that layout's Frames setting (see [Gallery mat](#gallery-mat)). Rotating through every layout (`"layouts": []`) includes the gallery layouts.
- **Old configs:** `"daily_layout_rotation": true` from earlier versions still works (all layouts, switching at midnight). The editor converts it to `layout_rotation` the next time it saves.

#### Rearranging slots

To move a slot's content to another position, drag it onto that slot in the editor's layout preview; the two slots swap all their settings (mode, folder, frame, metadata, timer, group, fill and video options). Slot numbers stay with the positions, so slot 1 is still `config.slots[0]`. So do gallery-mat opening shapes: a heart-shaped opening stays a heart and shows whatever is dragged into it. On a phone or tablet, press and hold a slot for a moment, then drag; a quick swipe scrolls the page as usual. With daily rotation on, drop a slot on one of the **Edit slot N** buttons under the preview to swap it with a slot that only shows on some days (or drag the button onto the preview). Dragging back undoes a swap. The change saves automatically, and open displays pick it up within a few seconds: a swap that changes a visible slot's mode, timer, group or video folder reloads the display, anything else is applied in place.

#### Editor modes

The switch at the top of the editor's sidebar picks **Basic** or **Advanced**. Basic shows the settings most people change; Advanced shows everything.

- **A view preference only.** Hidden settings keep their values and still apply on the display. Switching modes never changes `config.json`.
- **Per browser.** The choice is saved in the browser (`localStorage`), not in the config, so each person's editor remembers its own mode. A browser that has never chosen starts in Basic. If the browser blocks storage, the editor still works and starts in Basic each time.
- **Advanced values in Basic.** When the config sets advanced settings away from their defaults, Basic shows **"N advanced settings active · Show"** under the switch. Hover it for the list; **Show** switches to Advanced.

The sidebar runs, top to bottom: Global Settings, Layout, Frame, Gallery Mat, Default Metadata, Videos and Reset. Which settings each mode shows (the split lives in one table, `EDITOR_SETTINGS` in `frontend/editor/index.html`, so it is easy to change):

| | Basic | Advanced adds |
|---|---|---|
| **Layout** | Layout picker, Frames (Separate / Gallery mat), Rotate layout daily, the rotation checkboxes on the thumbnails, Select all / Deselect all, today's rotation status | Switch layout at, Mat chips (frames per rotated layout) |
| **Global** | Save as my preset, Apply my preset, Undo, Default media folder, Wall color, Fill frames, Tap a photo for details | Include subfolders, Time-of-day colors (on / off and Configure day / night colors…) |
| **Frame** | Mat color, Frame style | Mat width, Show frame border, Frame color, Frame width |
| **Gallery Mat** | Mat color (the frame's style is in Frame) | Use a different frame for gallery mats (its style, show frame, colour), outer frame width, mat around and between the openings, wall around the frame, bevel |
| **Metadata** | Show metadata; Text size, Pill size, Label position and the fields (Date, Location, Camera…) | Date format, Max metadata pills |
| **Videos** | — | The whole section (max video size, Live Photo clips) |
| **Reset** | Reset everything to defaults | Reset slots to global settings |
| **Preview** | Each slot's size (aspect ratio) and Fit / Fill | — |
| **Each slot** | Media folder, Photos / Video (and the "no videos" warning), Change every (seconds), Opening shape, Show metadata | Use global toggles, Include subfolders, Group, Fit / Fill (the slot's own), Frame, the slot's own metadata settings, video loops and audio, conversion status |

In Basic, a slot's media folder is always editable: typing a different folder gives the slot its own folder, and clearing it or typing the default folder (a trailing `/` makes no difference) makes it follow the default again. Like every folder box, it is saved when you press Enter, leave the box or pick a folder with **Browse…**, not while you type. A slot that follows the default shows the default folder, also right after the default changes. Picking Wood or Gold widens a thin frame to suit it; going back to Solid restores the previous width unless you changed it in between.

#### Greyed-out settings

A setting that changes nothing in the current state is greyed out, both in Basic and in Advanced: dimmed, not clickable (a not-allowed cursor) and with a short reason under it, for example *Color applies to solid frames only.* It comes back as soon as the setting that decides it changes. Greying is a view only: values are kept, nothing in `config.json` changes, and the "N advanced settings active" count is the same. Folded sections stay folded. A time-of-day note under a greyed colour keeps its **Edit** and **Use my colour** links.

| Setting | Greyed when |
|---|---|
| Frame color (Frame section, a slot's Frame, the gallery's own frame, each time-of-day period) | Every frame it would colour is decorative (wood, gold, black, silver, white, barnwood) |
| Frame style, color and width (Frame section, a slot's Frame, the gallery's own frame) | No frame shows: **Show frame border** (or the gallery's **Show frame**) is off. A time-of-day period that turns the frame on counts, so these stay live then |
| Frame section: Mat color, Mat width, Frame width | No separate-frame layout is on screen (base layout, plus rotated ones) |
| Frame section: Show frame border, Frame style, Frame color | The screen is gallery-only and the gallery uses its own frame |
| Gallery Mat (the whole section) | No gallery-mat layout is on screen; the reason says how to get one |
| Bevel width, Bevel color | **Bevelled openings** is off |
| Label position | The label style places itself: caption and placard always sit on the mat, corner whisper on the photo |
| Pill size (Default Metadata, a slot's metadata) | The label style isn't Pills (the other styles size from Text size alone) |
| A slot's Show metadata | **Show metadata** in Default Metadata is off |
| A slot's Media folder, Frame and metadata settings | Its **Use global** is on (Advanced; in Basic the folder is always editable) |
| A slot's Frame (the whole section) | No separate-frame layout is on screen |
| A slot's Opening shape | No gallery-mat layout is on screen |
| A slot's Change every and Group | It is a video slot (videos advance after their loops) |
| Switch layout at, and the rotation checkboxes on the thumbnails | **Rotate layout daily** is off |
| Time-of-day templates (times, colours, frames) | Time of day is off; the template window has its own **Enabled** switch |
| A period's Frame style, color and width | That period shows no frame (its **Show frame** is off); its mat and frame widths also need a separate-frame layout |
| A period's colour picker | **Same as default** is ticked (the picker shows the colour in use) |

Settings that only exist for one kind of slot are hidden rather than greyed: a photo slot has no video loops or audio. The same goes for disclosures you open yourself (the gallery's own frame shows with **Use a different frame for gallery mats**; Default Metadata's fields fold away with **Show metadata** off). In the code, one helper, `setInactive` in `frontend/editor/index.html`, sets both the `disabled` attribute and the `.field-inactive` look with the reason, so the two can't drift apart.

#### Editor layout

- **Resize the preview.** Drag the bar between the layout preview and the slot settings to give either more room; the preview keeps the screen's 16:9 shape, so it is still the display, smaller. On a touch screen drag it with a finger; with the keyboard, focus it (Tab) and use the arrow keys, Home (smallest preview) or End (largest). Double-click it to go back to the default (the preview takes 60% of the height, never more than its full width needs). The slot settings scroll on their own below it.
- **Fold sidebar sections.** Click a section's title (Global Settings, Layout, Frame, Gallery Mat, Default Metadata, Videos, Reset) to fold it; click again to open it. Every section starts open. The links beside the Layout title (Select all / Deselect all) work whether it is folded or not. Videos, which Basic hides, stays hidden in Basic, folded or not. A fold saved for the old Time-of-Day or My Settings section (both now part of Global Settings) is forgotten.
- **Remembered per browser.** The divider position and folded sections are saved in the browser (`localStorage`), like the editor mode, never in `config.json`.
- **Slot sizes.** Under each slot's number the preview shows its photo opening's aspect ratio and whether it fits or fills, in smaller type: **3:2 · Fill**. It is the opening on a 1920×1080 screen after the frame, mat, caption band, gallery margins, shape and bevel, written width:height (so portrait reads **2:3**, like a 4×6 print held upright). Openings within about 12% of a common photo shape (1:1, 4:3, 3:2, 16:9, 5:4 and 7:5, either way round) show that shape's name, so a slot a little wider than 4:5 still reads **4:5**; anything else shows its own ratio, such as **2.4:1**. The tags are plain grey text, like the slot number. Hover a slot's tag for its size in px. Opening shapes are measured by their box. Small slots show the ratio above Fit / Fill.

#### Presets and reset

The preset buttons at the top of **Global Settings**, the first section in the sidebar, save and restore your settings; the two resets sit in the **Reset** section at the bottom:

- **Save as my preset** saves the current look: layout, rotation, Frames per layout, wall colour, Fill frames, Tap a photo for details, frame, gallery mat, metadata, label position and style, time of day, and each slot's frame, metadata, timer, group, Fit / Fill, opening shape and video loops and audio. It asks before replacing a saved preset.
- **Apply my preset** puts that look back. The dialog says how many settings change and lists them. Anything the preset names that no longer exists (a removed layout, an unknown shape) is skipped and listed in the dialog; the current layout stays then.
- **Reset everything to defaults…** puts every look setting back to the first-run defaults: Classic 4, daily rotation off, wood frames, Fill frames on, Tap a photo for details on, every slot on the global frame and metadata, 30 s timers. The dialog says how many settings differ from the defaults (counting what the display shows differently).
- **Kept by both.** Your default media folder, each slot's folder and photo/video mode, Include subfolders, and the video settings (max video size, Live Photo clips), so the display never goes blank and the video library isn't converted again. All slots are kept too, including ones the layout doesn't show, so a bigger layout later still has their folders.
- **Undo.** After a reset or applying the preset, an **Undo** link under the preset buttons restores the config from just before, exactly. There is one step of undo: it is used up once undone, and a second reset replaces it. Edits made after the reset are lost by Undo, and its dialog says so.
- **From any device.** Presets, the reset and Undo run on the server: `presets.json` and `config.backup.json` sit next to `config.json` (both ignored by git), so another device's editor sees the same preset and can undo the same reset.
- **Two different resets.** **Reset slots to global settings** (Advanced, in Reset) only clears each slot's own overrides — folder, frame, metadata and Fit / Fill — and changes nothing else. **Reset everything to defaults** resets the look and keeps the folders.

`presets.json` is a list, so more named presets can be added later without changing the file: `{"version": 1, "presets": [{"id": "my-preset", "name": "My preset", "created": "…", "updated": "…", "settings": {…}}]}`. The editor uses the one with id `my-preset`. A preset saves what the display showed: a config from before `fill_mode` saves Fit, for example. A missing or broken `presets.json` reads as no preset, and saving rewrites it.

**Show metadata.** The **Default Metadata** section and each slot's **Metadata Overlays** section open and close with a **Show metadata** switch (instantly if the computer is set to reduce motion):

- **Default Metadata** controls `show_metadata`. Off hides every pill on every slot and keeps all the other metadata settings. While it is off, each slot's switch is greyed out.
- **A slot's switch** is on when the slot shows at least one pill (for a video slot, the date). Turning it off sets `use_global.metadata: false` and every `show_*` field to `false` for that slot; text and pill size are kept. Turning it on removes the slot's `show_*` fields so it uses the default fields again. If those show nothing (for example no field is on in Default Metadata), it turns the date on for that slot. A slot's own field choices made in Advanced are not remembered after turning it off and on again.

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
- **Background, one at a time:** conversion starts when PicMe starts (for every video slot's folder, including its subfolders unless they're turned off) and whenever a folder is opened in the editor or saved. It runs one clip at a time at low priority, shortest first. Until a clip is ready it's left out of the rotation; the display picks it up within a few minutes without a reload. The editor shows progress for the selected video slot, e.g. *"12 of 40 videos ready — converting IMG_1234.MOV (45%)"*.
- **Cache:** converted copies are stored next to the HEIC cache, in a `video` folder (`~/Library/Caches/PicMe/video` on macOS, `%LOCALAPPDATA%\PicMe\Cache\video` on Windows, `~/.cache/picme/video` on Linux, or `$PICME_CACHE_DIR/video`). Your originals are only ever read, and nothing is written into your video folders. Expect roughly 45 MB per minute of 1080p video. Copies whose original is deleted or edited are cleaned up at startup. The folder is safe to delete; it's rebuilt as needed.
- **Live Photos:** the 2–3 second `.MOV` an iPhone saves with each Live Photo would flicker by in a video slot, so a `.MOV`/`.MP4` of 4 seconds or less with the same name as a HEIC/JPEG in the same folder is hidden (a photo with that name in another subfolder doesn't count). A longer clip that happens to share a photo's name is still shown. Turn on *Include Live Photo clips* in the editor (`include_live_photos`) to show them anyway.
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

To check for duplicate photos across slots, sample only each slot's decoded `img.visible` (or the display's `displayedPaths` claims), never both `<img>`s or anything with opacity > 0 — the outgoing image of a crossfade would count as a duplicate. See the comment above `SAMPLE_SLOTS` in `tests/e2e/test_browser.py`.

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
