"""Keep webpage identity (including multi-part index), discard tracking data."""
import re
import urllib.request
import urllib.error
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit, urljoin


def normalize_source(value):
    if not isinstance(value, str):
        raise ValueError("缺少 B站链接")
    value = value.strip()
    if not re.fullmatch(r"https?://[^\s<>\"\u3000]+", value):
        candidates = list(dict.fromkeys(re.findall(r"https?://[^\s<>\"\u3000，。！）)]+", value)))
        if len(candidates) != 1:
            raise ValueError("请每次选择一个 B站链接；分享文本中检测到多个或没有链接")
        value = candidates[0]
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not (
        host == "bilibili.com" or host.endswith(".bilibili.com") or host == "b23.tv"
    ) or parsed.username or parsed.password or parsed.port:
        raise ValueError("请输入 bilibili.com 或 b23.tv 的视频链接")
    query = urlencode([(key, val) for key, val in parse_qsl(parsed.query) if key == "p" and val.isdigit()])
    return urlunsplit(("https", host, parsed.path, query, ""))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def resolve_short_link(value):
    url = normalize_source(value)
    opener = urllib.request.build_opener(_NoRedirect())
    for _ in range(5):
        if urlsplit(url).hostname != 'b23.tv':
            return url
        try:
            with opener.open(urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'}), timeout=15):
                raise ValueError('短链接未返回视频地址，请使用完整视频链接')
        except urllib.error.HTTPError as response:
            if response.code not in (301, 302, 303, 307, 308):
                raise
            location = response.headers.get('Location')
            if not location:
                raise ValueError('短链接缺少跳转地址')
            # Validate every redirect before making the next network request.
            url = normalize_source(urljoin(url, location))
    raise ValueError('短链接跳转过多，请使用完整视频链接')
