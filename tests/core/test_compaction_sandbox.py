"""上下文压缩（自动折叠）后文件沙盒可正常还原的回归测试。

覆盖修复：压缩落地此前发 ``remove`` 事件 → 沙盒按「删除即失效」丢弃被压缩
区间的文件变更记录，导致压缩后 ``/changes`` 看不到、回滚无法还原这些文件。
现压缩统一发 ``fold`` 事件（保留记录、折叠区间重挂到摘要位置）。

覆盖：``SandboxManager.fold_indices``、``_FileHistory.fold_indices``、
``ContextManager.apply_replacement`` 事件语义、会话消息数上限裁剪
（``enforce_message_limit``）与两种压缩策略（摘要 / 降级删除）的事件语义，
以及「压缩 → 真实文件回滚」的端到端还原。
"""

from __future__ import annotations

import pytest

from src.core import sandbox_manager as sm_mod
from src.core.context_manager import ContextManager
from src.core.adapters.config import MockConfigAdapter
from src.core.sandbox_manager import (
    SandboxManager,
    get_sandbox_manager,
    set_sandbox_manager,
)


@pytest.fixture
def clean_sandbox():
    """保存并恢复全局沙盒管理器与当前消息索引。"""
    old = get_sandbox_manager()
    yield
    set_sandbox_manager(old)
    sm_mod.clear_current_message_index()


def _make_cm(messages, sandbox):
    """构造带沙盒回调的 ContextManager（与 ChatSession._sandbox_callback 同语义）。"""

    def cb(event):
        event_type = event.get("type")
        if event_type == "insert":
            sandbox.shift_indices(event["index"])
        elif event_type == "remove":
            sandbox.remap_indices(event["indices"])
        elif event_type == "fold":
            sandbox.fold_indices(event["indices"], event.get("insert_index"))

    def fake_summary(msgs, model=None):
        return ("", "## Primary Request and Intent\n- condensed", {"input": 5, "output": 5}, [])

    config = MockConfigAdapter({
        "model_context_tokens": 2000,
        "provider": "deepseek",
        "compaction": {
            "threshold_ratio": 0.5, "headroom_tokens": 200,
            "retain_ratio": 0.1, "model_policies": [],
        },
    })
    return ContextManager(
        messages, "test-model", summarize_fn=fake_summary,
        on_messages_changed=cb, config_port=config,
    )


def _long_messages(count=8):
    messages = [{"role": "system", "content": "sys"}]
    for i in range(count):
        role = "user" if i % 2 == 0 else "assistant"
        messages.append({"role": role, "content": f"m{i}-" + "x" * 400})
    return messages


# ── fold_indices 单元语义 ─────────────────────────────────

def test_fold_indices_keeps_folded_records_at_anchor(clean_sandbox):
    """折叠区间内的记录重挂到摘要位置，之后的记录按「删 N + 插 1」平移。"""
    sm = SandboxManager()
    sm.record_file_change("/f", None, "v1", 2)
    sm.record_file_change("/f", "v1", "v2", 4)
    sm.record_file_change("/g", None, "g0", 8)
    sm.update_message_index(8)

    sm.fold_indices([1, 2, 3, 4, 5, 6], insert_index=1)

    triples = sorted((r.file_path, r.message_index) for r in sm.get_all_file_changes())
    assert triples == [("/f", 1), ("/f", 1), ("/g", 3)]
    # message_history 与 file_history 一致
    assert [r.file_path for r in sm.message_history[1]] == ["/f", "/f"]
    assert [r.file_path for r in sm.message_history[3]] == ["/g"]
    assert sm.get_current_message_index_safe() == 3


def test_fold_indices_none_insert_index_keeps_records(clean_sandbox):
    """insert_index=None（降级删除）：被删记录重挂到最小被删索引，不丢弃。"""
    sm = SandboxManager()
    sm.record_file_change("/a", None, "a0", 1)
    sm.record_file_change("/b", None, "b0", 3)
    sm.record_file_change("/c", None, "c0", 5)

    sm.fold_indices([1, 3], insert_index=None)

    triples = sorted((r.file_path, r.message_index) for r in sm.get_all_file_changes())
    assert triples == [("/a", 1), ("/b", 1), ("/c", 3)]


