"""yt-dlp library wrapper.

We call yt-dlp as a Python library (NOT the CLI) so we can hand-pick fields
and attach `progress_hooks` for streaming download progress. The options
below silence yt-dlp's own logging — any non-JSON byte on our stdout would
corrupt the JSONL protocol the Rust shell reads.
"""
from __future__ import annotations

import os
import threading
from typing import Any, Callable, Optional

from yt_dlp import YoutubeDL

# Bilibili rejects default yt-dlp User-Agent with HTTP 412. A real browser UA
# plus `Referer` AND `Origin` headers pointing back to bilibili.com is what
# bilibili's WAF requires for anonymous metadata fetches; without Origin the
# extractor still 412s on the JSON metadata endpoint (verified against current
# yt-dlp extractor). Cookie-gated content (member-only, 大会员 quality) still
# needs the user's own cookies — a future settings feature.
_DEFAULT_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
}


class CancelRequested(Exception):
    """Raised from inside a progress_hook to abort the current download.

    yt-dlp catches this and propagates it as a normal error, which we then
    surface to the UI as a `cancelled` status (not an error).
    """


def _get_ffmpeg_path() -> Optional[str]:
    """Return the full path of a portable ffmpeg binary that yt-dlp can use.

    yt-dlp needs ffmpeg to merge separate video+audio streams that Bilibili
    serves (the typical case for any quality above 360p). `imageio-ffmpeg`
    ships a portable binary cross-platform — using it means the user doesn't
    have to install ffmpeg system-wide. yt-dlp accepts a full executable
    path in `ffmpeg_location`, not just a directory (verified against the
    FFmpegPostProcessor source).
    """
    try:
        from imageio_ffmpeg import get_ffmpeg_exe

        return get_ffmpeg_exe()
    except Exception:
        return None


def parse_url(url: str) -> dict[str, Any]:
    opts: dict[str, Any] = {
        # Resolve playlist/collection containers but leave entries flat — we
        # only want titles/ids for the UI's "select episode" step.
        "extract_flat": "in_playlist",
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": False,
        "http_headers": _DEFAULT_HTTP_HEADERS,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return _shape(info or {})


def download_video(
    url: str,
    format_id: str,
    output_dir: str,
    progress_callback: Callable[[dict[str, Any]], None],
    cancel_event: threading.Event,
) -> Optional[str]:
    """Download `url` at `format_id` into `output_dir`.

    Returns the final on-disk path of the merged output file (yt-dlp's
    `requested_downloads[0].filepath`), or None if not available.

    `progress_callback` receives yt-dlp's raw hook dict; it should not block.
    To cancel, set `cancel_event` — the next progress tick raises
    `CancelRequested` which bubbles up through yt-dlp.
    """
    os.makedirs(output_dir, exist_ok=True)

    def _hook(d: dict[str, Any]) -> None:
        if cancel_event.is_set():
            raise CancelRequested()
        progress_callback(d)

    opts: dict[str, Any] = {
        "format": format_id,
        # When a format_id refers to a video-only or audio-only stream,
        # yt-dlp will fetch+merge the matching counterpart. The format
        # string `<id>+bestaudio/best` is the simplest way to express that.
        # We pass it verbatim; if the user picks a combined format, the +
        # suffix is harmless.
        "outtmpl": os.path.join(output_dir, "%(title)s [%(id)s].%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,         # silence yt-dlp's own progress text
        "http_headers": _DEFAULT_HTTP_HEADERS,
        "progress_hooks": [_hook],
        "noplaylist": True,         # single-video download; playlists are per-entry
    }

    ffmpeg_path = _get_ffmpeg_path()
    if ffmpeg_path:
        opts["ffmpeg_location"] = ffmpeg_path

    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        # yt-dlp puts the final merged path in `requested_downloads`.
        if isinstance(info, dict):
            reqs = info.get("requested_downloads") or []
            if reqs and isinstance(reqs[0], dict):
                return reqs[0].get("filepath")
            return info.get("_filename")
    return None


def _shape(info: dict[str, Any]) -> dict[str, Any]:
    """Trim a yt-dlp info dict to a UI-friendly subset.

    The raw dict is huge (tens of KB of internal fields); we forward only what
    the parser screen actually renders. Add fields here as the UI grows.
    """
    return {
        "title": info.get("title"),
        "uploader": info.get("uploader"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "webpage_url": info.get("webpage_url"),
        "is_playlist": info.get("_type") == "playlist",
        "entries": [
            {
                "id": e.get("id"),
                "title": e.get("title"),
                "duration": e.get("duration"),
                "url": e.get("url"),
            }
            for e in (info.get("entries") or [])
        ],
        "formats": [
            {
                "format_id": f.get("format_id"),
                "ext": f.get("ext"),
                "height": f.get("height"),
                "vbr": f.get("vbr"),
                "acodec": f.get("acodec"),
                "vcodec": f.get("vcodec"),
                "format_note": f.get("format_note"),
                "filesize": f.get("filesize") or f.get("filesize_approx"),
            }
            for f in (info.get("formats") or [])
        ],
    }
