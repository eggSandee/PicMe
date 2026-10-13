"""Video slot backend: listing/serving routes and the MP4/MOV container date parser."""
from __future__ import annotations

import datetime
import time
from pathlib import Path

import pytest

from metadata import video as video_module
from metadata.video import extract_video_date
from video_fixtures import (
    FTYP,
    h264_stub,
    box,
    large_box,
    mac_seconds,
    make_clip,
    mvhd,
    open_box,
    requires_ffmpeg,
)

UTC = datetime.timezone.utc
# 03:30 UTC — still the previous day in Los Angeles, already the same day in Tokyo
STAMP = datetime.datetime(2021, 6, 15, 3, 30, tzinfo=UTC)
STAMP_SECONDS = mac_seconds(STAMP)

# Arbitrary non-video payload served by /api/video — the route streams bytes, it doesn't decode
VIDEO_BYTES = h264_stub(1024)  # sniffs as plain H.264, so it is served untouched


@pytest.fixture
def set_tz(monkeypatch):
    """Switch the process timezone; restored (and re-applied via tzset) on teardown."""
    def _set(name: str) -> None:
        monkeypatch.setenv("TZ", name)
        time.tzset()

    yield _set
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def media_dir(tmp_path) -> Path:
    """A folder mixing videos, photos, AppleDouble dotfiles and non-media."""
    d = tmp_path / "media"
    d.mkdir()
    for name in ("clip.mp4", "HOLIDAY.MOV", "phone.m4v", "trip.mov"):
        (d / name).write_bytes(VIDEO_BYTES)
    (d / "._clip.mp4").write_bytes(b"\x00\x05\x16\x07AppleDouble")  # macOS resource fork
    (d / ".hidden.mov").write_bytes(VIDEO_BYTES)
    (d / "photo.jpg").write_bytes(b"\xff\xd8\xff not really a jpeg")
    (d / "notes.txt").write_text("secret")
    (d / "clip.avi").write_bytes(VIDEO_BYTES)  # video, but not a supported container
    (d / "folder.mp4").mkdir()  # directories never count, whatever their name
    return d


def _names(paths) -> set[str]:
    return {Path(p).name for p in paths}


# --- /api/images/list?kind=video ---

def test_list_video_kind_returns_only_videos(client, media_dir):
    r = client.get("/api/images/list", params={"folder": str(media_dir), "kind": "video"})
    assert r.status_code == 200
    body = r.json()
    assert _names(body["images"]) == {"clip.mp4", "HOLIDAY.MOV", "phone.m4v", "trip.mov"}
    assert body["count"] == 4
    assert all(Path(p).parent == media_dir for p in body["images"])


def test_list_video_kind_skips_dotfiles(client, media_dir):
    images = client.get("/api/images/list", params={"folder": str(media_dir), "kind": "video"}).json()["images"]
    assert not any(Path(p).name.startswith(".") for p in images)


def test_list_default_kind_is_photos(client, media_dir):
    r = client.get("/api/images/list", params={"folder": str(media_dir)})
    assert r.status_code == 200
    assert _names(r.json()["images"]) == {"photo.jpg"}


def test_list_explicit_photos_kind_excludes_videos(client, media_dir):
    r = client.get("/api/images/list", params={"folder": str(media_dir), "kind": "photos"})
    assert _names(r.json()["images"]) == {"photo.jpg"}


@pytest.mark.parametrize("kind", ["videos", "VIDEO", "audio", ""])
def test_list_unknown_kind_is_400(client, media_dir, kind):
    r = client.get("/api/images/list", params={"folder": str(media_dir), "kind": kind})
    assert r.status_code == 400


def test_list_video_kind_on_photo_only_folder_is_empty(client, photo_library):
    r = client.get("/api/images/list", params={"folder": str(photo_library.dir), "kind": "video"})
    assert r.json() == {"images": [], "count": 0, "pending": 0}


def test_list_video_kind_missing_folder_is_empty(client, tmp_path):
    r = client.get("/api/images/list", params={"folder": str(tmp_path / "nope"), "kind": "video"})
    assert r.json() == {"images": [], "count": 0, "pending": 0}