def test_fold_indices_non_contiguous_removed(clean_sandbox):
    """非连续被移除索引：未删消息正确前移，被删记录重挂锚点。"""
    sm = SandboxManager()
    sm.record_file_change("/keep", None, "k", 2)
    sm.record_file_change("/gone", None, "g", 3)
    sm.record_file_change("/tail", None, "t", 6)

    # 删除 [1, 3]，在 1 处插入 1 条
    sm.fold_indices([1, 3], insert_index=1)

    triples = sorted((r.file_path, r.message_index) for r in sm.get_all_file_changes())
    assert triples == [("/gone", 1), ("/keep", 2), ("/tail", 5)]


def test_fold_indices_empty_is_noop(clean_sandbox):
    sm = SandboxManager()
    sm.record_file_change("/f", None, "v", 2)
    sm.fold_indices([], insert_index=1)
    assert [r.message_index for r in sm.get_all_file_changes()] == [2]


def test_fold_index_boundary_before_anchor(clean_sandbox):
    """锚点之前的记录索引不变（只影响锚点及其后）。"""
    sm = SandboxManager()
    sm.record_file_change("/head", None, "h", 1)
    sm.record_file_change("/tail", None, "t", 5)
    sm.fold_indices([3, 4], insert_index=3)
    triples = sorted((r.file_path, r.message_index) for r in sm.get_all_file_changes())
    assert triples == [("/head", 1), ("/tail", 4)]


# ── apply_replacement 事件语义 + 端到端还原 ────────────────

def test_apply_replacement_emits_fold_and_keeps_records(clean_sandbox):
    sm = SandboxManager()
    set_sandbox_manager(sm)
    messages = _long_messages()
    cm = _make_cm(messages, sm)

    sm.record_file_change("/tmp/f.txt", None, "v1", 2)
    sm.record_file_change("/tmp/f.txt", "v1", "v2", 4)
    sm.record_file_change("/tmp/g.txt", None, "g0", 8)

    cm.apply_replacement(1, 6, {"role": "system", "content": "[对话摘要] ckpt"})

    assert len(messages) == 4
    triples = sorted((r.file_path, r.message_index) for r in sm.get_all_file_changes())
    assert triples == [("/tmp/f.txt", 1), ("/tmp/f.txt", 1), ("/tmp/g.txt", 3)]


def test_compaction_then_restore_recovers_files(clean_sandbox, tmp_path):
    """压缩 → restore_to_message(0) 能把文件还原到压缩前的初始状态。"""
    f = tmp_path / "f.txt"
    g = tmp_path / "g.txt"
    f.write_text("v2", encoding="utf-8")
    g.write_text("g0", encoding="utf-8")

    sm = SandboxManager()
    set_sandbox_manager(sm)
    messages = _long_messages()
    cm = _make_cm(messages, sm)

    sm.record_file_change(str(f), None, "v1", 2)
    sm.record_file_change(str(f), "v1", "v2", 4)
    sm.record_file_change(str(g), None, "g0", 8)
    sm.update_message_index(8)

    cm.apply_replacement(1, 6, {"role": "system", "content": "[对话摘要] ckpt"})

    results = sm.restore_to_message(0)
    assert results.get(str(f)) is True
    assert results.get(str(g)) is True
    assert not f.exists(), "压缩前的文件应还原为「不存在」"
    assert not g.exists()


def test_compaction_then_new_change_then_restore(clean_sandbox, tmp_path):
    """压缩后新增文件变更：回滚到压缩点之后只还原新增变更（不误恢复已固化历史）。"""
    f = tmp_path / "f.txt"
    f.write_text("v2", encoding="utf-8")

    sm = SandboxManager()
    set_sandbox_manager(sm)
    messages = _long_messages()
    cm = _make_cm(messages, sm)

    sm.record_file_change(str(f), None, "v1", 2)
    sm.record_file_change(str(f), "v1", "v2", 4)
    cm.apply_replacement(1, 6, {"role": "system", "content": "[对话摘要] ckpt"})
    # 压缩后 messages = [sys0, ckpt1, u=2, a=3]；a 又修改 f.txt
    f.write_text("v3", encoding="utf-8")
    sm.record_file_change(str(f), "v2", "v3", 3)

    sm.restore_to_message(2)
    assert f.read_text(encoding="utf-8") == "v2"

    # 再回滚到压缩点之前 → 还原为不存在
    sm.restore_to_message(0)
    assert not f.exists()


