"""Application-owned Bilibili session, kept in memory and never in task history."""
import copy
import threading

_lock = threading.RLock()
_session_id = None
_cookies = None


def validate_cookies(cookies):
    if not isinstance(cookies, list) or not cookies or len(cookies) > 300:
        raise ValueError("登录数据无效，请重新登录 B站")
    result = []
    for cookie in cookies:
        if not isinstance(cookie, dict):
            raise ValueError("登录数据无效")
        domain = cookie.get("domain", "").lstrip(".").lower()
        name, value = cookie.get("name"), cookie.get("value")
        if not (domain == "bilibili.com" or domain.endswith(".bilibili.com")):
            raise ValueError("只接受 B站的登录数据")
        if not isinstance(name, str) or not isinstance(value, str) or not name or len(name) > 256 or len(value) > 16384:
            raise ValueError("登录数据无效")
        if any(c in name + value for c in "\r\n\x00") or any(c in name for c in ";= \t"):
            raise ValueError("登录数据无效")
        path = cookie.get("path") or "/"
        if not isinstance(path, str) or not path.startswith("/") or any(c in path for c in "\r\n\x00"):
            raise ValueError("登录数据无效")
        result.append({"name": name, "value": value, "domain": cookie["domain"], "path": path,
                       "secure": bool(cookie.get("secure", True)), "expires": cookie.get("expires")})
    return result


def install(session_id, cookies):
    if not isinstance(session_id, str) or not session_id or len(session_id) > 100:
        raise ValueError("登录会话标识无效")
    cookies = validate_cookies(cookies)
    global _session_id, _cookies
    with _lock:
        _session_id, _cookies = session_id, cookies


def snapshot(expected=None):
    with _lock:
        if not _cookies:
            raise ValueError("请先在 Bilio 登录 B站")
        if expected and expected != _session_id:
            raise ValueError("登录会话已变化，请重新解析后添加下载")
        return copy.deepcopy(_cookies)


def clear(expected=None):
    global _session_id, _cookies
    with _lock:
        if expected and expected != _session_id:
            return False
        _session_id, _cookies = None, None
        return True