# --- /api/images/random?kind=video ---

def test_random_video_kind_returns_a_video(client, media_dir):
    videos = {"clip.mp4", "HOLIDAY.MOV", "phone.m4v", "trip.mov"}
    seen = set()
    for _ in range(40):
        r = client.get("/api/images/random", params={"folder": str(media_dir), "kind": "video"})
        assert r.status_code == 200
        seen.add(Path(r.json()["path"]).name)
    assert seen <= videos
    assert len(seen) > 1  # actually random, not a fixed pick


def test_random_video_kind_with_no_videos_is_404(client, photo_library):
    r = client.get("/api/images/random", params={"folder": str(photo_library.dir), "kind": "video"})
    assert r.status_code == 404


def test_random_unknown_kind_is_400(client, media_dir):
    r = client.get("/api/images/random", params={"folder": str(media_dir), "kind": "clips"})
    assert r.status_code == 400


def test_random_default_kind_is_photos(client, media_dir):
    r = client.get("/api/images/random", params={"folder": str(media_dir)})
    assert r.status_code == 200
    assert Path(r.json()["path"]).name == "photo.jpg"


# --- /api/video ---

@pytest.mark.parametrize("name", ["clip.mp4", "HOLIDAY.MOV", "phone.m4v"])
def test_video_full_get(client, media_dir, name):
    r = client.get("/api/video", params={"path": str(media_dir / name)})
    assert r.status_code == 200
    # .mov is deliberately labelled video/mp4 (TV browsers reject video/quicktime)
    assert r.headers["content-type"] == "video/mp4"
    assert r.headers["accept-ranges"] == "bytes"
    assert r.content == VIDEO_BYTES


