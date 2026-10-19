"""User-facing failures must not expose temporary signed media addresses."""
import re


def failure(error):
    text = str(error).lower()
    code = "unknown"
    for key, hints in (
        ("disk_full", ("no space left", "磁盘空间不足")),
        ("permission", ("permission denied", "operation not permitted", "目录不可写")),
        ("auth", ("登录", "会员", "会话", "http error 401")),
        ("format", ("requested format", "画质", "格式")),
        ("network", ("timeout", "timed out", "network", "connection", "网络", "http error")),
        ("file", ("文件", "目录", "磁盘", "file", "directory")),
    ):
        if any(hint in text for hint in hints):
            code = key
            break
    return {"code": code, "message": user_error(error), "retryable": code in ("network", "unknown")}


def user_error(error):
    text = str(error)
    text = re.sub(r'(?i)(SESSDATA|bili_jct|cookie|authorization|access_token|refresh_token)\s*[:=]\s*[^\s,;]+', r'\1=[已隐藏]', text)
    text = re.sub(r'/Users/[^/\s]+', '/Users/[用户]', text)
    lower = text.lower()
    if "requested format" in lower:
        return "所选画质当前不可用，请重新解析并选择账号可用的画质。"
    if "operation not permitted" in lower or "permission denied" in lower:
        return "系统拒绝访问，请检查下载目录写入权限；读取浏览器时请检查完全磁盘访问权限。"
    if "could not find" in lower and "cookies" in lower:
        return "未找到浏览器登录数据，请在所选浏览器登录 B站后重试。"
    if "decrypt" in lower or "keyring" in lower or "keychain" in lower:
        return "浏览器登录数据无法解密，请允许钥匙串访问或重新登录后重试。"
    if any(code in lower for code in ("http error 403", "http error 412", "http error 429")):
        return "B站暂时拒绝了请求，请确认登录状态并稍后重试。"
    if "timed out" in lower or "timeout" in lower:
        return "网络连接超时，请检查网络后重试。"
    if "no space left" in lower:
        return "磁盘空间不足，请更换保存目录或释放空间后重试。"
    return re.sub(r"https?://[^\s]+", "[媒体地址]", text)[:800] or "任务失败，请重试。"
