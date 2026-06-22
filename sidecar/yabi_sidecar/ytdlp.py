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
    shaped = _shape(info or {})
    if shaped.get("is_playlist") and shaped.get("entries"):
        _enrich_entries(shaped["entries"], cookies_from_browser)
    return shaped


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
    resolved_format = _format_with_audio(format_id)

    def _hook(d: dict[str, Any]) -> None:
        if cancel_event.is_set():
            raise CancelRequested()
        progress_callback(d)

    opts: dict[str, Any] = {
        # Bilibili exposes video and audio as separate DASH streams. A table
        # row like "30080" is video-only, so exact ids must be expanded to
        # "<id>+bestaudio/best"; richer expressions from batch downloads are
        # already complete and should be left untouched.
        "format": resolved_format,
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


def _format_with_audio(format_id: str) -> str:
    """Return a yt-dlp format expression that keeps audio with exact video ids.

    The UI's single-video table emits raw Bilibili format ids such as "30080".
    Passing that directly to yt-dlp downloads video-only content. Batch mode
    emits full selectors such as "bv*[height<=1080]+ba/best", so expressions
    containing yt-dlp operators are preserved as-is.
    """
    fmt = format_id.strip()
    if not fmt:
        return fmt
    if any(op in fmt for op in ("+", "/", ",")):
        return fmt
    return f"{fmt}+bestaudio/best"


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


def _entry_display_title(detail: dict[str, Any], fallback: Optional[str]) -> Optional[str]:
    """Return a useful episode title without exposing yt-dlp's bare numbers."""
    episode_number = detail.get("episode_number")
    if episode_number is not None:
        return f"第 {episode_number} 集"
    episode = detail.get("episode")
    if episode:
        return episode
    title = detail.get("title")
    if title and not str(title).isdigit():
        return title
    return fallback


def _best_video_format(formats: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    videos = [
        f for f in formats
        if f.get("vcodec") and f.get("vcodec") != "none"
    ]
    if not videos:
        return None

    def sort_key(f: dict[str, Any]) -> tuple[int, int, int, float]:
        return (
            int(f.get("quality") or 0),
            int(f.get("height") or 0),
            int(f.get("width") or 0),
            float(f.get("vbr") or 0),
        )

    return max(videos, key=sort_key)


def _format_label(f: dict[str, Any]) -> Optional[str]:
    return f.get("format") or f.get("format_note") or (
        f"{f.get('height')}p" if f.get("height") else None
    )


def _enrich_entries(
    entries: list[dict[str, Any]],
    cookies_from_browser: Optional[str],
) -> None:
    """Resolve playlist entries once so the UI can show duration and quality.

    Flat playlist extraction is fast but only returns episode URLs. For
    bangumi/season downloads, users need to know what their current cookies can
    actually access, so we pay the extra metadata requests here and keep the
    downloaded media untouched (`skip_download=True`).
    """
    opts: dict[str, Any] = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "http_headers": _DEFAULT_HTTP_HEADERS,
    }
    _apply_cookies(opts, cookies_from_browser)

    with YoutubeDL(opts) as ydl:
        for entry in entries:
            entry["detail_status"] = "pending"
            detail_url = entry.get("url")
            if not detail_url:
                entry["detail_status"] = "missing_url"
                continue
            try:
                detail = ydl.extract_info(detail_url, download=False) or {}
            except Exception as exc:
                entry["detail_status"] = "error"
                entry["detail_error"] = str(exc)
                continue

            entry["detail_status"] = "ok"
            entry["title"] = _entry_display_title(detail, entry.get("title"))
            entry["duration"] = detail.get("duration") or entry.get("duration")

            best = _best_video_format(detail.get("formats") or [])
            if not best:
                entry["detail_status"] = "no_formats"
                continue
            entry["best_format_id"] = best.get("format_id")
            entry["best_format_note"] = _format_label(best)
            entry["best_width"] = best.get("width")
            entry["best_height"] = best.get("height")
            entry["best_quality"] = best.get("quality")


def _shape(info: dict[str, Any]) -> dict[str, Any]:
    """Trim a yt-dlp info dict to a UI-friendly subset.

    The raw dict is huge (tens of KB of internal fields); we forward only what
    the parser screen actually renders. Add fields here as the UI grows.

    Three content kinds get distinct treatment:
    - `"bangumi"`: a single episode of a 番剧 (anime / drama). yt-dlp's
      Bilibili-bangumi extractor returns `title` as bare episode number (e.g.
      `"1"`), drops `uploader`, and never includes the actual series name (you
      need the bilibili pgc API for that). We surface `episode_number` /
      `episode_id` / `season_id` so the UI can show "番剧 · 第 N 集" instead
      of the misleading bare title.
    - `"playlist"`: an anthology / collection / multi-part video. Has
      `entries`; the UI shows the part list.
    - `"single"`: a regular standalone video. Has `uploader` and a real title.
    """
    raw_entries = info.get("entries") or []
    extractor = (info.get("extractor") or info.get("extractor_key") or "").lower()
    is_bangumi = "bangumi" in extractor
    is_playlist = info.get("_type") == "playlist"

    if is_bangumi:
        kind = "bangumi"
    elif is_playlist:
        kind = "playlist"
    else:
        kind = "single"

    raw_title = info.get("title")
    episode_number = info.get("episode_number")
    if kind == "bangumi":
        # The raw `title` is just the episode number for bangumi (yt-dlp limitation);
        # render something a human can read.
        if episode_number is not None:
            display_title = f"番剧 · 第 {episode_number} 集"
        else:
            display_title = f"番剧 · {raw_title}" if raw_title else "番剧"
    else:
        display_title = raw_title

    # yt-dlp's bilibili-bangumi extractor reports an unreliable `duration` —
    # the value is the entire-season cumulative time (or some other inflated
    # number), not the single-episode duration. Showing it would mislead
    # users into thinking they're getting an hour-long episode when they're
    # actually getting ~24 min. Surface as None so the UI shows "—".
    raw_duration = info.get("duration")
    safe_duration = None if kind == "bangumi" else raw_duration

    return {
        "title": raw_title,
        "display_title": display_title,
        "kind": kind,
        "uploader": info.get("uploader"),
        "duration": safe_duration,
        "thumbnail": info.get("thumbnail"),
        "webpage_url": info.get("webpage_url"),
        "is_playlist": is_playlist,
        # Bangumi-specific fields. Always present (None when not bangumi) so the
        # frontend type stays stable.
        "episode": info.get("episode"),
        "episode_number": episode_number,
        "episode_id": info.get("episode_id"),
        "season_id": info.get("season_id"),
        "extractor": info.get("extractor"),
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
                "width": f.get("width"),
                "vbr": f.get("vbr"),
                "acodec": f.get("acodec"),
                "vcodec": f.get("vcodec"),
                # The bilibili extractor puts the human-readable Chinese
                # quality label (e.g. "1080P 高清", "4K 超高清") in `format`,
                # not `format_note`. Surface `format` first so the UI can
                # tell apart "1080P 高清" vs "1080P 高码率" (same height
                # 816, different bitrate) and label 1632p as 4K.
                "format_note": f.get("format") or f.get("format_note"),
                "quality": f.get("quality"),
                "filesize": f.get("filesize") or f.get("filesize_approx"),
            }
            for f in (info.get("formats") or [])
        ],
    }
