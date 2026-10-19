"""yt-dlp library wrapper.

We call yt-dlp as a Python library (NOT the CLI) so we can hand-pick fields
and attach `progress_hooks` for streaming download progress. The options
below silence yt-dlp's own logging — any non-JSON byte on our stdout would
corrupt the JSONL protocol the Rust shell reads.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import threading
import http.cookiejar
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

from yt_dlp import YoutubeDL
from .errors import user_error
from .source import resolve_short_link

# Bilibili rejects default yt-dlp User-Agent with HTTP 412. A real browser UA
# plus `Referer` AND `Origin` headers pointing back to bilibili.com is what
# bilibili's WAF requires for anonymous metadata fetches; without Origin the
# extractor still 412s on the JSON metadata endpoint (verified against current
# yt-dlp extractor). Cookie-gated content still needs the user's own local
# browser cookies; Bilio reuses that login state without storing cookie contents.
_DEFAULT_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
}


class QuietLogger:
    def debug(self, message):
        pass

    def warning(self, message):
        pass

    def error(self, message):
        pass


class CancelRequested(Exception):
    """Raised from inside a progress_hook to abort the current download.

    yt-dlp catches this and propagates it as a normal error, which we then
    surface to the UI as a `cancelled` status (not an error).
    """


def _check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise CancelRequested()


def quality_selector(policy: dict) -> str:
    """Compile a structured policy without an unrestricted fallback."""
    if not isinstance(policy, dict):
        raise ValueError("缺少清晰度策略")
    mode = policy.get("mode")
    if mode == "best":
        return "bv+ba/b"
    if mode == "max_height":
        height = policy.get("height")
        if type(height) is not int or height not in (360, 480, 720, 1080, 2160):
            raise ValueError("无效的像素高度限制")
        return f"bv[height<={height}]+ba/b[height<={height}]"
    if mode == "max_quality":
        quality = policy.get("quality")
        if type(quality) is not int or quality not in (16, 32, 64, 80, 120):
            raise ValueError("无效的 B站画质档位")
        return f"bv[quality<={quality}]+ba/b[quality<={quality}]"
    if mode == "exact":
        fmt = policy.get("format_id")
        if not isinstance(fmt, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", fmt):
            raise ValueError("无效的指定格式")
        return f"{fmt}+ba/{fmt}[acodec!=none]"
    raise ValueError("不支持的清晰度策略")


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


class DownloadProgressTracker:
    """Aggregate the selected streams and keep protocol messages credential-free."""
    def __init__(self, info, callback):
        self.callback = callback
        self.streams = {str(f.get("format_id")): {"bytes": 0, "total": f.get("filesize") or f.get("filesize_approx")}
                        for f in (info.get("requested_formats") or [info])}
        self.percent = 0
        self.actual_quality = info.get("format") or info.get("format_id")

    def hook(self, data):
        details = data.get("info_dict") or {}
        stream_id = str(details.get("format_id"))
        if stream_id not in self.streams and len(self.streams) == 1:
            stream_id = next(iter(self.streams))
        stream = self.streams.setdefault(stream_id, {"bytes": 0, "total": None})
        stream["bytes"] = max(stream["bytes"], data.get("downloaded_bytes") or 0)
        stream["total"] = data.get("total_bytes") or data.get("total_bytes_estimate") or stream["total"]
        self.publish("downloading", data)

    def publish(self, status, data=None):
        data = data or {}
        downloaded = sum(s["bytes"] for s in self.streams.values())
        totals = [s["total"] for s in self.streams.values()]
        total = sum(totals) if all(totals) else None
        if total:
            self.percent = max(self.percent, min(99, int(downloaded / total * 99)))
        if status == "processing":
            self.percent = max(self.percent, 99)
        speed = data.get("speed")
        self.callback({"status": status, "overall_percent": self.percent if total or status == "processing" else None,
            "downloaded_bytes": downloaded, "total_bytes": total, "speed": speed,
            "eta": max(0, int((total - downloaded) / speed)) if total and speed else None,
            "filename": os.path.basename(data.get("filename") or ""), "actual_quality": self.actual_quality})


def _apply_cookies(opts: dict[str, Any], cookies_from_browser: Optional[str], session_cookies=None) -> None:
    """Wire yt-dlp's cookiesfrombrowser into `opts` if a browser is given.

    yt-dlp expects a tuple `(browser_name, profile, keyring, container)`; we
    pass just the browser name (most common case). Empty/None means no cookies.
    Supported browsers: chrome, chromium, brave, edge, firefox, opera, safari,
    vivaldi, whale. Unsupported names are rejected rather than made anonymous.
    """
    if cookies_from_browser == "bilio":
        # Use CookieJar rather than a broad Cookie header, so unrelated hosts
        # and redirects never receive Bilibili credentials.
        return
    if not cookies_from_browser:
        return
    if cookies_from_browser not in {
        "chrome", "chromium", "brave", "edge", "firefox",
        "opera", "safari", "vivaldi", "whale",
    }:
        raise ValueError("不支持所选浏览器，请重新选择浏览器")
    access_error = _cookie_access_error(cookies_from_browser)
    if access_error:
        raise ValueError(access_error)
    # yt-dlp accepts a tuple here; only the first element is required.
    opts["cookiesfrombrowser"] = (cookies_from_browser,)


def _cookie_access_error(browser):
    if sys.platform != "darwin":
        return None
    roots = {"chrome": "~/Library/Application Support/Google/Chrome",
             "edge": "~/Library/Application Support/Microsoft Edge",
             "brave": "~/Library/Application Support/BraveSoftware/Brave-Browser",
             "chromium": "~/Library/Application Support/Chromium",
             "firefox": "~/Library/Application Support/Firefox/Profiles",
             "safari": "~/Library/Cookies"}
    root = roots.get(browser)
    if not root:
        return None
    try:
        with os.scandir(os.path.expanduser(root)) as entries:
            next(entries, None)
    except PermissionError:
        return "macOS 阻止读取浏览器数据。请在系统设置 → 隐私与安全性 → 完全磁盘访问权限中允许 Bilio，然后重启应用。"
    except FileNotFoundError:
        return "没有找到所选浏览器的数据。请先在该浏览器登录 B站，或选择实际使用的浏览器。"
    return None


def _session_jar(cookies):
    from .auth import validate_cookies
    jar = http.cookiejar.CookieJar()
    for cookie in validate_cookies(cookies):
        expires = cookie.get("expires")
        jar.set_cookie(http.cookiejar.Cookie(0, cookie["name"], cookie["value"], None, False,
            cookie["domain"], True, cookie["domain"].startswith("."), cookie["path"], True,
            cookie["secure"], int(expires) if expires else None, not bool(expires), None, None, {}, False))
    return jar


def _apply_session(ydl, browser, cookies):
    if browser == "bilio":
        if cookies is None:
            from .auth import snapshot
            cookies = snapshot()
        for cookie in _session_jar(cookies):
            ydl.cookiejar.set_cookie(cookie)


def check_cookies(cookies_from_browser: Optional[str], session_cookies=None) -> dict[str, Any]:
    """Probe the user's Bilibili login state using the given browser's cookies.

    Strategy: ask yt-dlp's cookie extractor for a `CookieJar` (so we reuse the
    same browser-keyring code path the downloader will), build a header
    `Cookie: ...` from just the `bilibili.com`-scoped cookies, and call the
    public `web-interface/nav` endpoint. The response tells us:
    - whether the session is recognized (`isLogin`)
    - the display name (`uname`) — surfaces "已登录 albert"
    - VIP status (`vipStatus == 1`) and VIP label (`vip_label.text` or
      `vipType` fallback) — so the UI can explain which account state is used

    Returns a stable shape; any failure populates `error` and leaves
    `logged_in` False so the UI can render "读取失败 …" rather than crash.
    """
    result: dict[str, Any] = {
        "ok": False,
        "logged_in": False,
        "username": None,
        "is_vip": False,
        "vip_label": None,
        "error": None,
    }
    if not cookies_from_browser:
        result["error"] = "未选择浏览器"
        return result
    if cookies_from_browser != "bilio" and cookies_from_browser not in {
        "chrome", "chromium", "brave", "edge", "firefox",
        "opera", "safari", "vivaldi", "whale",
    }:
        result["error"] = f"不支持的浏览器: {cookies_from_browser}"
        return result

    # Extract cookies via yt-dlp so we share its keyring/profile handling.
    access_error = None if cookies_from_browser == "bilio" else _cookie_access_error(cookies_from_browser)
    if access_error:
        result["error"] = access_error
        return result
    try:
        from yt_dlp.cookies import extract_cookies_from_browser

        if cookies_from_browser == "bilio":
            if session_cookies is None:
                from .auth import snapshot
                session_cookies = snapshot()
            jar = _session_jar(session_cookies)
        else:
            jar = extract_cookies_from_browser(cookies_from_browser)
    except Exception as exc:
        result["error"] = ("请先在 Bilio 登录 B站" if cookies_from_browser == "bilio" else
            "未找到浏览器登录数据，请先在所选浏览器登录 B站。" if "could not find" in str(exc) else
            "无法读取浏览器登录数据，请检查系统权限或浏览器钥匙串授权后重试。")
        return result

    bili_cookies = [c for c in jar if (c.domain or "").lstrip(".") == "bilibili.com" or (c.domain or "").endswith(".bilibili.com")]
    if not bili_cookies:
        result["error"] = "未在该浏览器中找到 bilibili.com 的 Cookies"
        return result
    req = urllib.request.Request(
        "https://api.bilibili.com/x/web-interface/nav",
        headers={
            **_DEFAULT_HTTP_HEADERS,
        },
    )
    try:
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        with opener.open(req, timeout=8) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        result["error"] = f"调用 B站 API 失败: {exc}"
        return result
    except Exception as exc:
        result["error"] = f"解析 B站响应失败: {exc}"
        return result

    # `code` is 0 on success, -101 on "not logged in" (anonymous). We treat
    # -101 as a successful probe with `logged_in=False` so the user still
    # learns the cookies were read but didn't include a valid session.
    code = payload.get("code")
    data = payload.get("data") or {}
    if code == -101:
        result["ok"] = True
        return result
    if code != 0:
        result["error"] = f"B站返回错误 code={code}: {payload.get('message')}"
        return result

    result["ok"] = True
    result["logged_in"] = bool(data.get("isLogin"))
    result["username"] = data.get("uname")
    if cookies_from_browser == "bilio":
        # This field is for the native process only; it is not a credential.
        result["account_id"] = str(data.get("mid")) if data.get("mid") else None
    # `vipStatus`: 1 = active VIP, 0 = expired / never. `vipType`: 1 = 月度,
    # 2 = 年度 / 长期. The label object usually has `text` like "大会员".
    vip = data.get("vip") if isinstance(data.get("vip"), dict) else {}
    result["is_vip"] = (data.get("vipStatus", vip.get("status")) == 1)
    label_obj = data.get("vip_label") or data.get("vip") or {}
    if isinstance(label_obj, dict):
        result["vip_label"] = label_obj.get("text") or label_obj.get("label")
    if result["is_vip"] and not result["vip_label"]:
        result["vip_label"] = "大会员"
    return result


def parse_url(
    url: str,
    cookies_from_browser: Optional[str] = None,
    on_entry: Optional[Callable[[int, int, dict[str, Any]], None]] = None,
    cancel_event: Optional[threading.Event] = None,
    session_cookies=None,
) -> dict[str, Any]:
    """Parse a Bilibili URL.

    For playlist / bangumi URLs the per-entry detail fetch (`_enrich_entries`)
    is the slow part — dozens of episodes × ~1 RTT each. When `on_entry` is
    given, every entry is shaped immediately and the callback fires after the
    entry's detail metadata lands, so the UI can render the table progressively
    instead of waiting for the whole season.

    `on_entry(index, total, entry)` is called from the same thread; the
    callback is responsible for any cross-thread serialization (the sidecar's
    `emit()` already takes the stdout lock).
    """
    _check_cancel(cancel_event)
    if urllib.parse.urlsplit(url).hostname == 'b23.tv':
        url = resolve_short_link(url)
    opts: dict[str, Any] = {
        # Resolve playlist/collection containers but leave entries flat — we
        # only want titles/ids for the UI's "select episode" step.
        "extract_flat": "in_playlist",
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": False,
        "http_headers": _DEFAULT_HTTP_HEADERS,
        "socket_timeout": 15,
        "extractor_retries": 1,
        "logger": QuietLogger(),
    }
    _apply_cookies(opts, cookies_from_browser)
    with YoutubeDL(opts) as ydl:
        _apply_session(ydl, cookies_from_browser, session_cookies)
        info = ydl.extract_info(url, download=False)
        if isinstance(info, dict) and 'bangumi' in str(info.get('extractor', '')).lower() and not info.get('series') and info.get('episode_id'):
            try:
                endpoint = 'https://api.bilibili.com/pgc/view/web/season?ep_id=' + str(int(info['episode_id']))
                opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(ydl.cookiejar))
                with opener.open(urllib.request.Request(endpoint, headers=_DEFAULT_HTTP_HEADERS), timeout=10) as response:
                    metadata = json.loads(response.read(2 * 1024 * 1024))
                if metadata.get('code') == 0:
                    info['series'] = (metadata.get('result') or {}).get('title')
            except Exception:
                pass  # Missing optional series metadata must not block valid formats.
    _check_cancel(cancel_event)
    shaped = _shape(info or {})
    if shaped.get("is_playlist") and shaped.get("entries"):
        _enrich_entries(shaped["entries"], cookies_from_browser, on_entry=on_entry, cancel_event=cancel_event, session_cookies=session_cookies)
    return shaped


def download_video(
    url: str,
    policy: dict,
    output_dir: str,
    progress_callback: Callable[[dict[str, Any]], None],
    cancel_event: threading.Event,
    cookies_from_browser: Optional[str] = None,
    session_cookies=None,
    display_title=None,
) -> Optional[str]:
    """Download `url` under a strict structured quality policy.

    Returns the final on-disk path of the merged output file (yt-dlp's
    `requested_downloads[0].filepath`), or None if not available.

    `progress_callback` receives yt-dlp's raw hook dict; it should not block.
    To cancel, set `cancel_event` — the next progress tick raises
    `CancelRequested` which bubbles up through yt-dlp.
    """
    os.makedirs(output_dir, exist_ok=True)
    if urllib.parse.urlsplit(url).hostname == 'b23.tv':
        url = resolve_short_link(url)
    _check_cancel(cancel_event)
    resolved_format = quality_selector(policy)

    tracker = None
    final_path = None

    def _hook(d: dict[str, Any]) -> None:
        if cancel_event.is_set():
            raise CancelRequested()
        if tracker:
            tracker.hook(d)

    def _post_hook(d):
        nonlocal final_path
        _check_cancel(cancel_event)
        if tracker:
            tracker.publish("processing")
        if d.get("status") == "finished":
            candidate = (d.get("info_dict") or {}).get("filepath")
            if candidate and os.path.isfile(candidate):
                final_path = candidate

    opts: dict[str, Any] = {
        # The selector keeps separate audio and has no unrestricted fallback.
        "format": resolved_format,
        "outtmpl": os.path.join(output_dir, "%(title).160B [%(id)s] [%(format_id)s].%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,         # silence yt-dlp's own progress text
        "continuedl": True,
        "overwrites": False,
        "http_headers": _DEFAULT_HTTP_HEADERS,
        "progress_hooks": [_hook],
        "postprocessor_hooks": [_post_hook],
        "socket_timeout": 15,
        "retries": 2,
        "fragment_retries": 2,
        "extractor_retries": 1,
        "noplaylist": True,         # single-video download; playlists are per-entry
        "logger": QuietLogger(),
    }

    ffmpeg_path = _get_ffmpeg_path()
    if ffmpeg_path:
        opts["ffmpeg_location"] = ffmpeg_path

    _apply_cookies(opts, cookies_from_browser)

    with YoutubeDL(opts) as ydl:
        _apply_session(ydl, cookies_from_browser, session_cookies)
        info = ydl.extract_info(url, download=False)
        _check_cancel(cancel_event)
        if not isinstance(info, dict) or info.get("_type") == "playlist":
            raise ValueError("请选择具体视频或分集后下载")
        if display_title:
            info['title'] = display_title
        tracker = DownloadProgressTracker(info, progress_callback)
        tracker.publish("starting")
        info = ydl.process_ie_result(info, download=True)
        _check_cancel(cancel_event)
        # yt-dlp puts the final merged path in `requested_downloads`.
        if isinstance(info, dict):
            if final_path and os.path.isfile(final_path):
                return final_path
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
    return quality_selector({"mode": "exact", "format_id": fmt})


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
    on_entry: Optional[Callable[[int, int, dict[str, Any]], None]] = None,
    cancel_event: Optional[threading.Event] = None,
    session_cookies=None,
) -> None:
    """Resolve playlist entries once so the UI can show duration and quality.

    Flat playlist extraction is fast but only returns episode URLs. For
    bangumi/season downloads, users need to know what their current cookies can
    actually access, so we pay the extra metadata requests here and keep the
    downloaded media untouched (`skip_download=True`).

    When `on_entry` is provided, it's invoked after every entry resolves
    (regardless of success/failure) — caller can stream `parse_progress`
    events from here without waiting for the whole season.
    """
    opts: dict[str, Any] = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "http_headers": _DEFAULT_HTTP_HEADERS,
        "socket_timeout": 15,
        "extractor_retries": 1,
        "logger": QuietLogger(),
    }
    _apply_cookies(opts, cookies_from_browser)

    total = len(entries)
    with YoutubeDL(opts) as ydl:
        _apply_session(ydl, cookies_from_browser, session_cookies)
        for idx, entry in enumerate(entries):
            _check_cancel(cancel_event)
            entry["detail_status"] = "pending"
            detail_url = entry.get("url")
            if not detail_url:
                entry["detail_status"] = "missing_url"
                if on_entry:
                    on_entry(idx, total, entry)
                continue
            try:
                detail = ydl.extract_info(detail_url, download=False) or {}
            except Exception as exc:
                _check_cancel(cancel_event)
                entry["detail_status"] = "error"
                entry["detail_error"] = user_error(exc)
                if on_entry:
                    on_entry(idx, total, entry)
                continue

            _check_cancel(cancel_event)
            entry["detail_status"] = "ok"
            entry["title"] = _entry_display_title(detail, entry.get("title"))
            entry["duration"] = detail.get("duration") or entry.get("duration")

            best = _best_video_format(detail.get("formats") or [])
            if not best:
                entry["detail_status"] = "no_formats"
            else:
                entry["best_format_id"] = best.get("format_id")
                entry["best_format_note"] = _format_label(best)
                entry["best_width"] = best.get("width")
                entry["best_height"] = best.get("height")
                entry["best_quality"] = best.get("quality")

            if on_entry:
                on_entry(idx, total, entry)


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
            display_title = f"{info.get('series') or '番剧'} · 第 {episode_number} 集"
            if info.get('episode') and info['episode'] != str(episode_number):
                display_title += ' · ' + info['episode']
        else:
            display_title = f"番剧 · {raw_title}" if raw_title else "番剧"
    else:
        display_title = raw_title

    # The pinned extractor converts play_info.timelength from ms to seconds.
    raw_duration = info.get("duration")
    safe_duration = raw_duration if isinstance(raw_duration, (int, float)) and math.isfinite(raw_duration) and raw_duration >= 0 else None

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
