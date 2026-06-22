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


def _apply_cookies(opts: dict[str, Any], cookies_from_browser: Optional[str]) -> None:
    """Wire yt-dlp's cookiesfrombrowser into `opts` if a browser is given.

    yt-dlp expects a tuple `(browser_name, profile, keyring, container)`; we
    pass just the browser name (most common case). Empty/None means no cookies.
    Supported browsers: chrome, chromium, brave, edge, firefox, opera, safari,
    vivaldi, whale. Anything else is silently ignored — the download just runs
    anonymous, same as before.
    """
    if not cookies_from_browser:
        return
    if cookies_from_browser not in {
        "chrome", "chromium", "brave", "edge", "firefox",
        "opera", "safari", "vivaldi", "whale",
    }:
        return
    # yt-dlp accepts a tuple here; only the first element is required.
    opts["cookiesfrombrowser"] = (cookies_from_browser,)


def parse_url(url: str, cookies_from_browser: Optional[str] = None) -> dict[str, Any]:
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
    _apply_cookies(opts, cookies_from_browser)
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return _shape(info or {})


def download_video(
    url: str,
    format_id: str,
    output_dir: str,
    progress_callback: Callable[[dict[str, Any]], None],
    cancel_event: threading.Event,
    cookies_from_browser: Optional[str] = None,
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

    _apply_cookies(opts, cookies_from_browser)

    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        # yt-dlp puts the final merged path in `requested_downloads`.
        if isinstance(info, dict):
            reqs = info.get("requested_downloads") or []
            if reqs and isinstance(reqs[0], dict):
                return reqs[0].get("filepath")
            return info.get("_filename")
    return None


def _entry_title(e: dict[str, Any], idx: int) -> Optional[str]:
    """Best-effort title for a flat-extracted entry.

    yt-dlp's `extract_flat="in_playlist"` populates `url` but typically leaves
    `title`/`id` empty for Bilibili anthology entries. We fall back to the
    `?p=N` page index parsed from the URL so the UI shows something useful.
    """
    if e.get("title"):
        return e["title"]
    url = e.get("url") or ""
    if "?p=" in url:
        p = url.split("?p=", 1)[1].split("&", 1)[0]
        return f"P{p}"
    return f"第 {idx + 1} 集"


def _shape(info: dict[str, Any]) -> dict[str, Any]:
    """Trim a yt-dlp info dict to a UI-friendly subset.

    The raw dict is huge (tens of KB of internal fields); we forward only what
    the parser screen actually renders. Add fields here as the UI grows.
    """
    raw_entries = info.get("entries") or []
    return {
        "title": info.get("title"),
        "uploader": info.get("uploader"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "webpage_url": info.get("webpage_url"),
        "is_playlist": info.get("_type") == "playlist",
        "entries": [
            {
                "id": e.get("id") or (e.get("url") or "").split("?p=", 1)[-1] or f"_idx_{idx}",
                "title": _entry_title(e, idx),
                "duration": e.get("duration"),
                "url": e.get("url"),
            }
            for idx, e in enumerate(raw_entries)
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