def test_summarize_strategy_fold_event_preserves_sandbox(clean_sandbox):
    """降级摘要策略同样发 fold 事件（保留沙盒记录）。"""
    from src.core.compression import SummarizeStrategy

    sm = SandboxManager()
    set_sandbox_manager(sm)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
        {"role": "assistant", "content": "d"},
        {"role": "user", "content": "recent"},
    ]
    sm.record_file_change("/f", None, "v", 3)

    import src.core.compression as comp

    orig_select = comp.selector.select_for_compression
    orig_summarize = comp.summarizer.summarize
    try:
        comp.selector.select_for_compression = lambda *a, **k: [3, 4]
        comp.summarizer.summarize = lambda *a, **k: (
            "## Summary\n- kept", {"input": 5, "output": 5},
        )
        events = []
        SummarizeStrategy().compress(
            messages, "m", lambda *a, **k: None,
            lambda ev: events.append(ev), None, force=True,
        )
    finally:
        comp.selector.select_for_compression = orig_select
        comp.summarizer.summarize = orig_summarize

    assert events[0]["type"] == "fold"
    assert events[0]["indices"] == [3, 4]
    assert events[0]["insert_index"] == 1
    # 手动按事件重映射（会话回调语义），记录应保留
    sm.fold_indices(events[0]["indices"], events[0]["insert_index"])
    assert [(r.file_path, r.message_index) for r in sm.get_all_file_changes()] == [("/f", 1)]


def test_drop_strategy_fold_event_preserves_sandbox(clean_sandbox):
    """降级删除策略发 fold（insert_index=None），保留文件变更记录。"""
    from src.core.compression import DropStrategy

    sm = SandboxManager()
    set_sandbox_manager(sm)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ]
    sm.record_file_change("/f", None, "v", 2)

    events = []
    DropStrategy().compress(
        messages, "m", None, lambda ev: events.append(ev), None, force=True,
    )
    assert events[0]["type"] == "fold"
    assert events[0]["insert_index"] is None
    sm.fold_indices(events[0]["indices"], events[0]["insert_index"])
    # 删除 [1,2,3] → 锚点 1，记录保留
    assert [(r.file_path, r.message_index) for r in sm.get_all_file_changes()] == [("/f", 1)]


def test_session_callback_fold_dispatch(clean_sandbox):
    """ChatSession 的沙盒回调识别 fold 事件（不默认走 remove 丢弃）。"""
    import inspect

    from src.core import session as session_mod

    source = inspect.getsource(session_mod.ChatSession.initialize)
    assert '"fold"' in source or "'fold'" in source


def test_enforce_message_limit_preserves_records(clean_sandbox):
    """会话消息数上限裁剪保留沙盒记录（自动裁剪不回滚磁盘文件）。"""
    sm = SandboxManager()
    set_sandbox_manager(sm)
    messages = [{"role": "system", "content": "sys"}]
    for i in range(9):
        role = "user" if i % 2 == 0 else "assistant"
        messages.append({"role": role, "content": f"m{i}"})

    def cb(event):
        event_type = event.get("type")
        if event_type == "insert":
            sm.shift_indices(event["index"])
        elif event_type == "remove":
            sm.remap_indices(event["indices"])
        elif event_type == "fold":
            sm.fold_indices(event["indices"], event.get("insert_index"))

    config = MockConfigAdapter({"max_session_messages": 5})
    cm = ContextManager(
        messages, "m", summarize_fn=lambda *a, **k: None,
        on_messages_changed=cb, config_port=config,
    )
    sm.record_file_change("/f", None, "v", 3)

    removed = cm.enforce_message_limit()
    assert removed > 0
    assert [(r.file_path, r.message_index) for r in sm.get_all_file_changes()] == [("/f", 1)]


def test_auto_compaction_keeps_sandbox_records(clean_sandbox):
    """自动压缩（check_and_compress 引擎路径）后端到端保留沙盒记录。"""
    sm = SandboxManager()
    set_sandbox_manager(sm)
    messages = _long_messages(20)
    cm = _make_cm(messages, sm)
    sm.record_file_change("/f", None, "v", 2)
    sm.record_file_change("/g", None, "g", 30)

    cm.check_and_compress()

    from src.core.compaction.checkpoint import is_checkpoint_message

    assert any(is_checkpoint_message(m) for m in messages)
    recs = {(r.file_path, r.message_index) for r in sm.get_all_file_changes()}
    # 记录一条不丢；/f（压缩区间内）重挂到摘要位置
    assert {p for p, _ in recs} == {"/f", "/g"}
    assert dict(recs)["/f"] == 1
