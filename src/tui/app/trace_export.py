"""轨迹导出与复制（trace_export）——``w``/``W`` 导出、``y`` 复制内容。

2026-10-07（用户需求：轨迹 Trace 更多功能）新增：
  - ``record_to_text``：单条记录 → 纯文本（``y`` 复制到剪贴板的内容）；
  - ``records_to_markdown`` / ``records_to_json``：整条轨迹序列化；
  - ``export_filename`` / ``write_export``：写入文件（时间戳命名）。

设计：**序列化纯函数与写盘分离**——纯函数便于单测（不触碰文件系统），
写盘函数只负责取路径 + 写文件 + 返回展示用路径。时间经参数注入（默认
``_time.time``），便于测试固定输出。
"""

from __future__ import annotations

import json as _json
import os
import time as _time

from .trace_stats import collect_trace_stats

__all__ = [
    "record_to_text", "records_to_markdown", "records_to_json",
    "export_filename", "write_export", "line_text",
]

#: 导出文件命名前缀（工作目录下生成 ``trace-export-<时间戳>.<ext>``）。
EXPORT_PREFIX = "trace-export"
#: 格式 → 扩展名（``w`` = markdown / ``W`` = json）。
_FORMAT_EXT = {"md": "md", "json": "json"}


def line_text(line) -> str:
    """详情行 → 纯文本（str 原样；AnsiLine/StyledRun 取 ``plain``/``text``；
    run 列表逐段拼接）。"""
    if isinstance(line, str):
        return line
    if isinstance(line, (list, tuple)):
        return "".join(line_text(x) for x in line)
    plain = getattr(line, "plain", None)
    if plain is not None:
        return str(plain)
    text = getattr(line, "text", None)
    if text is not None:
        return str(text)
    return str(line)


def _lines_text(lines) -> list:
    """详情行列表 → 纯文本行列表（跳过 None）。"""
    out: list = []
    for line in lines or []:
        if line is None:
            continue
        out.append(line_text(line))
    return out


def record_to_text(rec) -> str:
    """单条记录 → 纯文本（``y`` 复制内容）。

    结构：标题行（``#N 种类 状态``）→ 摘要 → 元信息 → 参数 → 返回值 →
    详情行。空字段不输出。
    """
    from src.tui._format import format_duration, format_tokens

    index = getattr(rec, "index", 0)
    kind = getattr(rec, "kind", "") or "context"
    status = getattr(rec, "status", "") or ""
    head = f"#{index} {kind}"
    if status:
        head += f" [{status}]"
    out: list = [head]
    summary = (getattr(rec, "summary", "") or "").strip()
    if summary:
        out.append(summary)
    meta: list = []
    t = getattr(rec, "time_seconds", None)
    if t is not None:
        try:
            meta.append(f"耗时 {format_duration(float(t))}")
        except (TypeError, ValueError):
            pass
    tokens = getattr(rec, "tokens", None) or {}
    if isinstance(tokens, dict):
        tin = _int_or(tokens.get("input"), 0)
        tout = _int_or(tokens.get("output"), 0)
        if tin or tout:
            meta.append(f"tokens \u2191{format_tokens(tin)} \u2193{format_tokens(tout)}")
    name = (getattr(rec, "tool_name", "") or "").strip()
    if name:
        meta.append(f"工具 {name}")
    cid = (getattr(rec, "tool_call_id", "") or "").strip()
    if cid:
        meta.append(f"调用 ID {cid}")
    if meta:
        out.append(" \u00b7 ".join(meta))
    args = getattr(rec, "tool_args", None)
    if args is not None and str(args) != "":
        out.append("参数:")
        out.append(str(args))
    result = getattr(rec, "tool_result", "") or ""
    if result:
        out.append("返回:")
        out.append(result)
    lines = _lines_text(getattr(rec, "lines", None))
    for line in lines:
        out.append(line)
    return "\n".join(out)


def _int_or(value, default: int) -> int:
    """int 归一化（异常值回退默认）。"""
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _records_meta(records, source: str = "") -> dict:
    """导出元信息（统计汇总 + 来源标签）。"""
    stats = collect_trace_stats(records)
    return {
        "source": source or "主轨迹",
        "exported_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
        "records": stats["total"],
        "turns": stats["turns"],
        "tools": stats["tools"],
        "errors": stats["errors"],
        "elapsed": stats["elapsed"],
        "tokens_in": stats["tokens_in"],
        "tokens_out": stats["tokens_out"],
    }


