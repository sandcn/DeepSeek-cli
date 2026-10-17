"""
对话消息自动保存与恢复。

保存位置: .chat/msg_list/<session_id>.json（项目根目录下）
命令行恢复: python chat.py --load <session_id>

注意：save_session() 会自动过滤 system 角色消息，仅保存 user/assistant/tool 消息。
如需保留 system 消息，调用方需自行拼接。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime
from typing import Any
from pathlib import Path

import logging

from .core.stats import get_token_stats
from .paths import CHAT_MSGS_DIR, ensure_chat_msgs_dir

_logger = logging.getLogger(__name__)

# ── 会话列表缓存 ──────────────────────────────────────────
_session_cache: list[dict[str, Any]] | None = None
_session_cache_mtime: float = 0.0
_SESSION_CACHE_TTL = 30.0  # 秒
_session_cache_lock = threading.Lock()

def _invalidate_session_cache() -> None:
    """使会话列表缓存失效"""
    global _session_cache
    _session_cache = None

# ── 异常 ──────────────────────────────────────────────────
class ExportError(RuntimeError):
    """会话导出失败（目标路径非法 / 文件已存在等**非「会话不存在」**错误）。

    ★ 修复（2026-10）：修复前 ``export_session`` 对「路径越界」「文件已存在」
    与「会话不存在」一律返回 ``None``，调用方只能打印"未找到会话: <id>"——
    对真实存在的会话给出与事实相反的错误提示。现以专用异常区分两类失败。
    """


# ── 会话ID校验 ────────────────────────────────────────────
_SESSION_ID_RE = re.compile(r'^[a-zA-Z0-9_-]+$')

def _content_to_text(content) -> str:
    """将消息 content（str 或 list[dict] content blocks）转换为纯文本。

    委托 ``multimodal.content_to_text`` 作为单一真源（多模态 user/tool 消息
    content 为 OpenAI 兼容 content blocks（text + image_url），标题提取/列
    表摘要等场景需要其中的文本部分）。统一归一化，避免四处行为分叉。
    """
    try:
        from .core.multimodal import content_to_text as _impl
        return _impl(content)
    except Exception:
        # 防御回退（content_to_text 内部已对 str/list/None 做兜底）
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for b in content:
                if isinstance(b, dict):
                    if b.get("type") == "text":
                        t = b.get("text", "")
                        if isinstance(t, str):
                            parts.append(t)
                    elif b.get("type") == "image_url":
                        parts.append("[图片]")
                elif b is not None:
                    parts.append(str(b))
            return " ".join(parts)
        return str(content)

def _validate_session_id(session_id: str) -> str | None:
    """校验 session_id 格式，防止路径遍历。只允许字母数字下划线连字符。"""
    sid = session_id.removesuffix(".json")
    if not sid or not _SESSION_ID_RE.match(sid):
        return None
    return sid


# ── 标题归一化 ────────────────────────────────────────────
#: 标题中的折行/制表等空白（含 Unicode 行分隔符）——压成单个空格。
_TITLE_WS_RE = re.compile(r"[\r\n\t\v\f\u0085\u2028\u2029]+")


def normalize_title(text: str) -> str:
    """会话标题归一化：折行/制表等空白压成单空格并去首尾空白。

    ★ 修复（2026-10）：标题取自首条用户消息前 40 字符（仅 ``strip()``），
    多行输入会把 ``\\n`` 原样写入会话文件——TUI 会话浏览器左栏与
    ``chat.py session list`` 按行渲染时被拆行，列表格式被破坏（实测 304 个
    会话中 86 个标题含换行）。保存 / 重命名 / 摘要读取统一走本函数。
    """
    if not text:
        return ""
    return _TITLE_WS_RE.sub(" ", str(text)).strip()


# ── 会话摘要头部快速解析 ──────────────────────────────────
#: 头部扫描字节数（``save_session`` 把标量字段前置在文件开头）。
_HEAD_SCAN_BYTES = 16384
#: 头部扫描所需字段（缺任一 → 回退全量读取）。
_HEAD_REQUIRED_FIELDS = ("title", "message_count")
_NUMBER_RE = re.compile(r"-?\d+")
_JSON_ESCAPES = {
    '"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f",
    "n": "\n", "r": "\r", "t": "\t",
}


def _scan_json_string(text: str, pos: int) -> tuple[str | None, int]:
    """扫描 ``text[pos]`` 处的 JSON 字符串字面量。

    Returns:
        ``(解码后的字符串, 结束引号后一位)``；扫描失败返回 ``(None, pos)``。
    """
    n = len(text)
    if pos >= n or text[pos] != '"':
        return None, pos
    pos += 1
    out: list[str] = []
    while pos < n:
        ch = text[pos]
        if ch == '"':
            return "".join(out), pos + 1
        if ch == "\\":
            pos += 1
            if pos >= n:
                return None, pos
            esc = text[pos]
            if esc == "u":
                digits = text[pos + 1:pos + 5]
                if len(digits) < 4:
                    return None, pos
                try:
                    out.append(chr(int(digits, 16)))
                except ValueError:
                    return None, pos
                pos += 5
                continue
            out.append(_JSON_ESCAPES.get(esc, esc))
            pos += 1
            continue
        out.append(ch)
        pos += 1
    return None, pos


def _parse_session_head(header: str) -> dict | None:
    """从文件头部文本提取会话摘要标量字段（非严格 JSON 解析）。

    只顺序扫描**顶层标量键值对**（string / number / true / false / null）；
    遇到复合值（``{`` / ``[``）即停止——``save_session`` 把 id/title/model/
    saved_at/message_count 前置在复合字段之前，故无需跳过复合结构。

    Returns:
        摘要数据字典（含 id/title/model/saved_at/message_count/messages=[]）；
        字段不完整（旧格式文件）或头部不可信时返回 None（调用方回退全量读取）。
    """
    text = header.lstrip()
    if not text.startswith("{"):
        return None
    fields: dict[str, Any] = {}
    pos = 1
    n = len(text)
    while pos < n:
        while pos < n and text[pos] in " \t\r\n,":
            pos += 1
        if pos >= n or text[pos] != '"':
            break
        key, pos = _scan_json_string(text, pos)
        if key is None:
            break
        while pos < n and text[pos] in " \t\r\n":
            pos += 1
        if pos >= n or text[pos] != ":":
            break
        pos += 1
        while pos < n and text[pos] in " \t\r\n":
            pos += 1
        if pos >= n:
            break
        ch = text[pos]
        if ch == '"':
            value, pos = _scan_json_string(text, pos)
            if value is None:
                break
            fields[key] = value
        elif ch == "-" or ch.isdigit():
            match = _NUMBER_RE.match(text, pos)
            if match is None:
                break
            fields[key] = int(match.group(0))
            pos = match.end()
        elif text.startswith("true", pos):
            fields[key] = True
            pos += 4
        elif text.startswith("false", pos):
            fields[key] = False
            pos += 5
        elif text.startswith("null", pos):
            fields[key] = None
            pos += 4
        else:
            break  # 复合值 / 未知 token：停止（后续字段不在可提取范围内）
        if key == _HEAD_REQUIRED_FIELDS[-1]:
            break
    if any(field not in fields for field in _HEAD_REQUIRED_FIELDS):
        return None
    return {
        "id": fields.get("id", ""),
        "title": fields.get("title", ""),
        "model": fields.get("model", "?"),
        "saved_at": fields.get("saved_at", "?"),
        "message_count": fields.get("message_count", 0),
        # 头部快路径不读取 messages（列表只需标量字段）
        "messages": [],
    }


# ── 头部消息预览（会话浏览器预览行的轻量来源） ────────────
#: 预览扫描字节上限（只需前若干条消息，避免为预览整读大文件）。
_PREVIEW_SCAN_BYTES = 262144
#: 头部解析用 JSON 解码器（``raw_decode`` 跳过复合字段 / 逐条解析消息元素）。
_HEAD_DECODER = json.JSONDecoder()


def _find_top_level_value(text: str, target_key: str) -> int | None:
    """顺序扫描顶层键，返回 ``target_key`` 对应**值**的起始下标。

    只按顶层键匹配——字符串值整体经 ``_scan_json_string`` 跳过，故标题等
    字符串里出现 ``"messages"`` 不会误判；遇到无法跳过的复合值（在目标键
    之前）或文本截断时返回 None（调用方回退全量读取）。
    """
    n = len(text)
    pos = 0
    while pos < n and text[pos] in " \t\r\n":
        pos += 1
    if pos >= n or text[pos] != "{":
        return None
    pos += 1
    while pos < n:
        while pos < n and text[pos] in " \t\r\n,":
            pos += 1
        if pos >= n or text[pos] != '"':
            return None
        key, pos = _scan_json_string(text, pos)
        if key is None:
            return None
        while pos < n and text[pos] in " \t\r\n":
            pos += 1
        if pos >= n or text[pos] != ":":
            return None
        pos += 1
        while pos < n and text[pos] in " \t\r\n":
            pos += 1
        if pos >= n:
            return None
        if key == target_key:
            return pos
        ch = text[pos]
        if ch == '"':
            value, end = _scan_json_string(text, pos)
            if value is None:
                return None
            pos = end
        elif ch == "-" or ch.isdigit():
            match = _NUMBER_RE.match(text, pos)
            if match is None:
                return None
            pos = match.end()
        elif text.startswith("true", pos):
            pos += 4
        elif text.startswith("false", pos):
            pos += 5
        elif text.startswith("null", pos):
            pos += 4
        else:
            # 复合值（{ / [）：整体解析以跳过（头部范围内）；截断/异常 → 放弃
            try:
                _, end = _HEAD_DECODER.raw_decode(text, pos)
            except ValueError:
                return None
            pos = end
    return None


def _parse_head_messages(header: str, max_messages: int) -> list | None:
    """从头部文本增量解析 ``messages`` 数组的前 ``max_messages`` 个元素。

    Returns:
        消息列表（数组已结束则返回实际条数）；单条消息超出扫描范围 /
        结构异常 → None（调用方回退全量读取）。
    """
    if max_messages <= 0:
        return []
    start = _find_top_level_value(header, "messages")
    if start is None or header[start] != "[":
        return None
    n = len(header)
    pos = start + 1
    decoder = json.JSONDecoder()
    out: list = []
    while True:
        while pos < n and header[pos] in " \t\r\n,":
            pos += 1
        if pos >= n:
            return None
        if header[pos] == "]":
            return out
        if len(out) >= max_messages:
            return out
        try:
            value, end = decoder.raw_decode(header, pos)
        except ValueError:
            return None
        out.append(value)
        pos = end


def load_session_head_messages(session_id: str, max_messages: int) -> list | None:
    """读取会话**前若干条消息**（预览轻量路径，不整读大文件）。

    ★ 修复（2026-10）：会话浏览器预览此前为每个会话调用 ``load_session``
    （整文件解析 304 个会话 / 2.8GB，首屏卡顿数十秒）；预览只需前若干条消息，
    现改为读文件头部增量解析。

    Returns:
        消息列表；文件较小（<= 256KB）时等价完整读取；无法从头部解析时返回
        None（调用方回退 ``load_session`` 全量读取）。
    """
    sid = _validate_session_id(session_id)
    if sid is None:
        return None
    filepath = CHAT_MSGS_DIR / f"{sid}.json"
    try:
        size = filepath.stat().st_size
    except OSError:
        return None
    try:
        if size <= _PREVIEW_SCAN_BYTES:
            data = json.loads(filepath.read_text(encoding="utf-8"))
            messages = data.get("messages")
            return list(messages[:max_messages]) if isinstance(messages, list) else None
        with open(filepath, "r", encoding="utf-8") as fh:
            header = fh.read(_PREVIEW_SCAN_BYTES)
    except (OSError, ValueError):
        return None
    return _parse_head_messages(header, max_messages)


# ── 会话摘要磁盘索引（旧文件免重复全量解析） ──────────────
#: 索引文件名（存于会话目录的**父目录**，避免与 ``*.json`` 会话文件混淆——
#: ``pathlib.Path.glob("*.json")`` 会匹配以 ``.`` 开头的隐藏文件，
#: 放在 msg_list 内会被当成会话列出）。
_INDEX_FILE_NAME = ".session_index.json"
_INDEX_VERSION = 1


def _index_path() -> Path:
    return CHAT_MSGS_DIR.parent / _INDEX_FILE_NAME


def _load_index() -> dict:
    """读取会话摘要磁盘索引（缺失/损坏/版本不符 → 空索引）。"""
    try:
        data = json.loads(_index_path().read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != _INDEX_VERSION:
        return {}
    files = data.get("files")
    return files if isinstance(files, dict) else {}


def _save_index(files: dict) -> None:
    """原子写回会话摘要索引（失败仅记日志——索引只是加速，不影响正确性）。"""
    path = _index_path()
    tmp = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(
            json.dumps({"version": _INDEX_VERSION, "files": files}, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError:
        _logger.debug("写入会话摘要索引失败（忽略）", exc_info=True)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass








# ── 生成唯一 ID ───────────────────────────────────────────
def generate_id() -> str:
    """基于当前时间戳的 MD5 生成唯一会话 ID"""
    raw = f"{time.time_ns()}_{os.urandom(4).hex()}"
    return hashlib.md5(raw.encode()).hexdigest()


# ── 终端窗口标题同步 ──────────────────────────────────────
def _sync_terminal_title(title: str) -> None:
    """同步会话标题到终端窗口标题（OSC 序列）。

    会话保存生成标题后调用，使 Termux / 桌面终端窗口标签显示当前会话主题。
    延迟导入 ``set_window_title`` 避免数据层→UI 层模块级依赖（与
    ``publish_output`` 同模式）；无 TTY / 异常时静默失败（非关键路径）。
    """
    if not title:
        return
    try:
        from .tui._screen import set_window_title
        set_window_title(title)
    except Exception:
        _logger.debug("同步终端窗口标题失败（非关键）", exc_info=True)


# ── 保存对话 ──────────────────────────────────────────────
def save_session(messages: list[dict], model: str, session_id: str | None = None,
                 subagents: list | None = None, session_log: list | None = None) -> str:
    """保存对话到 .chat/msg_list/<id>.json

    自动过滤 system 角色消息，仅保留 user/assistant/tool 对话消息。

    Args:
        messages: 消息列表（将自动剔除 system 消息）。
            注意：system 角色消息会被自动过滤（不保存），如需保存 system 消息请调用方自行拼接
        model: 模型名称
        session_id: 指定 ID，为 None 则自动生成
        subagents: SubAgent 任务记录列表（含每个 subagent 的完整聊天信息），
            由 SubAgent._record_to_parent() 收集、/export 命令消费。
            None 时保存为空列表（旧会话兼容）。
        session_log: 会话事件日志快照（仅追加事实源）。定时自动保存 / 退出
            保存路径均传入当前日志快照；None 时不写入该字段（兼容旧调用方），
            显式传入时持久化，加载时据此重建日志，实现 fork / 回放 / 一致性校验。

    Returns:
        保存的会话 ID
    """
    ensure_chat_msgs_dir()
    # ★ P1（review）：与其他会话函数一致做路径校验——session_id 非法/含
    #   ../ 等路径穿越字符时回退到自动生成 id，避免越权写入任意路径。
    sid = _validate_session_id(session_id) if session_id else None
    if sid is None:
        sid = generate_id()
    filepath = CHAT_MSGS_DIR / f"{sid}.json"

    # 有意过滤 system 消息（标题提取也用 filtered），调用方需自行拼接 system 消息
    filtered = [m for m in messages if m.get("role") != "system"]

    try:
        stats = get_token_stats()
    except Exception:
        _logger.exception("获取 token 统计失败，使用空统计")
        stats = {"input": 0, "output": 0, "total": 0}
    # ── 标题策略 ───────────────────────────────────────────
    # 1) 已有会话文件且已有标题（用户重命名）→ 保留，不覆盖
    # 2) 否则从首条 user 消息截断生成（即时 fallback）
    title = ""
    if session_id:
        existing = load_session(session_id)
        if existing and existing.get("title"):
            title = existing["title"]
    if not title:
        # 基于 filtered 提取标题（跳过 system 消息），保持与保存内容一致
        for m in filtered:
            if m.get("role") == "user":
                # content 可能为 list（多模态 content blocks）——提取文本部分；
                # ★ 修复（2026-10）：折行/制表符归一化为空格——多行首条消息
                #   曾把 \n 原样写进标题，破坏会话列表与 CLI 输出格式。
                content = normalize_title(_content_to_text(m.get("content")))
                title = content[:40]
                if len(content) > 40:
                    title += "…"
                break
    # 用户重命名 / 旧文件残留的标题同样归一化（列表渲染防御）
    title = normalize_title(title)

    # ★ 起完标题后同步终端窗口标题（OSC 序列，无 TTY 静默失败）
    _sync_terminal_title(title)

    data = {
        "id": sid,
        "title": title,
        "model": model,
        "saved_at": datetime.now().isoformat(),
        # ★ 修复（2026-10）：消息数前置为**标量字段**（位于 token_stats /
        #   messages 复合字段之前）——会话列表可只读文件头部标量字段构建
        #   摘要（见 ``_parse_session_head``），避免为拿 message_count 而
        #   全量解析整个会话文件（实测 2.8GB / 20s+）。
        "message_count": len(filtered),
        "token_stats": dict(stats),
        "messages": filtered,
        "subagents": list(subagents) if subagents else [],
    }
    # 传入日志快照时写入 session_log 字段（定时自动保存 / 退出保存均传入）；
    #   未传（None，兼容旧调用方）时不写该字段，加载端统一归一化为空列表。
    if session_log is not None:
        data["session_log"] = list(session_log)

    try:
        filepath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        _logger.error("保存会话文件失败: path=%s, sid=%s, error=%s", filepath, sid, exc)
        raise
    _invalidate_session_cache()
    return sid


# ── 加载对话 ──────────────────────────────────────────────
def load_session(session_id: str) -> dict[str, Any] | None:
    """从 .chat/msg_list/<id>.json 加载对话

    Args:
        session_id: 会话 ID（可带或不带 .json 后缀）

    Returns:
        数据字典包含 id/model/messages/token_stats/saved_at/subagents，
        不存在返回 None。
        旧会话文件缺少 subagents 字段时归一化为空列表。
    """
    sid = _validate_session_id(session_id)
    if sid is None:
        return None
    filepath = CHAT_MSGS_DIR / f"{sid}.json"

    if not filepath.exists():
        return None

    try:
        data = json.loads(filepath.read_text(encoding="utf-8"))
        # 归一化：旧会话文件可能缺少 subagents / session_log 字段
        data.setdefault("subagents", [])
        data.setdefault("session_log", [])
        return data
    except (json.JSONDecodeError, OSError):
        return None


# ── 会话摘要读取（轻量） ─────────────────────────────────
def _load_session_summary(filepath: Path) -> dict | None:
    """读取会话摘要数据（头部标量字段优先，失败回退全量解析）。

    ★ 修复（2026-10）：修复前只读前 16384 字节并直接 ``json.loads``——大于
    16KB 的会话文件 JSON 必然被截断、解析必败（实测 304 个会话 0 命中），
    轻量路径形同虚设（每次列表都全量读取 2.8GB / 20s+）。现改为从头部
    **提取**顶层标量字段（id/title/model/saved_at/message_count）；旧格式
    文件缺 ``message_count`` 字段时回退全量读取。
    """
    header = ""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            header = f.read(_HEAD_SCAN_BYTES)
    except (OSError, ValueError):
        header = ""
    if header:
        head = _parse_session_head(header)
        if head is not None:
            return head
    try:
        return json.loads(filepath.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError, ValueError):
        # ★ P1 修复：缩小异常范围，只捕获与 JSON 解析和 IO 相关的异常，
        #   避免意外吞掉 MemoryError、KeyboardInterrupt 等严重异常。
        return None


# ── 列出所有保存的会话 ────────────────────────────────────
def _session_summary_entry(data: dict, filepath: Path) -> dict[str, Any]:
    """会话数据 → 摘要条目（id/title/model/saved_at/message_count）。"""
    title = normalize_title(data.get("title") or "")
    # 兼容旧会话：没有 title 字段则从首条 user 消息提取
    if not title:
        for m in data.get("messages", []) or []:
            if isinstance(m, dict) and m.get("role") == "user":
                # content 可能为 list（多模态 content blocks）——提取文本
                content = normalize_title(_content_to_text(m.get("content")))
                title = content[:40] + ("…" if len(content) > 40 else "")
                break
    # message_count：新格式文件前置标量字段优先（头部快路径已带；messages
    # 可能未加载故不能仅依赖 len(messages)）
    count = data.get("message_count")
    if not isinstance(count, int) or count < 0:
        count = len(data.get("messages", []))
    return {
        "id": data.get("id", filepath.stem),
        "title": title,
        "model": data.get("model", "?"),
        "saved_at": data.get("saved_at", "?"),
        "message_count": count,
    }


def iter_sessions():
    """逐条产出会话摘要（流式版 ``list_sessions``，界面可增量显示）。

    与 ``list_sessions`` 同源同缓存：缓存有效（TTL 内）时逐条 yield 快照；
    未命中时逐文件解析，**每解析出一条即 yield**（调用方可即时显示一条），
    全部结束后写回缓存（``saved_at`` 降序）。调用方提前中断（不耗尽生成器）
    时不写缓存——下次重新读取。

    性能（2026-10）：
      - 头部标量字段快速读取（``_parse_session_head``）——新格式文件无需
        全量解析即可构建摘要；
      - 磁盘索引（``.session_index.json``，按 ``(size, mtime_ns)`` 失效）——
        旧格式文件首次全量解析后缓存，后续列表命中索引（实测 20s+ → <1s）。

    ★ 修复（2026-10，死锁）：缓存命中分支**先在锁内取快照、再在锁外 yield**
    ——修复前在 ``with _session_cache_lock`` 块内直接 ``yield``：生成器挂起
    期间锁不释放，调用方在迭代过程中重入任何加锁路径（同线程调用
    ``list_sessions`` 等）即永久死锁（``threading.Lock`` 不可重入）。
    """
    global _session_cache, _session_cache_mtime
    now = time.time()
    cached: list | None = None
    with _session_cache_lock:
        if _session_cache is not None and (now - _session_cache_mtime) < _SESSION_CACHE_TTL:
            cached = list(_session_cache)
    if cached is not None:
        # 锁外 yield（锁已释放）——调用方可在迭代中安全调用其它会话函数
        for item in cached:
            yield item
        return

    if not CHAT_MSGS_DIR.exists():
        with _session_cache_lock:
            _session_cache = []
            _session_cache_mtime = now
        return
    index = _load_index()
    fresh_index: dict = {}
    sessions: list[dict[str, Any]] = []
    for f in CHAT_MSGS_DIR.glob("*.json"):
        # 跳过隐藏文件（``Path.glob`` 与 ``glob`` 模块不同，会匹配 ``.`` 开头的
        # 名字——防御性排除索引/临时文件）
        if f.name.startswith("."):
            continue
        # 检查文件可读性，不可读则跳过
        if not os.access(f, os.R_OK):
            continue
        try:
            stat = f.stat()
        except OSError:
            continue
        sig = [int(stat.st_size), int(stat.st_mtime_ns)]
        entry: dict | None = None
        cached_item = index.get(f.name)
        if isinstance(cached_item, dict) and cached_item.get("sig") == sig:
            candidate = cached_item.get("entry")
            if isinstance(candidate, dict):
                entry = candidate
        if entry is None:
            data = _load_session_summary(f)
            if data is None:
                continue
            entry = _session_summary_entry(data, f)
        fresh_index[f.name] = {"sig": sig, "entry": entry}
        sessions.append(entry)
        yield entry
    # 按 saved_at 降序排列（最新在前）；缺失 saved_at 的旧会话排到最后。
    sessions.sort(key=lambda s: s.get("saved_at", "") or "", reverse=True)
    # 索引只保留本轮实际扫描到的文件（已删除文件自动出清）
    _save_index(fresh_index)
    with _session_cache_lock:
        _session_cache = sessions
        _session_cache_mtime = now


def list_sessions() -> list[dict[str, Any]]:
    """列出 .chat/msg_list/ 下所有保存的会话摘要

    返回按保存时间降序排列的列表，每项含 id/title/model/saved_at/message_count。
    流式逐条读取见 ``iter_sessions``（同源同缓存；流式产出顺序为读取顺序，
    本函数在收集后统一按 ``saved_at`` 降序排列）。
     """
    sessions = list(iter_sessions())
    sessions.sort(key=lambda s: s.get("saved_at", "") or "", reverse=True)
    return sessions


# ── 删除会话文件 ──────────────────────────────────────────
def delete_session(session_id: str) -> bool:
    """删除指定 ID 的会话文件，返回是否确实删除了文件"""
    sid = _validate_session_id(session_id)
    if sid is None:
        return False
    filepath = CHAT_MSGS_DIR / f"{sid}.json"
    if not filepath.exists():
        return False
    filepath.unlink()
    _invalidate_session_cache()
    return not filepath.exists()


# ── 获取恢复命令 ─────────────────────────────────────────
def get_recover_cmd(session_id: str, script: str = "chat.py") -> str:
    """获取恢复会话的命令行提示"""
    return f"python {script} --load {session_id}"


# ── 重命名会话 ────────────────────────────────────────────
def rename_session(session_id: str, new_title: str) -> bool:
    """重命名指定会话的标题。

    Args:
        session_id: 会话 ID
        new_title: 新标题

    Returns:
        是否成功
    """
    sid = _validate_session_id(session_id)
    if sid is None:
        return False
    filepath = CHAT_MSGS_DIR / f"{sid}.json"
    if not filepath.exists():
        return False
    try:
        data = json.loads(filepath.read_text(encoding="utf-8"))
        # ★ 修复（2026-10）：重命名同样归一化折行/制表符（标题按行渲染）
        data["title"] = normalize_title(new_title)
        filepath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        _invalidate_session_cache()
        return True
    except (json.JSONDecodeError, OSError):
        return False


# ── 导出会话 ────────────────────────────────────────────
def export_session(session_id: str, output: str | None = None) -> str | None:
    """导出会话到文件或 stdout。

    Args:
        session_id: 会话 ID（可带或不带 .json 后缀）
        output: 输出文件路径，为 None 时返回 JSON 字符串

    Returns:
        指定 output 时返回文件路径，
        未指定 output 时返回 JSON 字符串，
        会话不存在时返回 None。

    Raises:
        ExportError: 导出目标非法（路径越出当前目录 / 文件已存在）——
            ★ 修复（2026-10）：修复前此类失败与「会话不存在」同为 ``None``，
            调用方只能提示"未找到会话"（对存在的会话给出相反结论）。
    """
    data = load_session(session_id)
    if data is None:
        return None

    json_str = json.dumps(data, ensure_ascii=False, indent=2)
    if output:
        out_path = Path(output).resolve()
        # 安全校验：只允许在当前工作目录下写入
        cwd = Path.cwd()
        try:
            out_path.relative_to(cwd)
        except ValueError:
            raise ExportError(f"导出路径必须在当前目录下: {out_path}")
        if out_path.exists():
            raise ExportError(f"文件已存在: {out_path}")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json_str, encoding='utf-8')
        return str(out_path)
    return json_str
