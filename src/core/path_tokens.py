"""拖放 / 粘贴文件路径解析与规范化（核心层纯逻辑，仅标准库）。

终端（mintty / Windows Terminal / VS Code 终端 / Web 终端等）在用户把文件
从文件管理器拖入窗口时，会把文件路径以「粘贴」形式注入 stdin；不同终端的
注入格式差异很大：

  - 反斜杠转义：``/home/me/a\\ b.txt``（mintty 默认）
  - 单引号包裹：``'/home/me/a b.txt'``
  - 双引号包裹：``"C:\\Program Files\\a.txt"``（Windows Terminal）
  - ``file://`` URI：``file:///C:/Program%20Files/a.txt``（VS Code）
  - Windows 原生路径：``C:\\Users\\me\\a.txt``（资源管理器拖入）

本模块把这些形态统一解析为「干净的本地路径列表」，供两处消费：

  - TUI 输入框（``src.tui._input_dispatcher``）插入前规范化拖放文本；
  - 用户消息图片引用识别（``src.core.multimodal.extract_image_refs``）
    识别带引号 / 转义 / file URI 的图片路径。

分层：核心层纯逻辑，不依赖表现层——TUI 与 ``core.multimodal`` 共用同一
实现，避免双实现漂移；文件/目录存在性经 ``exists`` 参数注入，便于测试。
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import unquote, urlsplit

__all__ = [
    "PathToken",
    "iter_path_tokens",
    "normalize_token",
    "split_dropped_paths",
    "normalize_dropped_paths",
    "looks_like_path",
    "file_uri_to_path",
    "to_posix_path",
    "quote_path",
    "needs_quote",
    "strip_trailing_punct",
]

#: 裸 token 中反斜杠可转义的字符（shell 风格）。不含字母/数字——保证 Windows
#: 路径分隔符 ``\``（如 ``C:\Users``）不被误当作转义序列吞掉。
_SHELL_ESCAPABLE = frozenset(" \t\\'\"()[]{}!&;|<>*?$`#~=%")

#: 输出时需要引号包裹的字符（空格与 shell 元字符）。不含 ``/``、``.``、
#: ``-``、``_``、``:`` 等路径常规字符。
_QUOTE_TRIGGER = frozenset(" \t()[]{}!&;|<>*?$`#\"'")

#: 路径尾部可能被句子标点粘连的字符（图片引用识别兼容旧行为）。
_TRAILING_PUNCT = ",;:。，；：、"

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_WINDOWS_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")
_FILE_URI_RE = re.compile(r"^file:", re.IGNORECASE)

#: 形似（绝对）路径的前缀——配合 ``exists`` 判定「是否拖放路径」。
_ABS_PREFIXES = ("/", "~", "./", "../", ".\\", "\\\\", "//")

#: 拖放路径识别规模上限——超过则视为普通粘贴（逐 token stat 的开销防御）。
#: 上限取较大值以容纳一次拖入多个长路径文件；大段文本粘贴（含大量空白）
#: 在解析前即被空白计数快速拒绝，避免 O(n) 切分与逐 token stat。
_MAX_DROP_TEXT_CHARS = 16384
_MAX_DROP_TOKENS = 128


@dataclass(frozen=True)
class PathToken:
    """切分出的一个 token（保留原文与在源文本中的字符区间）。"""

    #: 原始文本片段（含引号，如 ``'/a b.txt'``）。
    raw: str
    #: 去引号后的内容（未做转义/URI 规范化）。
    text: str
    #: 是否由引号包裹。
    quoted: bool
    #: 源文本起始索引（含）。
    start: int
    #: 源文本结束索引（含）。
    end: int


def iter_path_tokens(text: str) -> list[PathToken]:
    """把文本切分为候选路径 token（状态机，尊重引号与 shell 转义）。

    - 空白分隔 token；单/双引号包裹整体（引号内空白不切分）；
    - 裸 token 中 ``\\`` + shell 特殊字符（空格/引号/括号等）还原为字面量，
      字母/数字前的 ``\\`` 保留（Windows 路径分隔符）；
    - ``\\\\`` 开头的 UNC 路径不做转义还原（反斜杠为分隔符）。

    Returns:
        token 列表；引号未闭合（非拖放文本）时返回空列表，调用方原样处理。
    """
    tokens: list[PathToken] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        start = i
        if ch in ("'", '"'):
            quote = ch
            i += 1
            buf: list[str] = []
            closed = False
            while i < n:
                cur = text[i]
                if (
                    quote == '"'
                    and cur == "\\"
                    and i + 1 < n
                    and text[i + 1] in "\"\\$`"
                ):
                    buf.append(text[i + 1])
                    i += 2
                    continue
                if cur == quote:
                    closed = True
                    i += 1
                    break
                buf.append(cur)
                i += 1
            if not closed:
                return []
            tokens.append(PathToken(
                raw=text[start:i], text="".join(buf), quoted=True,
                start=start, end=i - 1,
            ))
            continue
        is_unc = text.startswith("\\\\", i)
        buf = []
        while i < n and not text[i].isspace():
            cur = text[i]
            if (
                not is_unc
                and cur == "\\"
                and i + 1 < n
                and text[i + 1] in _SHELL_ESCAPABLE
            ):
                buf.append(text[i + 1])
                i += 2
                continue
            buf.append(cur)
            i += 1
        tokens.append(PathToken(
            raw=text[start:i], text="".join(buf), quoted=False,
            start=start, end=i - 1,
        ))
    return tokens


def file_uri_to_path(uri: str) -> str:
    """``file://`` URI → 本地路径（百分号解码、Windows 盘符还原）。

    ``file:///C:/a%20b.txt`` → ``C:/a b.txt``；
    ``file://server/share/a.txt`` → ``//server/share/a.txt``。
    非 file URI 返回空串。
    """
    try:
        parts = urlsplit(uri)
    except ValueError:
        return ""
    if parts.scheme.lower() != "file":
        return ""
    path = unquote(parts.path or "")
    netloc = unquote(parts.netloc or "")
    if netloc and netloc.lower() != "localhost":
        return f"//{netloc}{path}"
    if len(path) >= 3 and path[0] == "/" and _WINDOWS_DRIVE_RE.match(path[1:]):
        path = path[1:]
    if not path and netloc:
        return "/"
    return path


def _drive_mount(platform: str, exists: Callable[[str], bool]) -> str:
    """盘符挂载前缀（``/cygdrive``、``/mnt``、``/`` 或 ``""``）。"""
    if platform.startswith("msys"):
        return ""
    for cand in ("/cygdrive", "/mnt", ""):
        try:
            if exists(os.path.join(cand or "/", "c")):
                return cand
        except (OSError, ValueError):
            continue
    return "/cygdrive"


def _split_windows_parts(rest: str) -> list[str]:
    """按 ``\\`` / ``/`` 切分 Windows 路径片段（``\\ ` 转义空格保留）。"""
    parts: list[str] = []
    cur: list[str] = []
    i = 0
    n = len(rest)
    while i < n:
        ch = rest[i]
        if ch == "\\" and i + 1 < n and rest[i + 1] in " \t":
            cur.append(rest[i + 1])
            i += 2
            continue
        if ch in ("\\", "/"):
            if cur:
                parts.append("".join(cur))
                cur = []
            i += 1
            continue
        cur.append(ch)
        i += 1
    if cur:
        parts.append("".join(cur))
    return parts


def to_posix_path(
    path: str,
    *,
    exists: Optional[Callable[[str], bool]] = None,
    platform: Optional[str] = None,
) -> str:
    """Windows 原生路径 → 当前 POSIX 运行时（Cygwin/MSYS）可读路径。

    仅当运行平台为 cygwin / msys 时转换（其他平台原样返回，避免无意义改写）：

      - ``C:\\Users\\me\\a.txt`` → ``/cygdrive/c/Users/me/a.txt``；
      - ``\\\\server\\share\\a.txt`` → ``//server/share/a.txt``。

    Args:
        path: 待转换路径。
        exists: 存在性判定（注入取默认 ``os.path.exists``；测试可替换）。
        platform: 平台标识（注入取默认 ``sys.platform``；测试可替换）。
    """
    if not path:
        return path
    probe = exists if exists is not None else os.path.exists
    plat = (platform if platform is not None else sys.platform).lower()
    if not (plat.startswith("cygwin") or plat.startswith("msys")):
        return path
    if path.startswith("\\\\"):
        parts = _split_windows_parts(path[2:])
        return "//" + "/".join(parts) if parts else path
    match = _WINDOWS_DRIVE_PREFIX_RE.match(path)
    if not match:
        return path
    parts = _split_windows_parts(path[match.end():])
    if not parts:
        return path
    mount = _drive_mount(plat, probe)
    drive = path[0].lower()
    return f"{mount}/{drive}/" + "/".join(parts)


def normalize_token(
    text: str,
    *,
    exists: Optional[Callable[[str], bool]] = None,
    platform: Optional[str] = None,
) -> str:
    """单个 token → 规范化本地路径（file URI 解析 + Windows→POSIX 转换）。"""
    if not text:
        return ""
    if _FILE_URI_RE.match(text):
        path = file_uri_to_path(text)
        if not path:
            return ""
    else:
        path = text
    return to_posix_path(path, exists=exists, platform=platform)


def looks_like_path(
    path: str,
    *,
    exists: Optional[Callable[[str], bool]] = None,
) -> bool:
    """判断文本是否像本地路径（存在，或形似绝对/相对路径）。"""
    if not path or "\n" in path or "\r" in path:
        return False
    if path.startswith("-"):
        return False
    probe = exists if exists is not None else os.path.exists
    expanded = os.path.expanduser(path)
    try:
        if probe(expanded):
            return True
    except (OSError, ValueError):
        pass
    if path.startswith(_ABS_PREFIXES):
        return True
    return bool(_WINDOWS_DRIVE_RE.match(path))


def needs_quote(path: str) -> bool:
    """路径是否含需要引号包裹的字符（空格 / shell 元字符）。"""
    return any(ch in _QUOTE_TRIGGER for ch in path)


def strip_trailing_punct(text: str) -> str:
    """剥离路径后粘连的句子标点（``/a/b.png，`` → ``/a/b.png``）。"""
    return text.rstrip(_TRAILING_PUNCT)


def quote_path(path: str) -> str:
    """路径需要时用引号包裹（优先双引号；含双引号退化单引号，再退化转义）。"""
    if not path or not needs_quote(path):
        return path
    if '"' not in path:
        return f'"{path}"'
    if "'" not in path:
        return f"'{path}'"
    return '"' + path.replace('"', '\\"') + '"'


def split_dropped_paths(
    text: str,
    *,
    exists: Optional[Callable[[str], bool]] = None,
    platform: Optional[str] = None,
) -> Optional[list[str]]:
    """解析「拖放路径文本」为规范化路径列表。

    仅当**每个 token 都像本地路径**时才判定为拖放文本；只要有一个 token
    不像路径（如用户粘贴的自然语言），整体返回 None —— 调用方应原样插入，
    不做任何改写。

    规模上限（防大段文本粘贴时逐 token stat 拖慢输入路径）：超过
    ``_MAX_DROP_TEXT_CHARS`` 字符或 ``_MAX_DROP_TOKENS`` 个 token 时直接
    返回 None（拖放路径文本远小于该规模）。

    Returns:
        规范化路径列表（非空）；非拖放文本返回 None。
    """
    if not text or len(text) > _MAX_DROP_TEXT_CHARS:
        return None
    # 解析前快速拒绝：token 数不超过「空白数 + 1」，空白已超上限时无需切分
    # （大段文本/代码粘贴的高频路径零解析开销）。
    if (text.count(" ") + text.count("\n") + text.count("\t")
            + text.count("\r") + 1) > _MAX_DROP_TOKENS:
        return None
    tokens = iter_path_tokens(text)
    if not tokens or len(tokens) > _MAX_DROP_TOKENS:
        return None
    probe = exists if exists is not None else os.path.exists
    paths: list[str] = []
    for token in tokens:
        path = normalize_token(token.text, exists=probe, platform=platform)
        if not path or not looks_like_path(path, exists=probe):
            return None
        paths.append(path)
    return paths


def normalize_dropped_paths(
    text: str,
    *,
    exists: Optional[Callable[[str], bool]] = None,
    platform: Optional[str] = None,
    multiline: bool = True,
) -> Optional[str]:
    """规范化拖放路径文本（单个原样 / 多个默认每行一个，含空格加引号）。

    Args:
        text: 终端注入的粘贴文本。
        exists: 存在性判定（注入，测试可替换）。
        platform: 平台标识（注入，测试可替换）。
        multiline: 多路径时的分隔——True 每行一个，False 空格分隔。

    Returns:
        规范化文本；非拖放文本返回 None（调用方原样插入）。
    """
    paths = split_dropped_paths(text, exists=exists, platform=platform)
    if not paths:
        return None
    if len(paths) == 1:
        return quote_path(paths[0])
    sep = "\n" if multiline else " "
    return sep.join(quote_path(path) for path in paths)
