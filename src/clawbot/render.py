"""结果渲染 — 从会话消息提取回复、清理 ANSI、按微信限制分段。

职责：
- extract_reply / extract_reasoning / extract_tool_summary：从消息列表提取回显内容
- strip_ansi：清理终端颜色控制序列
- split_message：把长文本分段（微信单条文本消息有长度限制）
"""

from __future__ import annotations

import re

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# 微信单条文本消息长度上限（保守按字符数，中文 3 字节编码下约 2000 字安全）
DEFAULT_MESSAGE_LIMIT = 2000


def strip_ansi(text: str) -> str:
    """去除 ANSI 转义序列。"""
    return _ANSI_RE.sub("", text or "")


def _content_to_text(content) -> str:
    """把 assistant content（str 或多模态 list）统一转为纯文本。"""
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text") or block.get("content") or "")
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    if content is None:
        return ""
    return str(content)


def extract_reply(messages: list[dict]) -> str:
    """从消息列表提取最后一条非空 assistant 回复文本。

    从后往前找，取最近一条 role=assistant 且 content 非空的消息。
    """
    for msg in reversed(messages or []):
        if msg.get("role") != "assistant":
            continue
        text = strip_ansi(_content_to_text(msg.get("content"))).strip()
        if text:
            return text
    return ""


def extract_reasoning(messages: list[dict]) -> str:
    """提取最后一条 assistant 的思考过程（reasoning_content）。"""
    for msg in reversed(messages or []):
        if msg.get("role") != "assistant":
            continue
        rc = msg.get("reasoning_content")
        if rc:
            text = strip_ansi(str(rc)).strip()
            if text:
                return text
    return ""


def extract_tool_summary(messages: list[dict]) -> str:
    """提取消息列表中的工具调用摘要（工具名 + 参数 + 结果片段）。

    Returns:
        多行摘要文本，无工具调用时返回空字符串
    """
    lines = []
    for msg in messages or []:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                name = fn.get("name", "?")
                args = str(fn.get("arguments") or "")[:120]
                lines.append(f"🔧 调用工具 {name} {args}".rstrip())
        elif msg.get("role") == "tool":
            content = strip_ansi(str(msg.get("content") or "")).strip()
            if content:
                lines.append(f"  ↳ {content[:300]}")
    return "\n".join(lines)


def _find_cut(text: str, limit: int) -> int:
    """在 limit 附近寻找合适断点。

    优先保证代码块闭合（``` 成对），再优先在换行/标点/空格处断开，
    避免单词或语句被硬切。
    """
    window = text[:limit]
    # 代码块未闭合 → 把断点移到最近的闭合符之后
    if window.count("```") % 2 == 1:
        idx = window.rfind("```")
        return idx + 3
    for sep in ("\n", "。", "！", "？", "！", "；", " ", "，", ",", ";"):
        idx = window.rfind(sep)
        if idx > limit * 0.4:
            return idx + len(sep)
    return limit


def split_message(text: str, limit: int = DEFAULT_MESSAGE_LIMIT) -> list[str]:
    """把长文本按字符上限分段，返回分段列表。

    - 空文本返回空列表
    - 不超限时返回单段
    - 超限时在 _find_cut 选定的断点切分，并去掉分段首尾空白
    """
    text = strip_ansi(text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    chunks = []
    rest = text
    while len(rest) > limit:
        cut = _find_cut(rest, limit)
        if cut <= 0:
            cut = limit
        chunks.append(rest[:cut].strip())
        rest = rest[cut:].lstrip()
    if rest.strip():
        chunks.append(rest.strip())
    return chunks


# ── 终端二维码渲染 ─────────────────────────────────────

def render_qrcode_ascii(content: str, invert: bool = True,
                        max_width: int | None = None) -> list[str]:
    """用 qrcode 库生成终端 ASCII 二维码，返回行列表。

    每行以两个字符表示一个二维码模块（保持接近 1:1 的宽高观感）。
    invert=True 时反色（黑底白块），适配深色终端。

    Args:
        content: 二维码内容（微信返回的 qrcode 标识）
        invert: 是否反色
        max_width: 输出行最大字符宽度（None 不限制）。终端过窄时按模块
            降采样，避免 TUI/终端自动换行导致二维码错位变形。

    Returns:
        二维码文本行列表（含静区 border）
    """
    import math

    import qrcode
    from qrcode.constants import ERROR_CORRECT_M

    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=1,
        border=2,
    )
    qr.add_data(content)
    qr.make(fit=True)

    # 每模块输出 2 字符（██ 或 空格），行宽 = 模块数 × 2。
    # 超宽时把模块矩阵交给 matrix_to_ascii 降采样（block 合并多数投票）。
    if max_width is not None and max_width > 0:
        matrix = qr.get_matrix()
        n = len(matrix)
        if n * 2 > max_width:
            from .qrimage import matrix_to_ascii
            max_modules = max(1, max_width // 2)
            return matrix_to_ascii(matrix, max_width=max_modules, invert=invert)

    white, black = "  ", "██"
    if invert:
        white, black = black, white
    lines = []
    for row in qr.get_matrix():
        lines.append("".join(black if cell else white for cell in row))
    return lines
