"""sandbox 命令族 — 文件沙盒视图数据构建 + ``/sandbox`` 命令 + 实时刷新器。

本模块是文件沙盒 TUI 界面的**命令层单一数据源**：文件沙盒（``SandboxManager``）
的变更记录被构建成多种视图所需的纯数据（文件维度 / 消息维度 / 文件历史 /
统计区块），由命令线程写入视图状态；视图组件只读、不直接依赖 core 层。

职责：
  - :func:`change_label` / :func:`build_change_entries`：文件维度条目（变更
    标签 / 首末内容 / 修改次数 / 工具 / 消息索引范围）——供变更审查器视图；
  - :func:`build_message_entries`：消息维度条目（按消息索引分组的记录）——
    供沙盒历史视图（按消息浏览与回滚到消息）；
  - :func:`build_file_history_entries`：单文件历史时间线条目——供文件历史视图；
  - :func:`build_sandbox_sections`：统计区块（概览 / 变更类型 / 工具分布 /
    缓存）——供沙盒概览视图与变更审查器统计面板；
  - :func:`apply_sandbox_action`：执行视图回传的动作（回滚 / 撤销回滚 /
    回滚全部 / 回滚到消息 / 清空）；
  - :func:`make_sandbox_refresher`：实时刷新器（数据签名变化时重建并写回状态）；
  - ``/sandbox`` 命令（打开概览视图或文本回退）；``/changes`` 的视图打开
    逻辑（:func:`open_changes_ui`）也在此收敛，供 ``_session_cmd`` 委托。

设计约束：本模块只依赖 ``core``（sandbox_manager / adapters），不依赖
``tui`` 具体组件（视图状态类型经 ``adapters.ui_runtime`` 桥接）。
"""

from __future__ import annotations

import logging

from ..adapters.output import get_default_output_port
from ..constants import CYAN, DIM, RESET, YELLOW
from ..internal.commands._command_core import CommandContext
from ..sandbox_manager import get_sandbox_manager

_logger = logging.getLogger(__name__)
_out = get_default_output_port()

__all__ = [
    "change_label",
    "build_change_entries",
    "build_message_entries",
    "build_file_history_entries",
    "build_record_history_entries",
    "build_record_row",
    "build_sandbox_sections",
    "sandbox_signature",
    "build_view_data",
    "make_sandbox_refresher",
    "apply_sandbox_action",
    "consume_sandbox_actions",
    "open_changes_ui",
    "open_sandbox_ui",
    "open_sandbox_history_ui",
    "open_sandbox_records_ui",
    "SandboxCommand",
    "sandbox_text",
]

#: 变更标签（文件 / 目录共用；目录标签在 ``change_label`` 内按 is_dir 区分）。
_LABEL_NEW = "新建"
_LABEL_DEL = "删除"
_LABEL_MOD = "修改"
_LABEL_SAME = "无变化"


def change_label(before, after, is_dir: bool = False) -> str:
    """变更标签：新建 / 删除 / 修改 / 无变化（目录加后缀「目录」）。"""
    suffix = "目录" if is_dir else ""
    if before is None and after is not None:
        return f"{_LABEL_NEW}{suffix}"
    if before is not None and after is None:
        return f"{_LABEL_DEL}{suffix}"
    if before == after:
        return _LABEL_SAME
    return f"{_LABEL_MOD}{suffix}" if is_dir else _LABEL_MOD


def _rec_tool(record) -> str:
    return str(getattr(record, "tool_name", "") or "")


