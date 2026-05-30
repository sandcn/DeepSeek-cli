"""沙盒消息处理 — _handle_sandbox_message

处理沙盒文件的查询和 diff 请求
（get_sandbox_files / get_sandbox_file_diff）。

新增:
  build_sandbox_updated(session)     — 构建轻量级 sandbox_updated 消息，
                                       仅含 count，供初始化/变更时推送。
"""

from __future__ import annotations

import logging
from collections import defaultdict

from ...core.constants import filter_non_system_indices
from ...core.sandbox_manager import get_sandbox_manager as _get_sandbox
from ...ui.diff_renderer import render_diff_to_ansi
from . import _MESSAGE_PREVIEW_LENGTH

_logger = logging.getLogger(__name__)


# ── 文件修改工具列表（用于检测是否需推送沙盒更新） ──
FILE_MODIFY_TOOLS = frozenset([
    'write_file', 'update_file',
    'rm', 'cp', 'mv',
])


def build_sandbox_updated() -> dict:
    """构建轻量级沙盒更新消息（仅含 count，不含文件列表）。

    供初始化 (session_initialized) 和变更推送 (tool_done / messages_updated) 时使用。
    """
    sm = _get_sandbox()
    count = len(sm.get_all_file_changes()) if sm else 0
    return {"type": "sandbox_updated", "count": count}


async def _build_sandbox_files_response(session) -> dict:
    """构建沙盒文件列表响应（含索引映射、分组、摘要）。"""
    sm = _get_sandbox()
    if not sm:
        return {"type": "sandbox_files", "files": [], "count": 0}
    records = sm.get_all_file_changes()

    # ── 建立索引映射：实际消息索引 → 父用户消息的 non_system 索引 ──
    messages = session.messages
    # 反向扫描：每条消息所属的最近用户消息（实际索引）
    actual_to_parent_actual = {}
    last_user_actual = -1
    for i, m in enumerate(messages):
        if m.get("role") == "system":
            continue
        if m.get("role") == "user":
            last_user_actual = i
        actual_to_parent_actual[i] = last_user_actual

    # non_system 消息的实际索引 → non_system 索引
    non_system_real_indices = filter_non_system_indices(messages)

    # 用户消息的实际索引 → non_system 索引 + 内容预览
    user_real_to_ns = {}
    user_previews = {}
    for ns, real in enumerate(non_system_real_indices):
        if messages[real].get("role") == "user":
            user_real_to_ns[real] = ns
            content = messages[real].get("content", "") or ""
            user_previews[real] = content[:_MESSAGE_PREVIEW_LENGTH]

    # ★ 索引体系说明：
    #   records[i].message_index 存储的是 session.messages 中的实际索引
    #   （从 0 计数的完整消息列表位置，含 system 消息）。
    #   non_system 索引则是过滤掉 system 消息后的相对位置。
    #   在 session.messages 的 append 动作中被设置，
    #   与 edit.py 中 _handle_edit_messages_action 使用的 real_idx 一致。
    files = []
    for i, r in enumerate(records):
        actual_idx = r.message_index
        parent_actual = actual_to_parent_actual.get(actual_idx, -1)
        if parent_actual >= 0:
            parent_user_ns = user_real_to_ns.get(parent_actual, -1)
        else:
            parent_user_ns = -1
        files.append({
            "record_id": i,
            "file_path": r.file_path,
            "change_type": r.get_change_type(),
            "tool_name": r.tool_name,
            "message_index": actual_idx,
            "parent_user_index": parent_user_ns,
            "timestamp": r.timestamp,
        })

    # ── 按 parent_user_index 分组并生成摘要 ──
    groups_map = defaultdict(list)
    for f in files:
        groups_map[f["parent_user_index"]].append(f)

    groups = []
    for pid in sorted(groups_map.keys(), reverse=True):
        file_list = groups_map[pid]
        parent_actual = None
        for real, ns in user_real_to_ns.items():
            if ns == pid:
                parent_actual = real
                break
        preview = user_previews.get(parent_actual, "") if parent_actual is not None else ""

        create_count = sum(1 for f in file_list if f["change_type"] == "新建文件")
        modify_count = sum(1 for f in file_list if f["change_type"] == "修改文件")
        delete_count = sum(1 for f in file_list if f["change_type"] == "删除文件")

        parts = []
        if create_count: parts.append(f"新建{create_count}个")
        if modify_count: parts.append(f"修改{modify_count}个")
        if delete_count: parts.append(f"删除{delete_count}个")

        paths = [f["file_path"] for f in file_list[:3]]
        path_text = ", ".join(paths)
        if len(file_list) > 3:
            path_text += f" 等{len(file_list)}个"

        type_summary = " · ".join(parts) if parts else f"{len(file_list)}个文件"
        summary = f"{type_summary} — {path_text}"
        if len(summary) > _MESSAGE_PREVIEW_LENGTH:
            summary = summary[:_MESSAGE_PREVIEW_LENGTH - 3] + "..."

        groups.append({
            "parent_user_index": pid,
            "user_preview": preview,
            "summary": summary,
            "file_count": len(file_list),
        })

    return {
        "type": "sandbox_files",
        "files": files,
        "count": len(files),
        "groups": groups,
    }


def _build_sandbox_diff_response(data: dict) -> dict:
    """构建沙盒文件 diff 响应。"""
    record_id = data.get("record_id")
    sm = _get_sandbox()
    if not sm:
        return {"type": "sandbox_file_diff", "file_path": "", "diff_text": "", "change_type": ""}
    records = sm.get_all_file_changes()
    if isinstance(record_id, (int, float)):
        record_id = int(record_id)
        if 0 <= record_id < len(records):
            r = records[record_id]
            # 使用 render_diff_to_ansi 生成带 ANSI 颜色的 diff，
            # 前端 renderAnsiDiff 直接渲染（与 write_file/update_file 一致）
            before = r.content_before or ""
            after = r.content_after or ""
            if r.content_before is None and r.content_after is not None:
                # 新建文件
                diff_text = render_diff_to_ansi(r.file_path, "", after)
            elif r.content_before is not None and r.content_after is None:
                # 删除文件
                diff_text = render_diff_to_ansi(r.file_path, before, "")
            else:
                diff_text = render_diff_to_ansi(r.file_path, before, after)
            if not diff_text:
                diff_text = "(无变化)"
            return {
                "type": "sandbox_file_diff",
                "file_path": r.file_path,
                "diff_text": diff_text,
                "change_type": r.get_change_type(),
            }
    return {"type": "sandbox_file_diff", "file_path": "", "diff_text": "", "change_type": ""}


async def _handle_sandbox_message(data: dict, ws_send, session=None) -> None:
    """处理沙盒相关的 WebSocket 消息。"""
    if session is None:
        await ws_send({"type": "sandbox_files", "files": [], "count": 0})
        return
    msg_type = data.get("type", "")
    if msg_type == "get_sandbox_files":
        await ws_send(await _build_sandbox_files_response(session))
    elif msg_type == "get_sandbox_file_diff":
        await ws_send(_build_sandbox_diff_response(data))
