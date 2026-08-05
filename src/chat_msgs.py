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

from .api.stats import get_token_stats
from .tui.events.consumers import publish_output
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

# ── 会话ID校验 ────────────────────────────────────────────
_SESSION_ID_RE = re.compile(r'^[a-zA-Z0-9_-]+$')

def _validate_session_id(session_id: str) -> str | None:
    """校验 session_id 格式，防止路径遍历。只允许字母数字下划线连字符。"""
    sid = session_id.removesuffix(".json")
    if not sid or not _SESSION_ID_RE.match(sid):
        return None
    return sid








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
                 subagents: list | None = None) -> str:
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

    Returns:
        保存的会话 ID
    """
    ensure_chat_msgs_dir()
    sid = session_id or generate_id()
    filepath = CHAT_MSGS_DIR / f"{sid}.json"

    # 有意过滤 system 消息（标题提取也用 filtered），调用方需自行拼接 system 消息
    filtered = [m for m in messages if m.get("role") != "system"]

    try:
        stats = get_token_stats()
    except Exception:
        _logger.exception("获取 token 统计失败，使用空统计")
        stats = {"input": 0, "output": 0, "total": 0}
    # ── 标题策略 ───────────────────────────────────────────
    # 1) 已有会话文件且已有标题（AI 生成 / 用户重命名）→ 保留，不覆盖
    # 2) 否则从首条 user 消息截断生成（即时 fallback，
    #    后台 AI 标题生成完成后经 rename_session 覆盖）
    title = ""
    if session_id:
        existing = load_session(session_id)
        if existing and existing.get("title"):
            title = existing["title"]
    if not title:
        # 基于 filtered 提取标题（跳过 system 消息），保持与保存内容一致
        for m in filtered:
            if m.get("role") == "user":
                content = (m.get("content") or "").strip()
                title = content[:40]
                if len(content) > 40:
                    title += "…"
                break

    # ★ 起完标题后同步终端窗口标题（OSC 序列，无 TTY 静默失败）
    _sync_terminal_title(title)

    data = {
        "id": sid,
        "title": title,
        "model": model,
        "saved_at": datetime.now().isoformat(),
        "token_stats": dict(stats),
        "messages": filtered,
        "subagents": list(subagents) if subagents else [],
    }

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
        # 归一化：旧会话文件可能缺少 subagents 字段
        data.setdefault("subagents", [])
        return data
    except (json.JSONDecodeError, OSError):
        return None


# ── 会话摘要读取（轻量） ─────────────────────────────────
def _load_session_summary(filepath: Path) -> dict | None:
    """从文件头部快速提取会话摘要（不加载整个文件）。

    只读取前 16384 字节，适合提取 id/title/model/saved_at 等元数据字段。
    解析失败时 fallback 到完整读取。
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            header = f.read(16384)
        data = json.loads(header)
        return data
    except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError):
        # ★ P1 修复：缩小异常范围，只捕获与 JSON 解析和 IO 相关的异常，
        #   避免意外吞掉 MemoryError、KeyboardInterrupt 等严重异常。
        #   fallback 到完整读取（兼容小文件或格式特殊的情况）
        try:
            return json.loads(filepath.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError):
            return None


# ── 列出所有保存的会话 ────────────────────────────────────
def list_sessions() -> list[dict[str, Any]]:
    """列出 .chat/msg_list/ 下所有保存的会话摘要

    返回按保存时间降序排列的列表，每项含 id/title/model/saved_at/message_count。
    使用 threading.Lock 保护缓存读写，防止并发场景下的竞态条件。
    """
    global _session_cache, _session_cache_mtime
    now = time.time()
    with _session_cache_lock:
        if _session_cache is not None and (now - _session_cache_mtime) < _SESSION_CACHE_TTL:
            return list(_session_cache)

    if not CHAT_MSGS_DIR.exists():
        with _session_cache_lock:
            _session_cache = []
            _session_cache_mtime = now
        return []
    sessions = []
    for f in CHAT_MSGS_DIR.glob("*.json"):
        # 检查文件可读性，不可读则跳过
        if not os.access(f, os.R_OK):
            continue
        data = _load_session_summary(f)
        if data is None:
            continue
        title = data.get("title")
        # 兼容旧会话：没有 title 字段则从消息中提取
        if not title:
            title = ""
        sessions.append({
            "id": data.get("id", f.stem),
            "title": title,
            "model": data.get("model", "?"),
            "saved_at": data.get("saved_at", "?"),
            "message_count": len(data.get("messages", [])),
        })
    # ★ Bug 修复：按 saved_at 降序排列（最新在前）
    #   之前按 MD5 文件名排序无意义。ISO 格式字符串的字典序与时间序一致。
    #   缺失 saved_at 的旧会话排到最后（空字符串 < ISO 时间）。
    sessions.sort(key=lambda s: s.get("saved_at", "") or "", reverse=True)
    with _session_cache_lock:
        _session_cache = sessions
        _session_cache_mtime = now
    return list(sessions)


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
        data["title"] = new_title.strip() or ""
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
            publish_output(f"  错误: 导出路径必须在当前目录下: {out_path}", level="raw")
            return None
        if out_path.exists():
            publish_output(f"  错误: 文件已存在: {out_path}", level="raw")
            return None
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json_str, encoding='utf-8')
        return str(out_path)
    return json_str
