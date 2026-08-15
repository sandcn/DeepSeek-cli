"""
page_fetcher — 网页内容获取与正文提取模块

供 web_fetch 工具使用。
从指定 URL 获取 HTML，提取标题/发布时间/正文内容（去导航/广告/页脚）。
"""

from __future__ import annotations

import copy
import ipaddress
import logging
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup, Tag

# 编码检测（复用 tools.encoding 模块，含二次质量校验）
from .encoding import detect_encoding

_logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────

# 正文最大提取字符数（避免输出过大）
MAX_BODY_CHARS = 50_000

# 内容最小长度阈值（正文提取策略判断）
_MIN_CONTENT_LENGTH = 200

# 摘要最大字符数（用于错误/截断提示）
MAX_PREVIEW_CHARS = 200

# 请求超时（秒）
REQUEST_TIMEOUT = 15

# 禁止请求的私有IP网段（SSRF防护）
PRIVATE_PREFIXES = (
    "10.", "172.16.", "172.17.", "172.18.", "172.19.",
    "172.20.", "172.21.", "172.22.", "172.23.",
    "172.24.", "172.25.", "172.26.", "172.27.",
    "172.28.", "172.29.", "172.30.", "172.31.",
    "192.168.", "127.", "0.", "169.254.",
    "::1", "fc00:", "localhost",
)

# 内容提取时排除的标签（导航/脚本/样式等非内容元素）
REMOVE_TAGS = {
    "script", "style", "nav", "footer", "header",
    "aside", "noscript", "iframe", "form", "button",
    "svg", "canvas", "video", "audio", "object",
    "embed", "select", "option", "datalist",
}

# 内容提取时排除的 class/id 关键词（小写匹配）
REMOVE_CLASS_KEYWORDS = (
    "nav", "navbar", "menu", "sidebar", "footer",
    "header", "banner", "advertisement", "ad-", "ads",
    "copyright", "footnote", "comment", "comments",
    "social", "share", "related", "recommend",
    "widget", "toolbar", "breadcrumb", "pagination",
    "cookie", "popup", "modal", "overlay",
    "siderail", "side",
)

# 预编译噪音关键词正则（替代逐关键词循环，O(n) 替代 O(n*k)）
_NOISE_KEYWORD_RE = re.compile(
    '|'.join(re.escape(k) for k in REMOVE_CLASS_KEYWORDS),
    re.IGNORECASE,
)

# 发布日期提取的 meta 属性组合
_META_KEY = "meta"
DATE_META_PATTERNS = [
    (_META_KEY, {"name": "pubdate"}),
    (_META_KEY, {"name": "publishdate"}),
    (_META_KEY, {"name": "article:published_time"}),
    (_META_KEY, {"property": "article:published_time"}),
    (_META_KEY, {"name": "date"}),
    (_META_KEY, {"name": "dc.date"}),
    (_META_KEY, {"property": "og:pubdate"}),
    (_META_KEY, {"itemprop": "datePublished"}),
    ("time", {"itemprop": "datePublished"}),
    ("time", {"datetime": True}),  # 任何 <time datetime="...">
]


# ═══════════════════════════════════════════════════════════
#  URL 安全校验
# ═══════════════════════════════════════════════════════════

def _is_private_url(url: str) -> bool:
    """检查 URL 是否指向私有/内网地址（SSRF防护）。

    字面量级快速检查（localhost 别名 / 数字 IP / IPv6 link-local），
    不做 DNS 解析——解析级校验见 ``_validate_fetch_url`` 的异步路径
    （域名可解析到内网 IP 的反弹攻击在请求前拦截）。
    """
    parsed = urlparse(url)
    hostname = parsed.hostname or ""

    # 检查 localhost 别名
    if hostname in ("localhost", "127.0.0.1", "0.0.0.0", "[::1]"):
        return True

    # 检查 IPv6 link-local 地址 (fe80::/10: 首 hextet 范围 0xFE80-0xFEBF)
    # 快速排除：不含 ":" 的 hostname 不可能是 IPv6，跳过 ipaddress 解析
    if ":" in hostname:
        try:
            ipv6 = ipaddress.IPv6Address(hostname)
            first_hextet = ipv6.packed[0] * 256 + ipv6.packed[1]
            if 0xFE80 <= first_hextet <= 0xFEBF:
                return True
        except ValueError:
            _logger.debug("IPv6 地址解析失败（非 IPv6 主机名，安全跳过）: %s", hostname)

    # 检查是否包含字母（区分 IP 地址和主机名）
    # 私网前缀匹配仅对纯 IP 地址生效，避免误拦截如 127.example.com 等合法域名
    is_numeric_ip = not re.search(r'[a-zA-Z]', hostname)

    # 检查私有 IP 前缀（仅对纯 IP 地址生效）
    if is_numeric_ip:
        for prefix in PRIVATE_PREFIXES:
            if hostname.startswith(prefix):
                return True

    return False