def test_video_range_request_is_206(client, media_dir):
    r = client.get("/api/video", params={"path": str(media_dir / "clip.mp4")}, headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 0-99/{len(VIDEO_BYTES)}"
    assert r.headers["content-length"] == "100"
    assert r.content == VIDEO_BYTES[:100]


def test_video_open_ended_range(client, media_dir):
    r = client.get("/api/video", params={"path": str(media_dir / "trip.mov")}, headers={"Range": "bytes=1000-"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 1000-1023/{len(VIDEO_BYTES)}"
    assert r.content == VIDEO_BYTES[1000:]


def test_video_range_past_end_is_416(client, media_dir):
    r = client.get("/api/video", params={"path": str(media_dir / "clip.mp4")},
                   headers={"Range": f"bytes={len(VIDEO_BYTES) + 10}-"})
    assert r.status_code == 416
    assert r.headers["content-range"].endswith(f"*/{len(VIDEO_BYTES)}")


@pytest.mark.xfail(strict=True, reason="BUG (Starlette FileResponse): 416 sends 'Content-Range: */N' "
                                       "without the 'bytes' unit RFC 9110 requires")
def test_video_416_content_range_has_unit(client, media_dir):
    r = client.get("/api/video", params={"path": str(media_dir / "clip.mp4")},
                   headers={"Range": f"bytes={len(VIDEO_BYTES) + 10}-"})
    assert r.headers["content-range"] == f"bytes */{len(VIDEO_BYTES)}"


def test_video_missing_file_is_404(client, media_dir):
    r = client.get("/api/video", params={"path": str(media_dir / "gone.mp4")})
    assert r.status_code == 404


def test_video_directory_named_mp4_is_404(client, media_dir):
    r = client.get("/api/video", params={"path": str(media_dir / "folder.mp4")})
    assert r.status_code == 404


@pytest.mark.parametrize("name", ["photo.jpg", "notes.txt", "clip.avi"])
def test_video_rejects_non_video_files(client, media_dir, name):
    r = client.get("/api/video", params={"path": str(media_dir / name)})
    assert r.status_code == 400


def test_video_missing_path_param_is_422(client):
    assert client.get("/api/video").status_code == 422


def test_image_route_rejects_videos(client, media_dir):
    r = client.get("/api/image", params={"path": str(media_dir / "clip.mp4")})
    assert r.status_code == 400


# --- symlinks: both the requested name and the resolved target must be allowed ---

def test_video_symlink_named_mp4_to_text_file_is_400(client, media_dir):
    link = media_dir / "x.mp4"
    link.symlink_to(media_dir / "notes.txt")
    r = client.get("/api/video", params={"path": str(link)})
    assert r.status_code == 400
    assert b"secret" not in r.content


def test_image_symlink_named_jpg_to_text_file_is_400(client, media_dir):
    link = media_dir / "x.jpg"
    link.symlink_to(media_dir / "notes.txt")
    r = client.get("/api/image", params={"path": str(link)})
    assert r.status_code == 400
    assert b"secret" not in r.content


def test_video_symlink_named_txt_to_real_clip_is_400(client, media_dir):
    link = media_dir / "x.txt"
    link.symlink_to(media_dir / "clip.mp4")
    assert client.get("/api/video", params={"path": str(link)}).status_code == 400


def test_video_symlink_to_real_clip_is_served(client, media_dir, tmp_path):
    link = tmp_path / "link.mp4"
    link.symlink_to(media_dir / "trip.mov")
    r = client.get("/api/video", params={"path": str(link)})
    assert r.status_code == 200
    assert r.content == VIDEO_BYTES


def test_video_dangling_symlink_is_404(client, media_dir):
    link = media_dir / "dangling.mp4"
    link.symlink_to(media_dir / "does-not-exist.mp4")
    assert client.get("/api/video", params={"path": str(link)}).status_code == 404


# --- extract_video_date: handcrafted ISO-BMFF ---

def _write(tmp_path: Path, data: bytes, name: str = "clip.mp4") -> str:
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


@pytest.fixture
def utc(set_tz):
    set_tz("UTC")


@pytest.mark.parametrize("version", [0, 1])
def test_mvhd_versions(tmp_path, utc, version):
    data = FTYP + box(b"moov", mvhd(STAMP_SECONDS, version=version))
    assert extract_video_date(_write(tmp_path, data)) == "2021-06-15"


def test_mvhd_v1_beyond_32_bits(tmp_path, utc):
    # 2050 doesn't fit in mvhd v0's u32 field (wraps in 2040); v1 holds it, but it's in the future
    far = mac_seconds(datetime.datetime(2050, 1, 1, tzinfo=UTC))
    assert far > 0xFFFFFFFF
    assert extract_video_date(_write(tmp_path, FTYP + box(b"moov", mvhd(far, version=1)))) is None


def test_moov_before_mdat(tmp_path, utc):
    data = FTYP + box(b"moov", mvhd(STAMP_SECONDS)) + box(b"mdat", b"\x00" * 4096)
    assert extract_video_date(_write(tmp_path, data)) == "2021-06-15"


def test_moov_after_mdat(tmp_path, utc):
    data = FTYP + box(b"free") + box(b"mdat", b"\x00" * 4096) + box(b"moov", mvhd(STAMP_SECONDS))
    assert extract_video_date(_write(tmp_path, data)) == "2021-06-15"


def test_mvhd_after_other_moov_children(tmp_path, utc):
    moov = box(b"moov", box(b"udta", b"\x00" * 20) + box(b"trak", b"\x00" * 30) + mvhd(STAMP_SECONDS))
    assert extract_video_date(_write(tmp_path, FTYP + moov)) == "2021-06-15"


def test_mvhd_outside_moov_is_ignored(tmp_path, utc):
    data = FTYP + mvhd(STAMP_SECONDS) + box(b"moov", box(b"trak"))
    assert extract_video_date(_write(tmp_path, data)) is None


def test_largesize_mdat_before_moov(tmp_path, utc):
    data = FTYP + large_box(b"mdat", b"\x00" * 4096) + box(b"moov", mvhd(STAMP_SECONDS))
    assert extract_video_date(_write(tmp_path, data)) == "2021-06-15"


def test_largesize_moov(tmp_path, utc):
    data = FTYP + large_box(b"moov", mvhd(STAMP_SECONDS))
    assert extract_video_date(_write(tmp_path, data)) == "2021-06-15"


def test_size_zero_moov_runs_to_end_of_file(tmp_path, utc):
    data = FTYP + box(b"mdat", b"\x00" * 64) + open_box(b"moov", box(b"trak") + mvhd(STAMP_SECONDS))
    assert extract_video_date(_write(tmp_path, data)) == "2021-06-15"


def test_size_zero_mdat_hides_later_boxes(tmp_path, utc):
    # size==0 means "to end of file", so nothing can follow it — a moov after it is garbage
    data = FTYP + open_box(b"mdat", b"\x00" * 64 + box(b"moov", mvhd(STAMP_SECONDS)))
    assert extract_video_date(_write(tmp_path, data)) is None


def test_mdat_payload_is_seeked_not_read(tmp_path, utc, monkeypatch):
    # A multi-GB mdat before moov must cost a seek, not a read of its payload
    data = FTYP + box(b"mdat", b"\x00" * 1_000_000) + box(b"moov", mvhd(STAMP_SECONDS))
    path = _write(tmp_path, data)
    reads = []
    real_open = open

    def tracking_open(*args, **kwargs):
        f = real_open(*args, **kwargs)
        real_read = f.read

        class Proxy:
            def __getattr__(self, name):
                return getattr(f, name)

            def read(self, n=-1):
                reads.append(n)
                return real_read(n)

            def __enter__(self):
                f.__enter__()
                return self

            def __exit__(self, *exc):
                return f.__exit__(*exc)

        return Proxy()

    monkeypatch.setattr(video_module, "open", tracking_open, raising=False)
    assert extract_video_date(path) == "2021-06-15"
    assert reads and all(0 <= n <= 16 for n in reads)


def test_no_moov(tmp_path, utc):
    assert extract_video_date(_write(tmp_path, FTYP + box(b"mdat", b"\x00" * 64))) is None


def test_moov_without_mvhd(tmp_path, utc):
    assert extract_video_date(_write(tmp_path, FTYP + box(b"moov", box(b"trak", b"\x00" * 16)))) is None


@pytest.mark.parametrize("cut", [1, 4, 6])
def test_truncated_mvhd(tmp_path, utc, cut):
    # moov/mvhd headers claim the full box, but the file ends `cut` bytes into the mvhd payload
    full = FTYP + box(b"moov", mvhd(STAMP_SECONDS))
    mvhd_payload_start = len(FTYP) + 8 + 8
    assert extract_video_date(_write(tmp_path, full[:mvhd_payload_start + cut])) is None


def test_truncated_box_header(tmp_path, utc):
    assert extract_video_date(_write(tmp_path, FTYP + b"\x00\x00\x01")) is None


def test_truncated_largesize_header(tmp_path, utc):
    assert extract_video_date(_write(tmp_path, FTYP + b"\x00\x00\x00\x01moov\x00\x00")) is None


@pytest.mark.parametrize("bad_size", [1, 4, 7])
def test_corrupt_box_size_terminates(tmp_path, utc, bad_size):
    # sizes smaller than the header would loop forever if the parser didn't bail out
    if bad_size == 1:
        header = b"\x00\x00\x00\x01moov" + (4).to_bytes(8, "big")  # largesize smaller than 16
    else:
        header = bad_size.to_bytes(4, "big") + b"moov"
    data = FTYP + header + mvhd(STAMP_SECONDS)
    assert extract_video_date(_write(tmp_path, data)) is None


def test_empty_file(tmp_path, utc):
    assert extract_video_date(_write(tmp_path, b"")) is None


def test_not_a_video(tmp_path, utc):
    assert extract_video_date(_write(tmp_path, b"this is plain text pretending to be a clip")) is None


def test_missing_file(tmp_path):
    assert extract_video_date(str(tmp_path / "gone.mp4")) is None


def test_creation_time_zero_is_missing(tmp_path, utc):
    assert extract_video_date(_write(tmp_path, FTYP + box(b"moov", mvhd(0)))) is None


@pytest.mark.parametrize("when", [
    datetime.datetime(1904, 1, 2, tzinfo=UTC),
    datetime.datetime(1970, 1, 1, tzinfo=UTC),   # unix-epoch confusion
    datetime.datetime(1989, 12, 31, 23, 59, tzinfo=UTC),
])
def test_creation_time_before_1990_is_missing(tmp_path, utc, when):
    assert extract_video_date(_write(tmp_path, FTYP + box(b"moov", mvhd(mac_seconds(when))))) is None


def test_creation_time_1990_is_accepted(tmp_path, utc):
    when = datetime.datetime(1990, 1, 1, 12, tzinfo=UTC)
    assert extract_video_date(_write(tmp_path, FTYP + box(b"moov", mvhd(mac_seconds(when))))) == "1990-01-01"


def test_creation_time_in_future_is_missing(tmp_path, utc):
    future = datetime.datetime.now(UTC) + datetime.timedelta(days=30)
    assert extract_video_date(_write(tmp_path, FTYP + box(b"moov", mvhd(mac_seconds(future))))) is None


def test_creation_time_recent_is_accepted(tmp_path, utc):
    recent = datetime.datetime.now(UTC) - datetime.timedelta(hours=2)
    data = FTYP + box(b"moov", mvhd(mac_seconds(recent)))
    assert extract_video_date(_write(tmp_path, data)) == recent.strftime("%Y-%m-%d")


def test_creation_time_overflow_is_missing(tmp_path, utc):
    data = FTYP + box(b"moov", mvhd(0xFFFFFFFFFFFFFFFF, version=1))
    assert extract_video_date(_write(tmp_path, data)) is None


# --- UTC → server-local date ---

@pytest.mark.parametrize("tz, expected", [
    ("America/Los_Angeles", "2021-06-14"),  # 03:30 UTC = 20:30 PDT the day before
    ("Asia/Tokyo", "2021-06-15"),           # 03:30 UTC = 12:30 JST
    ("UTC", "2021-06-15"),
])
def test_date_is_reported_in_server_local_time(tmp_path, set_tz, tz, expected):
    set_tz(tz)
    data = FTYP + box(b"moov", mvhd(STAMP_SECONDS))
    assert extract_video_date(_write(tmp_path, data)) == expected


# --- real ffmpeg clips ---

@requires_ffmpeg
@pytest.mark.parametrize("faststart", [False, True], ids=["moov-at-end", "faststart"])
@pytest.mark.parametrize("name, codec", [("clip.mp4", "h264"), ("clip.mov", "h264"), ("hevc.mov", "hevc")])
def test_ffmpeg_clip_creation_time(tmp_path, utc, name, codec, faststart):
    clip = make_clip(tmp_path / name, codec=codec, creation_time="2021-06-15T03:30:00Z", faststart=faststart)
    assert extract_video_date(str(clip)) == "2021-06-15"


@requires_ffmpeg
def test_ffmpeg_clip_without_creation_time(tmp_path, utc):
    clip = make_clip(tmp_path / "plain.mp4")
    assert extract_video_date(str(clip)) is None


@requires_ffmpeg
@pytest.mark.parametrize("tz, expected", [("America/Los_Angeles", "2021-06-14"), ("Asia/Tokyo", "2021-06-15")])
def test_metadata_route_on_video_returns_date_only(client, tmp_path, set_tz, tz, expected):
    set_tz(tz)
    clip = make_clip(tmp_path / "dated.MOV", creation_time="2021-06-15T03:30:00Z")
    r = client.get("/api/metadata", params={"path": str(clip)})
    assert r.status_code == 200
    assert r.json() == {"date_iso": expected}


@requires_ffmpeg
def test_metadata_route_on_undated_video(client, tmp_path):
    clip = make_clip(tmp_path / "undated.mp4")
    r = client.get("/api/metadata", params={"path": str(clip)})
    assert r.status_code == 200
    assert r.json() == {"date_iso": None}


def test_metadata_route_on_garbage_video_does_not_fail(client, media_dir):
    r = client.get("/api/metadata", params={"path": str(media_dir / "clip.mp4")})
    assert r.status_code == 200
    assert r.json() == {"date_iso": None}


def test_metadata_route_on_missing_video_is_404(client, media_dir):
    assert client.get("/api/metadata", params={"path": str(media_dir / "gone.mov")}).status_code == 404