def records_to_markdown(records, source: str = "") -> str:
    """轨迹 → Markdown 文档（``w`` 导出内容）。

    结构：标题 + 元信息表 + 每条记录小节（``## #N 种类`` + 摘要 + 状态/
    耗时/token + 参数/返回代码块 + 详情）。``lines`` 内容原样输出为段落。
    """
    meta = _records_meta(records, source)
    out: list = ["# 轨迹导出（Trace Export）", ""]
    out.append(f"- 来源：{meta['source']}")
    out.append(f"- 导出时间：{meta['exported_at']}")
    out.append(
        f"- 记录：{meta['records']} 条 · 轮次 {meta['turns']} · 工具 "
        f"{meta['tools']} · 失败 {meta['errors']}"
    )
    out.append(
        f"- 总耗时：{meta['elapsed']:.1f}s · tokens "
        f"↑{meta['tokens_in']} ↓{meta['tokens_out']}"
    )
    out.append("")
    for rec in records or []:
        if rec is None:
            continue
        index = getattr(rec, "index", 0)
        kind = getattr(rec, "kind", "") or "context"
        status = getattr(rec, "status", "") or ""
        title = f"## #{index} {kind}"
        if status:
            title += f" [{status}]"
        out.append(title)
        out.append("")
        summary = (getattr(rec, "summary", "") or "").strip()
        if summary:
            out.append(f"**摘要**：{summary}")
            out.append("")
        meta_parts: list = []
        t = getattr(rec, "time_seconds", None)
        if t is not None:
            try:
                meta_parts.append(f"耗时 {float(t):.1f}s")
            except (TypeError, ValueError):
                pass
        tokens = getattr(rec, "tokens", None) or {}
        if isinstance(tokens, dict) and (tokens.get("input") or tokens.get("output")):
            meta_parts.append(
                f"tokens ↑{_int_or(tokens.get('input'), 0)} "
                f"↓{_int_or(tokens.get('output'), 0)}"
            )
        name = (getattr(rec, "tool_name", "") or "").strip()
        if name:
            meta_parts.append(f"工具 {name}")
        cid = (getattr(rec, "tool_call_id", "") or "").strip()
        if cid:
            meta_parts.append(f"调用 ID {cid}")
        if meta_parts:
            out.append(" · ".join(meta_parts))
            out.append("")
        args = getattr(rec, "tool_args", None)
        if args is not None and str(args) != "":
            out.append("**参数**")
            out.append("")
            out.append("```json")
            out.append(str(args))
            out.append("```")
            out.append("")
        result = getattr(rec, "tool_result", "") or ""
        if result:
            out.append("**返回值**")
            out.append("")
            out.append("```text")
            out.append(result)
            out.append("```")
            out.append("")
        for line in _lines_text(getattr(rec, "lines", None)):
            out.append(line)
        out.append("")
    return "\n".join(out)


def _record_to_dict(rec) -> dict:
    """记录 → JSON 可序列化 dict。"""
    return {
        "index": getattr(rec, "index", 0),
        "kind": getattr(rec, "kind", "") or "context",
        "summary": getattr(rec, "summary", "") or "",
        "status": getattr(rec, "status", "") or "",
        "time_seconds": getattr(rec, "time_seconds", None),
        "tokens": dict(getattr(rec, "tokens", None) or {}),
        "tool_name": getattr(rec, "tool_name", "") or "",
        "tool_call_id": getattr(rec, "tool_call_id", "") or "",
        "tool_args": _jsonable(getattr(rec, "tool_args", None)),
        "tool_result": getattr(rec, "tool_result", "") or "",
        "subagent_label": getattr(rec, "subagent_label", "") or "",
        "lines": _lines_text(getattr(rec, "lines", None)),
        "images": [
            {k: v for k, v in img.items() if isinstance(v, (str, int, float, bool)) or v is None}
            for img in (getattr(rec, "images", None) or [])
            if isinstance(img, dict)
        ],
    }


def _jsonable(value):
    """任意值 → JSON 可序列化（dict/list 递归；其余原样或 str 化）。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def records_to_json(records, source: str = "") -> str:
    """轨迹 → JSON 文档（``W`` 导出内容；缩进 2、非 ASCII 原样）。"""
    payload = {
        "meta": _records_meta(records, source),
        "records": [
            _record_to_dict(rec) for rec in (records or []) if rec is not None
        ],
    }
    return _json.dumps(payload, ensure_ascii=False, indent=2)


def export_filename(fmt: str = "md", when: float | None = None) -> str:
    """导出文件名（``trace-export-<YYYYmmdd-HHMMSS>.<ext>``）。"""
    ts = _time.localtime(_time.time() if when is None else when)
    stamp = _time.strftime("%Y%m%d-%H%M%S", ts)
    ext = _FORMAT_EXT.get(fmt, "md")
    return f"{EXPORT_PREFIX}-{stamp}.{ext}"


def write_export(records, fmt: str = "md", source: str = "",
                 directory: str | None = None,
                 when: float | None = None) -> str:
    """把轨迹写入文件（``w``/``W`` 动作）。

    Args:
        records: 记录列表（``build_trace_records`` 结果）。
        fmt: ``"md"`` / ``"json"``。
        source: 轨迹来源标签（主轨迹 / subagent label）。
        directory: 输出目录（默认当前工作目录）。
        when: 时间基准（测试注入；默认当前时间）。

    Returns:
        写入的文件路径（相对工作目录的形式，便于状态行展示）。

    Raises:
        OSError: 写文件失败（调用方捕获并提示）。
        ValueError: 未知格式。
    """
    if fmt not in _FORMAT_EXT:
        raise ValueError(f"未知导出格式: {fmt!r}")
    directory = directory or os.getcwd()
    filename = export_filename(fmt, when)
    path = os.path.join(directory, filename)
    text = (
        records_to_json(records, source) if fmt == "json"
        else records_to_markdown(records, source)
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
        if not text.endswith("\n"):
            fh.write("\n")
    try:
        rel = os.path.relpath(path, os.getcwd())
    except ValueError:
        rel = path
    return rel