def _is_private_ip(ip_str: str) -> bool:
    """判断单个 IP 地址字符串是否属于内网/保留地址（SSRF 防护）。"""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


async def _resolve_host_ips_async(hostname: str) -> list[str]:
    """异步 DNS 解析主机名 → IP 列表（去重，保持顺序）。

    使用事件循环原生 getaddrinfo，不阻塞事件循环；解析失败抛
    socket.gaierror / OSError，由调用方转为错误信息。
    """
    import asyncio
    import socket

    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    ips: list[str] = []
    seen: set[str] = set()
    for info in infos:
        ip = info[4][0]
        if ip not in seen:
            seen.add(ip)
            ips.append(ip)
    return ips


async def _validate_fetch_url(url: str) -> Optional[str]:
    """校验 fetch 的 URL 是否合法安全，有问题返回错误消息，通过返回 None。

    异步：含 DNS 解析级 SSRF 校验（域名解析到内网 IP 时拒绝），
    使用 loop.getaddrinfo 避免阻塞事件循环。
    """
    if not url or not url.strip():
        return "(fetch失败: URL为空)"

    url = url.strip()

    # 只允许 http/https
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"(fetch失败: 不支持的协议 '{parsed.scheme}'，仅支持 http/https)"

    if not parsed.netloc:
        return "(fetch失败: URL缺少域名)"

    hostname = parsed.hostname or ""

    # SSRF 防护 1：字面量快速检查（localhost 别名 / 数字 IP / IPv6 link-local）
    if _is_private_url(url):
        return "(fetch失败: 不允许访问内网地址)"

    # SSRF 防护 2：DNS 解析级检查——域名可指向内网（如 127.0.0.1.nip.io、
    #   sslip.io 类反弹域名、十进制/十六进制 IP 编码），解析后任一地址
    #   为私有/保留地址即拒绝（修复前仅字面量前缀检查，该层完全缺失）。
    try:
        ips = await _resolve_host_ips_async(hostname)
    except (OSError, ValueError) as e:
        _logger.warning("fetch URL 域名解析失败: %s (%s)", hostname, e)
        return f"(fetch失败: 域名解析失败 '{hostname}')"

    if any(_is_private_ip(ip) for ip in ips):
        return "(fetch失败: 不允许访问内网地址)"

    return None


# ═══════════════════════════════════════════════════════════
#  发布日期提取
# ═══════════════════════════════════════════════════════════

def _extract_date(soup: BeautifulSoup) -> str:
    """从 HTML 的 meta 标签和 time 标签中提取发布时间"""
    for tag_name, attrs in DATE_META_PATTERNS:
        if "datetime" in attrs and attrs["datetime"] is True:
            # 匹配任何 <time datetime="...">
            for tag in soup.find_all("time"):
                dt = tag.get("datetime", "")
                if dt:
                    return _format_date_str(dt)
            continue

        tag = soup.find(tag_name, attrs)
        if tag:
            content = tag.get("content") or tag.get("datetime") or tag.get_text(strip=True)
            if content:
                formatted = _format_date_str(content)
                if formatted:
                    return formatted

    return ""


_DATE_PATTERNS = [
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S%Z",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y年%m月%d日",
]

# strptime 指令 → 匹配正则（用于截取「能被该格式解析的前缀」）。
# ★ 修复（review 方向）：修复前用 ``clean[:len(pattern)]`` 按**格式串长度**
#   截断（"%Y-%m-%d" 8 字符 vs 实际日期 "2024-01-01" 10 字符），所有模式
#   恒解析失败，日期时间分量被丢弃、斜杠/中文格式返回空串。
_STRPTIME_RE_PART = {
    "%Y": r"\d{4}",
    "%m": r"\d{2}",
    "%d": r"\d{2}",
    "%H": r"\d{2}",
    "%M": r"\d{2}",
    "%S": r"\d{2}",
    "%z": r"[+-]\d{2}:?\d{2}",
    "%Z": r"[A-Za-z]+",
}
def _pattern_to_regex(pattern: str) -> str:
    """strptime 格式串 → 前缀匹配正则（捕获组 1 = 恰好匹配该格式的前缀）。

    逐字符扫描：``%X`` 指令替换为对应匹配段，其余字面量转义保留
    （"-"、":"、"T"、"/"、"年" 等分隔符不得丢失）。
    """
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern[i] == "%" and i + 1 < len(pattern):
            directive = pattern[i:i + 2]
            part = _STRPTIME_RE_PART.get(directive)
            if part is not None:
                out.append(part)
                i += 2
                continue
        out.append(re.escape(pattern[i]))
        i += 1
    return "(" + "".join(out) + ")"


