"""model.py commit_block 冻结语义 / reset_display user_select 重置测试。

★ 2026-08-20（review P2 修复）：
  1. commit_block 对未走 close_* 冻结路径的关闭块（append_committed 立即
     关闭块）冻结**未提交部分**（``committed_line_count`` 起）——修复前
     全量 ``_block_to_ink_lines(block, 0)``：块已全量提交（committed_line_count
     == len(lines)）→ 冻结缓存与 committed_lines 各存一份全部行（大响应
     内存约翻倍），与 BUG-21（close_reasoning/close_content/close_tool_box
     只冻结未提交尾）修复精神相悖——该修复只覆盖 close_* 路径，漏了
     commit_block；
  2. reset_display 同时重置 ``user_select``（单数兼容字段，保留 seq）——
     修复前仅清空 ``user_selects`` 并发队列：``model.user_select`` 仍指向
     清屏前残留 state（done/action/result 残留，旧代码/测试/命令适配器
     读取该字段会读到清屏前终态立即返回旧结果）。
"""

from src.renderer.ansi.helpers import AnsiLine
from src.tui.app.model import AppModel, UserSelectState


def test_append_committed_freeze_only_uncommitted_tail():
    """append_committed 立即关闭块：冻结缓存 = 未提交尾（已全量提交 → 空）。

    修复前 commit_block 全量冻结（``_block_to_ink_lines(block, 0)``）——
    块已全量提交 → 冻结缓存与 committed_lines 各存一份全部行（内存约
    翻倍）；修复后冻结 ``committed_line_count`` 起（= len(lines) → 空），
    行只存于 committed_lines 一份。
    """
    model = AppModel()
    block = model.append_committed("content", [AnsiLine.of("a"), AnsiLine.of("b")])
    assert block.closed
    assert block.committed_line_count == 2          # 已全量提交
    assert block._cached_ink_lines == []            # 冻结未提交尾 = 空
    assert model.committed_lines                    # 行已进入 committed_lines
    assert model.committed_count == 1               # 块已推进提交游标


def test_commit_block_closed_without_freeze_frozen_empty():
    """commit_block 对「已关闭但无冻结缓存」的块冻结未提交尾（已提交→空）。

    close_content 等正常路径在关闭时已冻结（``_cached_ink_lines`` 非 None，
    commit_block 不覆盖）；本测试覆盖未走 close_* 冻结路径的关闭块（手动
    置 closed）：commit_block 先全量提交（committed_line_count == len(lines)）
    → 冻结 ``committed_line_count`` 起 = 空（修复前全量 ``_block_to_ink_lines
    (block, 0)`` 重复存储全部行）。
    """
    model = AppModel()
    block = model.append_block("content", [AnsiLine.of("first"), AnsiLine.of("tail")])
    # 流式期间 commit_open_block 全量提交（开放块提交语义 = 全部行）
    model.commit_open_block(block)
    assert block.committed_line_count == 2
    # 手动关闭（未走 close_* 冻结路径）
    block.closed = True
    model.commit_block(len(model.blocks) - 1)
    assert model.committed_count == 1
    assert block._cached_ink_lines == []  # 未提交尾 = 空（已全量提交）
    assert model.committed_lines          # 行只存于 committed_lines 一份


def test_reset_display_resets_user_select_keeps_seq():
    """reset_display 重置 user_select 兼容字段（保留 seq）。

    修复前仅清空 user_selects 并发队列——model.user_select 仍指向清屏前
    残留 state（done=True 残留 → 工具协程/旧代码读取立即返回旧结果）；
    修复后重置为干净 state 且 seq 单调保留（App key us-{seq} 不重复，
    再次打开 UserSelectPopup 强制重挂载）。
    """
    model = AppModel()
    state = UserSelectState(
        visible=True, seq=5, title="t", options=["a"],
        done=True, action="confirmed", result=["a"],
    )
    model.user_select = state
    model.user_selects = [state]
    model.reset_display()
    assert model.user_selects == []
    assert model.user_select is not state            # 新实例（残留不跨清屏）
    assert model.user_select.done is False
    assert model.user_select.action == ""
    assert model.user_select.result == []
    assert model.user_select.visible is False
    assert model.user_select.seq == 5                # 保留 seq（key 单调）


def test_reset_display_user_select_default_seq():
    """未打开过弹窗时 reset_display：user_select 重建且 seq 保持 0。"""
    model = AppModel()
    assert model.user_select.seq == 0
    model.reset_display()
    assert model.user_select.seq == 0
    assert model.user_select.done is False