def _rec_index(record) -> int:
    try:
        return int(getattr(record, "message_index", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _rec_time(record) -> float:
    try:
        return float(getattr(record, "timestamp", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _rec_is_dir(record) -> bool:
    return str(getattr(record, "record_type", "file") or "file") == "directory"


def _is_revert_tool(name: str) -> bool:
    return name.startswith("revert") or name.startswith("undo-revert")


def _sorted_records(records: list) -> list:
    """记录按「消息索引 + 时间戳」排序（视图展示顺序的单一真源）。"""
    try:
        return sorted(records, key=lambda r: (_rec_index(r), _rec_time(r)))
    except Exception:
        return list(records)


def build_record_row(record) -> dict:
    """单条文件变更记录 → 视图条目 dict。"""
    is_dir = _rec_is_dir(record)
    before = getattr(record, "content_before", None)
    after = getattr(record, "content_after", None)
    return {
        "path": str(getattr(record, "file_path", "") or ""),
        "before": before,
        "after": after,
        "message_index": _rec_index(record),
        "tool": _rec_tool(record),
        "time": _rec_time(record),
        "is_dir": is_dir,
        "change_label": change_label(before, after, is_dir),
        "is_revert": _is_revert_tool(_rec_tool(record)),
    }


def build_change_entries(sandbox) -> list:
    """文件沙盒变更 → 文件维度条目列表（按文件分组聚合首末内容）。

    每条条目同时携带视图所需的元信息：变更标签、首末内容、修改次数、
    消息索引范围、涉及工具、是否目录、最近修改时间、是否含回滚动作。
    对缺少属性的记录对象（测试桩）保持健壮（一律 ``getattr`` 默认值）。
    """
    try:
        records = sandbox.get_all_file_changes() or []
    except Exception:
        records = []
    groups: dict = {}
    for r in records:
        try:
            path = str(getattr(r, "file_path", "") or "")
        except Exception:
            continue
        if not path:
            continue
        groups.setdefault(path, []).append(r)
    entries: list = []
    for path, recs in groups.items():
        recs = _sorted_records(recs)
        if not recs:
            continue
        first, last = recs[0], recs[-1]
        before = getattr(first, "content_before", None)
        after = getattr(last, "content_after", None)
        is_dir = any(_rec_is_dir(r) for r in recs)
        tools: list = []
        for r in recs:
            name = _rec_tool(r)
            if name and name not in tools:
                tools.append(name)
        first_idx, last_idx = _rec_index(first), _rec_index(last)
        history: list = []
        for i, r in enumerate(recs):
            row = build_record_row(r)
            row["seq"] = i + 1
            history.append(row)
        entries.append({
            "path": path,
            "change_label": change_label(before, after, is_dir),
            "before": before,
            "after": after,
            "records": len(recs),
            "message_index": f"{first_idx}-{last_idx}",
            "first_index": first_idx,
            "last_index": last_idx,
            "tools": tools,
            "is_dir": is_dir,
            "mtime": _rec_time(last),
            "reverted": any(_is_revert_tool(_rec_tool(r)) for r in recs),
            "history": history,
            "_records": recs,
        })
    return entries


def build_message_entries(sandbox) -> list:
    """文件沙盒变更 → 消息维度条目列表（按消息索引分组）。

    每项：``{index, count, files, tools, time, changes}``——``changes`` 为该
    消息索引下的文件变更条目（复用 :func:`build_change_entries` 的字段形状，
    但按**单条记录**展开，便于逐文件查看与回滚）。
    """
    try:
        groups = sandbox.get_message_groups() or []
    except Exception:
        groups = []
    entries: list = []
    for index, records in groups:
        recs = _sorted_records(records)
        if not recs:
            continue
        tools: list = []
        files: list = []
        for r in recs:
            name = _rec_tool(r)
            if name and name not in tools:
                tools.append(name)
            path = str(getattr(r, "file_path", "") or "")
            if path and path not in files:
                files.append(path)
        entries.append({
            "index": int(index),
            "count": len(recs),
            "files": len(files),
            "file_paths": files,
            "tools": tools,
            "time": _rec_time(recs[-1]),
            "changes": [build_record_row(r) for r in recs],
        })
    return entries


def build_file_history_entries(sandbox, path: str) -> list:
    """单文件历史时间线条目列表（按消息索引 + 时间戳升序）。

    每项：``{seq, index, tool, time, change_label, before, after, is_dir,
    is_revert}``——``seq`` 为 1-based 序号（视图展示用）。
    """
    try:
        records = sandbox.get_file_history(path) or []
    except Exception:
        records = []
    rows: list = []
    for i, r in enumerate(records):
        row = build_record_row(r)
        row["seq"] = i + 1
        rows.append(row)
    return rows


def build_record_history_entries(sandbox, file_path=None) -> list:
    """全部变更记录 → 记录流水条目列表（按消息索引 + 时间戳升序）。

    ``file_path`` 非空时只保留该路径的记录。每项为 :func:`build_record_row`
    的结果 + ``seq``（1-based 序号）。
    """
    try:
        records = sandbox.get_all_file_changes() or []
    except Exception:
        records = []
    rows: list = []
    for i, r in enumerate(_sorted_records(records)):
        row = build_record_row(r)
        if file_path and row["path"] != file_path:
            continue
        row["seq"] = i + 1
        rows.append(row)
    return rows


def _fmt_int(value) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def build_sandbox_sections(sandbox) -> list:
    """文件沙盒统计区块（对齐 usage 视图的 sections 结构）。

    结构：``[{"title": str, "rows": [(label, value, kind, ratio), ...]}, ...]``
    """
    if sandbox is None:
        return [{"title": "沙盒", "rows": [("状态", "(未初始化)", "warn", None)]}]
    try:
        stats = sandbox.get_extended_stats()
    except Exception:
        stats = {}
    total_files = int(stats.get("total_files", 0) or 0)
    total_records = int(stats.get("total_records", 0) or 0)
    sections: list = [
        {
            "title": "概览",
            "rows": [
                ("文件数", _fmt_int(total_files), "info", None),
                ("记录数", _fmt_int(total_records), "info", None),
                ("消息分组", _fmt_int(stats.get("message_groups", 0)), "info", None),
                ("当前索引", _fmt_int(stats.get("current_message_index", 0)), "info", None),
            ],
        },
    ]
    type_counts = stats.get("type_counts") or {}
    if type_counts:
        sections.append({
            "title": "变更类型",
            "rows": [
                (str(label), _fmt_int(count), "info", None)
                for label, count in sorted(type_counts.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
        })
    tool_counts = stats.get("tool_counts") or {}
    if tool_counts:
        sections.append({
            "title": "工具分布",
            "rows": [
                (str(name), _fmt_int(count), "info", None)
                for name, count in sorted(tool_counts.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
        })
    sections.append({
        "title": "缓存与回滚",
        "rows": [
            ("内容字符数", _fmt_int(stats.get("content_chars", 0)), "info", None),
            ("每文件上限", _fmt_int(stats.get("max_history_per_file", 0)), "info", None),
            ("回滚次数", _fmt_int(stats.get("revert_count", 0)), "warn", None),
        ],
    })
    return sections


def sandbox_signature(sandbox) -> tuple:
    """沙盒数据签名（记录数 / 文件数 / 当前消息索引）——实时刷新检测用。"""
    if sandbox is None:
        return (0, 0, 0)
    try:
        stats = sandbox.get_extended_stats()
    except Exception:
        return (0, 0, 0)
    return (
        int(stats.get("total_records", 0) or 0),
        int(stats.get("total_files", 0) or 0),
        int(stats.get("current_message_index", 0) or 0),
    )


def build_view_data(sandbox) -> dict:
    """视图完整数据快照（变更审查器 / 概览视图共用的刷新载荷）。"""
    return {
        "files": build_change_entries(sandbox),
        "messages": build_message_entries(sandbox),
        "records": build_record_history_entries(sandbox),
        "sections": build_sandbox_sections(sandbox),
        "stats": sandbox.get_extended_stats() if sandbox is not None else {},
    }


def make_sandbox_refresher(session, apply_state, action_handler=None):
    """构建文件沙盒视图**实时刷新器**（数据签名变化时重建并写回状态）。

    Args:
        session: ``ChatSession``（未使用，保留签名一致性；沙盒经全局管理器读取）。
        apply_state: ``(data: dict) -> None``——把快照写入视图状态。
        action_handler: ``(sandbox) -> bool``——**每次调用先执行**（将待处理
            视图动作落地，见 :func:`consume_sandbox_actions`）；None 表示不处理
            动作（命令线程轮询路径自行处理）。

    Returns:
        ``refresh(force: bool = False) -> bool``——是否发生重建：先消费动作
        （可能改变签名），签名未变（且非 force）直接返回 False。
    """
    probe = {"sig": None}

    def _refresh(force: bool = False) -> bool:
        try:
            sandbox = get_sandbox_manager()
            if sandbox is None:
                return False
            if action_handler is not None:
                try:
                    action_handler(sandbox)
                except Exception:
                    _logger.debug("沙盒视图动作消费失败", exc_info=True)
            sig = sandbox_signature(sandbox)
            if not force and sig == probe["sig"]:
                return False
            probe["sig"] = sig
            apply_state(build_view_data(sandbox))
            return True
        except Exception:
            _logger.debug("文件沙盒刷新器失败", exc_info=True)
            return False

    return _refresh


# ── 动作执行（视图回传 → 沙盒操作） ─────────────────────────


def apply_sandbox_action(sandbox, action: dict) -> str:
    """执行视图回传的沙盒动作，返回反馈文本。

    动作（``action["action"]``）：
      - ``revert``：回滚单文件到首次变更前（``path``）；
      - ``undo-revert``：撤销最近一次回滚（``path`` 可选）；
      - ``revert-all``：回滚全部文件；
      - ``restore-message``：回滚到指定消息索引（``index``）；
      - ``clear``：清空沙盒记录。
    """
    if sandbox is None:
        return "文件沙盒未初始化"
    if not isinstance(action, dict):
        return "无效操作"
    kind = str(action.get("action", ""))
    if kind == "revert":
        path = str(action.get("path", "") or "")
        ok, _before, _after = sandbox.revert_file(path)
        return f"已回滚：{path}" if ok else f"回滚失败：{path}"
    if kind == "undo-revert":
        path = action.get("path")
        ok, target = sandbox.undo_last_revert(path or None)
        if ok:
            return f"已撤销回滚：{target}"
        return "没有可撤销的回滚"
    if kind == "revert-all":
        entries = build_change_entries(sandbox)
        done = failed = 0
        for entry in entries:
            ok, _b, _a = sandbox.revert_file(str(entry.get("path", "")))
            if ok:
                done += 1
            else:
                failed += 1
        suffix = f"，失败 {failed}" if failed else ""
        return f"已回滚 {done} 个文件{suffix}"
    if kind == "restore-message":
        try:
            index = int(action.get("index", 0))
        except (TypeError, ValueError):
            return "无效消息索引"
        results = sandbox.restore_to_message(index)
        ok = sum(1 for v in (results or {}).values() if v)
        bad = len(results or {}) - ok
        suffix = f"，失败 {bad}" if bad else ""
        return f"已回滚到消息 {index}（{ok} 个文件{suffix}）"
    if kind == "clear":
        sandbox.clear()
        return "文件沙盒已清空"
    return "未知操作"


#: 沙盒视图动作消费目标（视图状态属性名 → 需复位的确认标志）。
_ACTION_TARGETS = (
    ("changes_view", (("revert_confirm", ""), ("revert_all_confirm", False),
                      ("restore_mode", False))),
    ("sandbox_view", (("clear_confirm", False),)),
    ("sandbox_history_view", (("restore_confirm", -1),)),
    ("sandbox_records_view", (("revert_confirm", ""),)),
)


def consume_sandbox_actions(model, sandbox) -> bool:
    """消费各沙盒视图的待处理动作（幂等；返回是否执行了动作）。

    「先到先得」：视图组件写 ``applied`` + ``applied_seq`` 递增；本函数（经
    实时刷新器或命令线程轮询调用）按 ``applied_consumed`` 判定是否已消费，
    未消费则执行 :func:`apply_sandbox_action` 并复位确认标志。**命令路径与
    F11 直开路径共用本函数**（``applied_consumed`` 保证只执行一次，避免两
    条路径重复回滚）。
    """
    if model is None or sandbox is None:
        return False
    executed = False
    for attr, resets in _ACTION_TARGETS:
        state = getattr(model, attr, None)
        if state is None:
            continue
        try:
            seq = int(getattr(state, "applied_seq", 0) or 0)
            consumed = int(getattr(state, "applied_consumed", 0) or 0)
        except (TypeError, ValueError):
            continue
        if seq <= consumed:
            continue
        try:
            state.applied_consumed = seq
        except Exception:
            pass
        report = getattr(state, "applied", None) or {}
        try:
            state.status_message = apply_sandbox_action(sandbox, report)
            state.applied = None
        except Exception:
            _logger.debug("消费沙盒动作失败", exc_info=True)
        for name, value in resets:
            try:
                setattr(state, name, value)
            except Exception:
                pass
        executed = True
    return executed


# ── 视图打开（有 ChatUI）/ 文本回退 ────────────────────────


def open_changes_ui(ctx) -> bool:
    """打开全屏文件变更审查器视图（有 ChatUI 时）。返回是否已打开处理。"""
    from ..adapters.ui_runtime import get_changes_view_state_cls
    from ._view_opener import open_fullscreen_view

    sandbox = get_sandbox_manager()
    if not sandbox:
        return False
    holder: dict = {}

    def setup(model, state):
        holder["model"] = model
        state.entries = build_change_entries(sandbox)
        state.messages = build_message_entries(sandbox)
        state.sections = build_sandbox_sections(sandbox)

    def tick(state) -> bool:
        if consume_sandbox_actions(holder.get("model"), sandbox):
            state.entries = build_change_entries(sandbox)
            state.messages = build_message_entries(sandbox)
            state.sections = build_sandbox_sections(sandbox)
        return False

    return open_fullscreen_view(
        ctx, view_id="changes", state_attr="changes_view",
        state_cls=get_changes_view_state_cls(), setup=setup, on_tick=tick,
        close_hint="变更审查器已关闭", timeout_hint="变更审查器超时关闭",
    )


def open_sandbox_ui(ctx) -> bool:
    """打开全屏文件沙盒概览视图（有 ChatUI 时）。返回是否已打开处理。"""
    from ..adapters.ui_runtime import get_sandbox_stats_view_state_cls
    from ._view_opener import open_fullscreen_view

    sandbox = get_sandbox_manager()
    if sandbox is None:
        return False
    holder: dict = {}
    refresh_seq = {"v": 0}

    def setup(model, state):
        holder["model"] = model
        state.sections = build_sandbox_sections(sandbox)

    def tick(state) -> bool:
        if consume_sandbox_actions(holder.get("model"), sandbox):
            state.sections = build_sandbox_sections(sandbox)
        if state.refresh_seq > refresh_seq["v"]:
            refresh_seq["v"] = state.refresh_seq
            state.sections = build_sandbox_sections(sandbox)
            state.status_message = state.status_message or "已刷新"
        return False

    return open_fullscreen_view(
        ctx, view_id="sandbox", state_attr="sandbox_view",
        state_cls=get_sandbox_stats_view_state_cls(), setup=setup, on_tick=tick,
        close_hint="文件沙盒概览已关闭", timeout_hint="文件沙盒概览超时关闭",
    )


def open_sandbox_history_ui(ctx) -> bool:
    """打开全屏沙盒消息维度历史视图（有 ChatUI 时）。返回是否已打开处理。"""
    from ..adapters.ui_runtime import get_sandbox_history_view_state_cls
    from ._view_opener import open_fullscreen_view

    sandbox = get_sandbox_manager()
    if sandbox is None:
        return False
    holder: dict = {}

    def setup(model, state):
        holder["model"] = model
        state.entries = build_message_entries(sandbox)

    def tick(state) -> bool:
        if consume_sandbox_actions(holder.get("model"), sandbox):
            state.entries = build_message_entries(sandbox)
        return False

    return open_fullscreen_view(
        ctx, view_id="sandbox_history", state_attr="sandbox_history_view",
        state_cls=get_sandbox_history_view_state_cls(), setup=setup, on_tick=tick,
        close_hint="沙盒历史已关闭", timeout_hint="沙盒历史超时关闭",
    )


def open_sandbox_records_ui(ctx) -> bool:
    """打开全屏变更记录流水视图（有 ChatUI 时）。返回是否已打开处理。"""
    from ..adapters.ui_runtime import get_sandbox_records_view_state_cls
    from ._view_opener import open_fullscreen_view

    sandbox = get_sandbox_manager()
    if sandbox is None:
        return False
    holder: dict = {}

    def setup(model, state):
        holder["model"] = model
        state.entries = build_record_history_entries(sandbox)

    def tick(state) -> bool:
        if consume_sandbox_actions(holder.get("model"), sandbox):
            state.entries = build_record_history_entries(sandbox)
        return False

    return open_fullscreen_view(
        ctx, view_id="sandbox_records", state_attr="sandbox_records_view",
        state_cls=get_sandbox_records_view_state_cls(), setup=setup, on_tick=tick,
        close_hint="变更记录流水已关闭", timeout_hint="变更记录流水超时关闭",
    )


def sandbox_text(ctx) -> bool:
    """文本列出文件沙盒统计（无 ChatUI / 单次模式回退）。"""
    sandbox = get_sandbox_manager()
    if sandbox is None:
        _out.write(f"{YELLOW}  ! 文件沙盒未初始化{RESET}", level="raw", source="cmd")
        return True
    sections = build_sandbox_sections(sandbox)
    _out.write(f"\n{DIM}  \u2500 文件沙盒{RESET}", level="raw", source="cmd")
    for sec in sections:
        _out.write(f"  {CYAN}\u25b8 {sec.get('title', '')}{RESET}", level="raw", source="cmd")
        for row in sec.get("rows") or []:
            label, value = row[0], row[1]
            _out.write(f"    {label}: {value}", level="raw", source="cmd")
    return True


def _cmd_sandbox(ctx) -> bool:
    """打开文件沙盒视图（有 ChatUI）或文本列出（回退）。

    参数（``ctx.arg``）：
      - 空 → 概览视图（``/sandbox``）；
      - ``history`` / ``h`` → 消息维度历史视图；
      - ``records`` / ``r`` → 变更记录流水视图。
    """
    arg = str(getattr(ctx, "arg", "") or "").strip().lower()
    if arg in ("history", "h"):
        if open_sandbox_history_ui(ctx):
            return True
    elif arg in ("records", "record", "r"):
        if open_sandbox_records_ui(ctx):
            return True
    elif open_sandbox_ui(ctx):
        return True
    return sandbox_text(ctx)


# ── CommandPlugin 子类 ──────────────────────────────

from .base import CommandMeta, CommandPlugin, declare_command_plugin


class SandboxCommand(CommandPlugin):
    """文件沙盒概览（/sandbox）"""

    def __init__(self):
        self.meta = CommandMeta(
            name="sandbox", description="文件沙盒概览（统计 / 清理 / 历史）",
            group="files", usage="[history|records]",
        )

    def execute(self, ctx: CommandContext) -> bool:
        return _cmd_sandbox(ctx)


declare_command_plugin(SandboxCommand())