_PATTERN_RE = [
    (pattern, re.compile(_pattern_to_regex(pattern)))
    for pattern in _DATE_PATTERNS
]


def _format_date_str(date_str: str) -> str:
    """尝试多种格式解析日期字符串，返回统一格式 'YYYY-MM-DD HH:MM' 或空字符串"""
    # 先尝试去掉时区偏移的尾巴（如 +08:00）
    clean = date_str.strip()
    # 去掉末尾的 Z
    if clean.endswith("Z"):
        clean = clean[:-1] + "+00:00"

    for pattern, pattern_re in _PATTERN_RE:
        m = pattern_re.match(clean)
        if not m:
            continue
        # 仅截取「恰好匹配该格式」的前缀再解析（日期后允许跟随任意尾巴）
        prefix = m.group(1)
        try:
            dt = datetime.strptime(prefix, pattern)
            return dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue

    # 尝试仅提取 YYYY-MM-DD
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', date_str)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    return ""


# ═══════════════════════════════════════════════════════════
#  正文提取（启发式）
# ═══════════════════════════════════════════════════════════

def _is_noise_element(tag: Tag) -> bool:
    """判断元素是否为噪音（导航/广告/侧栏等），基于 class/id 关键词"""
    classes = " ".join(tag.get("class", [])) + " " + (tag.get("id", "") or "")
    return bool(_NOISE_KEYWORD_RE.search(classes))


# 策略3 常见内容区 CSS 选择器（模块级常量，避免每次调用重新构建）
_CONTENT_SELECTORS = [
    ".content", ".post-content", ".article-content",
    ".entry-content", ".post-body", ".article-body",
    ".main-content", ".page-content", ".body-content",
    "#content", "#main-content", "#article",
    "[itemprop='articleBody']",
]


def _extract_main_content(soup: BeautifulSoup) -> str:
    """从 BeautifulSoup 对象中提取正文文本

    多级降级策略：
    1. <article> 标签
    2. <main> 或 [role="main"]
    3. 内容区公共 class（.content, .post, .article 等）
    4. <body> 内最长文本密度的容器
    5. 纯 <body> 文本
    """
    # 策略1: <article> 标签
    article = soup.find("article")
    if article:
        text = _extract_text_from_container(article)
        if len(text) > _MIN_CONTENT_LENGTH:
            return text

    # 策略2: <main> 或 role="main"
    main_tag = soup.find("main") or soup.find(attrs={"role": "main"})
    if main_tag:
        text = _extract_text_from_container(main_tag)
        if len(text) > _MIN_CONTENT_LENGTH:
            return text

    # 策略3: 常见内容 class
    for selector in _CONTENT_SELECTORS:
        container = soup.select_one(selector)
        if container:
            text = _extract_text_from_container(container)
            if len(text) > _MIN_CONTENT_LENGTH:
                return text

    # 策略4: 最长文本密度的容器
    body = soup.find("body")
    if body:
        best_text = ""
        # 收集 body 下所有直接子容器
        for child in body.find_all(["div", "section", "main", "article"], recursive=False):
            if _is_noise_element(child):
                continue
            text = _extract_text_from_container(child)
            if len(text) > len(best_text):
                best_text = text
        if len(best_text) > _MIN_CONTENT_LENGTH:
            return best_text

    # 策略5: 直接 body 文本
    if body:
        text = _extract_text_from_container(body)
        return text

    return ""


def _extract_text_from_container(container: Tag) -> str:
    """从容器的 Tag 中提取清理后的文本

    1. 深拷贝避免影响原始 soup
    2. 递归移除噪音标签和噪音 class/id
    3. 提取纯文本，规范化空白
    """
    # 深拷贝避免影响原始 soup
    clone = copy.copy(container)

    # 递归移除噪音元素
    _remove_noise(clone)

    # 提取文本
    text = clone.get_text(separator="\n", strip=True)

    # 规范化空白：合并连续空行、去除行首尾空白
    lines = []
    for line in text.split("\n"):
        line = line.strip()
        if line:
            lines.append(line)

    # 合并短行（通常是不换行的连续段落被额外切分）
    merged = _merge_short_lines(lines)
    return merged


def _remove_noise(tag: Tag) -> None:
    """递归移除标签树中的噪音元素"""
    # 从 class/id 判断噪音
    if _is_noise_element(tag):
        tag.decompose()
        return

    # 递归处理子元素
    for child in list(tag.children):
        if isinstance(child, Tag):
            if child.name in REMOVE_TAGS:
                child.decompose()
            elif _is_noise_element(child):
                child.decompose()
            else:
                _remove_noise(child)


