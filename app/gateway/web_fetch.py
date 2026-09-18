"""搜索结果 URL 正文抓取（可选阶段）。

设计原则（对应需求）：HTML 打不开就跳过，解析失败也跳过 —— 本模块任何失败都不抛异常，
只返回"没抓到"，绝不影响总结与主流程。

只用标准库解析（项目无 bs4/lxml 依赖）：html.parser + 自写编码嗅探。
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
}
_DROP_TAGS = {"script", "style", "noscript", "svg", "head", "template", "iframe", "canvas"}
_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6",
               "section", "article", "header", "footer", "blockquote", "pre", "table", "ul", "ol"}
_OK_CTYPE = ("text/html", "text/plain", "application/xhtml", "application/xml", "text/xml")
_REDIRECTS = (301, 302, 303, 307, 308)
_META_CHARSET = re.compile(rb'charset\s*=\s*["\']?\s*([A-Za-z0-9_\-]+)', re.I)


class _TextExtractor(HTMLParser):
    """把 HTML 抽成纯文本：丢弃 script/style 等标签内容，块级标签补换行。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _DROP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _DROP_TAGS:
            if self._skip:
                self._skip -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """HTML → 正文纯文本。解析异常一律吞掉，返回已抽到的部分。"""
    p = _TextExtractor()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    text = "".join(p.parts)
    text = re.sub(r"[ \t\r\f\v\u3000]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def decode_body(raw: bytes, header_charset: str | None = None) -> str:
    """中文站常见 GBK 却无 header 声明：header → <meta charset> → 猜测链。"""
    cands: list[str] = []
    if header_charset:
        cands.append(str(header_charset))
    m = _META_CHARSET.search(raw[:8192])
    if m:
        try:
            cands.append(m.group(1).decode("ascii", "ignore"))
        except Exception:
            pass
    cands += ["utf-8", "gb18030", "big5", "latin-1"]
    seen = set()
    for cs in cands:
        cs = (cs or "").strip().lower()
        if not cs or cs in seen:
            continue
        seen.add(cs)
        try:
            return raw.decode(cs)
        except Exception:
            continue
    return raw.decode("utf-8", "replace")


def _host_allowed(url: str) -> bool:
    """SSRF 防护：仅 http(s)，且目标域名不得解析到私网/环回/保留地址。"""
    try:
        u = urlparse(url)
    except Exception:
        return False
    if u.scheme not in ("http", "https"):
        return False
    host = u.hostname
    if not host:
        return False
    if host.lower() in ("localhost", "localhost.localdomain"):
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    if not infos:
        return False
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            return False
    return True


async def _fetch_one(client: httpx.AsyncClient, url: str, *, max_bytes: int,
                     timeout_s: float, block_private: bool, max_hops: int = 3):
    """抓单页，返回 (最终URL, 正文, 原始长度)；任何失败返回 None。"""
    cur = url
    for _ in range(max_hops + 1):
        if block_private and not await asyncio.to_thread(_host_allowed, cur):
            return None
        try:
            async with client.stream("GET", cur, headers=_HEADERS,
                                     timeout=httpx.Timeout(timeout_s, connect=min(5.0, timeout_s))) as resp:
                if resp.status_code in _REDIRECTS:
                    loc = resp.headers.get("location")
                    if not loc:
                        return None
                    cur = urljoin(cur, loc)
                    continue
                if resp.status_code != 200:
                    return None
                ctype = (resp.headers.get("content-type") or "").lower()
                if ctype and not ctype.startswith(_OK_CTYPE):
                    return None
                charset = getattr(resp, "charset_encoding", None)
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf += chunk
                    if len(buf) >= max_bytes:
                        break
        except Exception:
            return None
        if not buf:
            return None
        raw = bytes(buf)
        text = await asyncio.to_thread(html_to_text, decode_body(raw, charset))
        if not text:
            return None
        return cur, text, len(raw)
    return None


async def fetch_pages(urls, *, max_urls: int = 8, concurrency: int = 5, timeout_s: float = 8,
                      total_timeout_s: float = 20, max_bytes: int = 524288,
                      max_chars: int = 1200, proxy_url: str = "",
                      block_private: bool = True) -> dict[str, str]:
    """并发抓取若干 URL 的正文。

    返回 {原始URL: 正文文本}。失败/超时/非 HTML/解析空 一律跳过（不进返回值，也不抛异常）。
    """
    out: dict[str, str] = {}
    if not urls or max_urls <= 0:
        return out
    targets: list[str] = []
    seen = set()
    for u in urls:
        if not isinstance(u, str):
            continue
        u = u.strip()
        if not u or u in seen:
            continue
        seen.add(u)
        targets.append(u)
        if len(targets) >= max_urls:
            break
    if not targets:
        return out

    sem = asyncio.Semaphore(max(1, int(concurrency)))
    client_kwargs = dict(follow_redirects=False,
                         limits=httpx.Limits(max_connections=max(4, int(concurrency) + 2),
                                             max_keepalive_connections=4, keepalive_expiry=60))
    if proxy_url:
        client_kwargs["proxy"] = proxy_url

    async def one(client, u):
        async with sem:
            return await _fetch_one(client, u, max_bytes=max_bytes, timeout_s=timeout_s,
                                    block_private=block_private)

    try:
        async with httpx.AsyncClient(**client_kwargs) as client:
            tasks = {asyncio.create_task(one(client, u)): u for u in targets}
            done, pending = await asyncio.wait(set(tasks.keys()), timeout=total_timeout_s)
            for t in pending:
                t.cancel()
            for t in done:
                u = tasks[t]
                try:
                    r = t.result()
                except Exception:
                    r = None
                if not r:
                    continue
                _final, text, _n = r
                text = text[:max_chars].strip()
                if text:
                    out[u] = text
    except Exception as e:
        logger.warning(f"[搜索总结] 网页抓取阶段异常，已跳过全部抓取: {type(e).__name__}: {e}")
    return out