def _merge_short_lines(lines: list[str], min_len: int = 30) -> str:
    """合并短行：如果一行小于 min_len 且下一行存在，合并到下一行"""
    if not lines:
        return ""
    merged = []
    buffer = ""
    for line in lines:
        if buffer:
            # 如果行以标点结尾，认为段落结束
            if buffer and buffer[-1] in "。！？；.:!?;":
                merged.append(buffer)
                buffer = line
            else:
                # 追加到当前缓冲区
                buffer = buffer + " " + line
        elif len(line) < min_len:
            buffer = line
        else:
            merged.append(line)
    if buffer:
        merged.append(buffer)
    return "\n\n".join(merged)


# ═══════════════════════════════════════════════════════════
#  主入口
# ═══════════════════════════════════════════════════════════

def extract_page(html: str, url: str) -> dict:
    """从 HTML 中提取网页结构化内容

    Args:
        html: 网页原始 HTML
        url: 源 URL（用于提取域名等上下文）

    Returns:
        dict 包含:
            - title: 页面标题
            - url: 原始 URL
            - domain: 来源域名
            - date: 发布日期（如能提取到）
            - body: 正文文本
    """
    # 解析器多级降级：lxml（最快）→ html.parser（内置）→ 抛错
    try:
        soup = BeautifulSoup(html, 'lxml')
    except Exception:
        _logger.warning("lxml 不可用，降级到 html.parser")
        soup = BeautifulSoup(html, 'html.parser')

    # 标题
    title = _extract_title(soup)

    # 域名
    parsed = urlparse(url)
    domain = parsed.netloc

    # 日期
    date = _extract_date(soup)

    # 正文
    body = _extract_main_content(soup)

    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS] + "\n\n... [正文过长已截断]"

    return {
        "title": title,
        "url": url,
        "domain": domain,
        "date": date,
        "body": body,
    }


def _extract_title(soup: BeautifulSoup) -> str:
    """提取页面标题

    优先级: og:title → <title> → <h1>
    """
    # og:title
    og_title = soup.find(_META_KEY, property="og:title") or soup.find(_META_KEY, attrs={"name": "og:title"})
    if og_title and og_title.get("content"):
        return og_title["content"].strip()

    # <title>
    title_tag = soup.find("title")
    if title_tag and title_tag.get_text(strip=True):
        return title_tag.get_text(strip=True)

    # <h1>
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)

    return "(无标题)"


# ── 格式化输出 ─────────────────────────────────────────

def format_fetch_result(data: dict) -> str:
    """将提取的网页内容格式化为可读文本"""
    lines = []
    lines.append(f"标题: {data['title']}")
    lines.append(f"来源: {data['url']}")
    if data['domain']:
        lines.append(f"域名: {data['domain']}")
    if data['date']:
        lines.append(f"发布时间: {data['date']}")

    lines.append("")
    lines.append("─" * 50)
    lines.append("")

    if data['body']:
        lines.append(data['body'])
    else:
        lines.append("(未提取到正文内容)")

    return "\n".join(lines)


async def fetch_page(url: str, client: Optional[object] = None) -> dict:
    """核心入口：获取 URL 的网页内容并提取正文

    Args:
        url: 目标 URL
        client: 可选的 httpx.AsyncClient 实例（用于连接池复用）

    Returns:
        dict，包含 title/url/domain/date/body/error 等字段
    """
    # 安全校验
    error_msg = await _validate_fetch_url(url)
    if error_msg:
        return {"error": error_msg, "url": url}

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    if client is not None:
        resp = await client.get(url, headers=headers, follow_redirects=True)
    else:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=True) as c:
            resp = await c.get(url, headers=headers)

    if resp.status_code != 200:
        return {
            "error": f"(获取网页失败: HTTP {resp.status_code})",
            "url": url,
        }

    # 检查 Content-Type 确保是 HTML
    content_type = resp.headers.get("content-type", "").lower()
    if "text/html" not in content_type and "application/xhtml" not in content_type:
        # 允许一些常见误报
        if not any(t in content_type for t in ("text/", "application/json", "application/xml")):
            return {
                "error": f"(获取网页失败: 非 HTML 内容 - {content_type})",
                "url": url,
            }

    # 自动检测编码（复用 tools.encoding 模块，含二次质量校验）
    html_bytes = resp.content
    encoding = detect_encoding(raw_bytes=html_bytes)

    try:
        html_text = html_bytes.decode(encoding, errors="replace")
    except (LookupError, UnicodeDecodeError):
        html_text = html_bytes.decode("utf-8", errors="replace")

    result = extract_page(html_text, url)
    return result
