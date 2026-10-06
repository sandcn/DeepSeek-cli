"""文件沙盒修复验证测试。

覆盖修复项：
- 线程安全：索引维护经 ``_FileHistory`` 持锁（并发 record 不抛 RuntimeError）
- 命令/会话删除消息后的沙盒索引同步（/undo、/retry、/edit、/clear、undo_last_round）
- 系统提词重建（system 条数变化）后的索引整体平移
- cp/mv/rm 目录树（含空子目录）记录与回滚
- 历史查询排序、``has_history`` 语义、历史上限边界与两表一致性
- 原子 I/O、FileChangeRecord 应用/回滚、SandboxContext token、多会话隔离
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
from types import SimpleNamespace

import pytest

from src.core import sandbox_manager as sm_mod
from src.core.sandbox_manager import (
    SandboxContext,
    SandboxManager,
    create_sandbox_manager,
    get_current_message_index,
    get_sandbox_manager,
    set_current_message_index,
    set_sandbox_manager,
)


@pytest.fixture
def clean_sandbox():
    """保存并恢复全局沙盒管理器与当前消息索引。"""
    old = get_sandbox_manager()
    yield
    set_sandbox_manager(old)
    sm_mod.clear_current_message_index()


# ── 线程安全 ──────────────────────────────────────────────

def test_concurrent_record_and_index_maintenance_no_crash(clean_sandbox):
    """并发 record 与索引维护（shift/remap）不得抛 RuntimeError。"""
    sm = SandboxManager(max_history_per_file=1000)
    set_sandbox_manager(sm)
    stop = threading.Event()
    errors: list = []

    def writer():
        i = 0
        try:
            while not stop.is_set():
                sm.record_file_change(f"/f{i % 60}", None, "c", i % 200)
                i += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    def maintainer():
        try:
            while not stop.is_set():
                sm.shift_indices(120)
                sm.shift_indices_by(120, -1)
                sm.remap_indices([300, 301])
                sm._rebuild_message_history()
                list(sm.file_history.items())
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=writer) for _ in range(4)]
    threads += [threading.Thread(target=maintainer) for _ in range(2)]
    for t in threads:
        t.start()
    time.sleep(0.8)
    stop.set()
    for t in threads:
        t.join()
    assert not errors, errors[:3]


def test_file_history_property_snapshot_is_copy(clean_sandbox):
    """file_history property 在锁下返回浅拷贝（外部改动不改内部 dict 结构）。"""
    sm = SandboxManager()
    sm.record_file_change("/a", None, "c", 1)
    snap = sm.file_history
    assert set(snap.keys()) == {"/a"}
    snap.pop("/a")
    assert "/a" in sm.file_history


# ── 索引维护 ──────────────────────────────────────────────

def test_shift_indices_by_negative_delta(clean_sandbox):
    sm = SandboxManager()
    sm.record_file_change("/a", None, "c", 3)
    sm.update_message_index(3)
    sm.shift_indices_by(2, -1)
    recs = sm.get_all_file_changes()
    assert [r.message_index for r in recs] == [2]
    assert sm.get_current_message_index_safe() == 2


def test_remap_drops_records_of_removed_indices(clean_sandbox):
    sm = SandboxManager()
    sm.record_file_change("/a", None, "c", 1)
    sm.record_file_change("/b", None, "c", 2)
    sm.record_file_change("/c", None, "c", 3)
    sm.remap_indices([2])
    recs = sorted(sm.get_all_file_changes(), key=lambda r: r.file_path)
    assert [(r.file_path, r.message_index) for r in recs] == [("/a", 1), ("/c", 2)]
    # 被删除索引 2 的原始记录（/b）已丢弃；索引 2 现在映射到 /c
    assert all(r.file_path != "/b" for r in sm.get_all_file_changes())
    assert [r.file_path for r in sm.message_history[2]] == ["/c"]


def test_shift_indices_then_history_order(clean_sandbox):
    sm = SandboxManager()
    sm.record_file_change("/a", None, "c", 5)
    sm.shift_indices(3)
    assert sm.get_all_file_changes()[0].message_index == 6


# ── 命令 / 会话消息删除后的索引同步 ─────────────────────────

def test_cmd_undo_remaps_sandbox(clean_sandbox):
    from src.core.commands._session_cmd import _cmd_undo

    sm = SandboxManager()
    set_sandbox_manager(sm)
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a"},
    ]
    sm.record_file_change("/x", None, "c", 2)
    ctx = SimpleNamespace(messages=messages, state={})
    _cmd_undo(ctx)
    assert sm.get_all_file_changes() == []
    assert len(messages) == 1


def test_cmd_retry_remaps_sandbox(clean_sandbox):
    from src.core.commands._session_cmd import _cmd_retry

    sm = SandboxManager()
    set_sandbox_manager(sm)
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a"},
        {"role": "tool", "content": "t"},
    ]
    sm.record_file_change("/y", None, "c", 3)
    ctx = SimpleNamespace(messages=messages, state={})
    _cmd_retry(ctx)
    assert sm.get_all_file_changes() == []
    assert len(messages) == 2


def test_cmd_edit_remaps_before_truncate(clean_sandbox):
    from src.core.commands._session_cmd import _cmd_edit

    sm = SandboxManager()
    set_sandbox_manager(sm)
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a"},
    ]
    sm.record_file_change("/z", None, "c", 2)
    ctx = SimpleNamespace(
        messages=messages, state={}, get_user_input=lambda: "u2",
    )
    _cmd_edit(ctx)
    assert messages[-1]["content"] == "u2"
    assert sm.get_all_file_changes() == []


def test_cmd_edit_remap_failure_keeps_messages(clean_sandbox):
    """remap 失败时消息不被删除（先 remap 后删，无中间态）。"""
    from src.core.commands._session_cmd import _cmd_edit

    class _BadSandbox:
        def remap_indices(self, indices):
            raise RuntimeError("boom")

    set_sandbox_manager(_BadSandbox())
    messages = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a"},
    ]
    ctx = SimpleNamespace(
        messages=messages, state={}, get_user_input=lambda: "u2",
    )
    _cmd_edit(ctx)
    assert len(messages) == 2
    assert messages[0]["content"] == "u1"


def test_cmd_clear_aligns_sandbox_index(clean_sandbox):
    from src.core.commands._session_cmd import _cmd_clear

    sm = SandboxManager()
    set_sandbox_manager(sm)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u"},
    ]
    ctx = SimpleNamespace(
        messages=messages,
        state={},
        session=None,
        build_system_prompt=lambda: ["sys"],
    )
    _cmd_clear(ctx)
    assert sm.get_all_file_changes() == []
    assert sm.get_current_message_index_safe() == len(ctx.messages) - 1
    assert get_current_message_index() == len(ctx.messages) - 1


def _make_messaging_manager(messages, sm):
    from src.core.internal.session._session_messaging_manager import (
        SessionMessagingManager,
    )

    return SessionMessagingManager(
        messages=messages,
        model_getter=lambda: "m",
        context_manager_getter=lambda: None,
        context_manager_setter=lambda v: None,
        sandbox_getter=lambda: sm,
        state_machine=None,
        emit_fn=lambda *a, **k: None,
        observability_port=SimpleNamespace(gauge=lambda *a, **k: None),
        retry_pending_getter=lambda: False,
        retry_pending_setter=lambda v: None,
    )


def test_session_undo_last_round_remaps_sandbox(clean_sandbox):
    sm = SandboxManager()
    set_sandbox_manager(sm)
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a"},
    ]
    sm.record_file_change("/u", None, "c", 2)
    mgr = _make_messaging_manager(messages, sm)
    removed = mgr.undo_last_round()
    assert removed == 2
    assert sm.get_all_file_changes() == []


def test_session_clear_messages_aligns_sandbox_index(clean_sandbox):
    sm = SandboxManager()
    set_sandbox_manager(sm)
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a"},
    ]
    mgr = _make_messaging_manager(messages, sm)
    mgr.clear_messages(
        lambda: [{"role": "system", "content": "s"}],
        lambda: ["s"],
    )
    assert sm.get_all_file_changes() == []
    assert sm.get_current_message_index_safe() == len(messages) - 1


# ── 系统提词重建后的索引平移 ────────────────────────────────

def test_rebuild_system_prompt_shifts_sandbox_indices(clean_sandbox):
    from src.core.agent import Agent

    sm = SandboxManager()
    set_sandbox_manager(sm)
    messages = [
        {"role": "system", "content": "a"},
        {"role": "system", "content": "b"},
        {"role": "system", "content": "c"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a"},
    ]
    sm.record_file_change("/f", None, "c", 3)
    sm.record_file_change("/g", None, "c", 4)
    sm.update_message_index(4)

    class _Stub:
        def __init__(self, msgs, parts):
            self.messages = msgs
            self._parts = parts

        def build_system_prompt(self):
            return self._parts

    stub = _Stub(messages, ["x", "y"])  # system 3 → 2（delta=-1）
    Agent.rebuild_system_prompt(stub)

    recs = sorted(sm.get_all_file_changes(), key=lambda r: r.file_path)
    assert [(r.file_path, r.message_index) for r in recs] == [("/f", 2), ("/g", 3)]
    assert sm.get_current_message_index_safe() == 3


# ── 目录树记录（cp/mv/rm） ─────────────────────────────────

async def _run_cp_mv_rm():
    from src.tools.cp import CpFunc
    from src.tools.mv import MvFunc
    from src.tools.rm import RmFunc

    d = tempfile.mkdtemp()

    # cp
    sm = SandboxManager()
    set_sandbox_manager(sm)
    src = os.path.join(d, "src")
    os.makedirs(os.path.join(src, "sub"))
    os.makedirs(os.path.join(src, "empty"))
    with open(os.path.join(src, "sub", "x.txt"), "w") as f:
        f.write("x")
    dst = os.path.join(d, "dst")
    set_current_message_index(5)
    assert "复制成功" in await CpFunc(source=src, destination=dst, recursive=True).execute()
    assert os.path.isdir(os.path.join(dst, "empty"))
    sm.restore_to_message(4)
    assert not os.path.exists(dst)
    assert os.path.isdir(src)

    # mv
    sm = SandboxManager()
    set_sandbox_manager(sm)
    mx = os.path.join(d, "mx")
    os.makedirs(os.path.join(mx, "sub"))
    os.makedirs(os.path.join(mx, "empty"))
    with open(os.path.join(mx, "sub", "y.txt"), "w") as f:
        f.write("y")
    mvdst = os.path.join(d, "mvdst")
    set_current_message_index(7)
    assert "移动成功" in await MvFunc(source=mx, destination=mvdst).execute()
    assert not os.path.exists(mx)
    sm.restore_to_message(6)
    assert os.path.isdir(os.path.join(mx, "empty"))
    assert open(os.path.join(mx, "sub", "y.txt")).read() == "y"
    assert not os.path.exists(mvdst)

    # rm
    sm = SandboxManager()
    set_sandbox_manager(sm)
    rx = os.path.join(d, "rx")
    os.makedirs(os.path.join(rx, "sub"))
    os.makedirs(os.path.join(rx, "empty"))
    with open(os.path.join(rx, "sub", "z.txt"), "w") as f:
        f.write("z")
    set_current_message_index(9)
    assert "删除成功" in await RmFunc(path=rx, recursive=True).execute()
    assert not os.path.exists(rx)
    sm.restore_to_message(8)
    assert os.path.isdir(os.path.join(rx, "empty"))
    assert open(os.path.join(rx, "sub", "z.txt")).read() == "z"


@pytest.mark.asyncio
async def test_directory_tools_record_and_restore_dirs(clean_sandbox):
    await _run_cp_mv_rm()


@pytest.mark.asyncio
async def test_write_file_records_created_parent_dirs(clean_sandbox, tmp_path):
    """write_file 隐式创建的父目录应记录并可回滚（批量记录）。"""
    from src.tools.write_file import WriteFileFunc

    sm = SandboxManager()
    set_sandbox_manager(sm)
    set_current_message_index(1)
    target = tmp_path / "a" / "b" / "c.txt"
    res = await WriteFileFunc(path=str(target), content="hi").execute()
    assert "写入成功" in res
    assert target.read_text() == "hi"

    dirs = {
        r.file_path for r in sm.get_all_file_changes()
        if r.record_type == "directory"
    }
    assert str(tmp_path / "a") in dirs
    assert str(tmp_path / "a" / "b") in dirs

    sm.restore_to_message(0)
    assert not target.exists()
    assert not (tmp_path / "a").exists()


def test_build_directory_tree_changes_helpers(tmp_path):
    from src.tools.file_ops import (
        build_directory_source_removal_changes,
        build_directory_target_creation_changes,
    )

    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    dst = tmp_path / "dst"

    creation = build_directory_target_creation_changes(str(src), str(dst), "cp")
    paths = {c[0] for c in creation}
    assert paths == {str(dst), str(dst / "sub")}
    assert all(c[1] is None and c[2] == "" and c[4] == "directory" for c in creation)

    removal = build_directory_source_removal_changes(str(src), "rm")
    rpaths = {c[0] for c in removal}
    assert rpaths == {str(src), str(src / "sub")}
    assert all(c[1] == "" and c[2] is None and c[4] == "directory" for c in removal)


# ── 历史查询 / 语义 ────────────────────────────────────────

def test_get_snapshot_uses_sorted_first_record(clean_sandbox):
    sm = SandboxManager()
    sm.record_file_change("/f", "A0", "A1", 10)
    sm.record_file_change("/f", "B0", "B1", 5)
    assert sm._fh.get_snapshot("/f", 1) == "B0"
    assert sm._fh.get_snapshot("/f", 7) == "B1"
    assert sm._fh.get_snapshot("/f", 11) == "A1"


def test_get_file_state_at_message_honours_record_deletion(clean_sandbox, tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("disk")
    sm = SandboxManager()
    sm.record_file_change(str(f), "old", None, 5)
    # 有记录显示该索引时已删除 → 返回 None，不得回退读磁盘
    assert sm.get_file_state_at_message(str(f), 5) is None


def test_history_limit_prunes_message_history(clean_sandbox):
    sm = SandboxManager(max_history_per_file=3)
    for i in range(5):
        sm.record_file_change("/f", None, str(i), i)
    assert sorted(sm.message_history.keys()) == [2, 3, 4]
    assert len(sm.get_all_file_changes()) == 3


def test_history_limit_zero_clears(clean_sandbox):
    sm = SandboxManager(max_history_per_file=0)
    sm.record_file_change("/f", None, "c", 1)
    assert sm.get_all_file_changes() == []
    assert 1 not in sm.message_history


def test_batch_record_creates_records(clean_sandbox):
    sm = SandboxManager()
    changes = [
        ("/a", None, "1", "cp", "file"),
        ("/b", "", None, "cp", "directory"),
    ]
    recs = sm.record_file_changes_batch(changes, 4)
    assert len(recs) == 2
    assert sm.get_current_message_index_safe() == 4
    assert len(sm.message_history[4]) == 2


# ── 原子 I/O / 记录应用 / context / 多会话 ──────────────────

def test_atomic_io_write_and_remove(tmp_path):
    from src.core._atomic_io import atomic_write_text, remove_path

    p = tmp_path / "d" / "f.txt"
    atomic_write_text(str(p), "hello")
    assert p.read_text() == "hello"
    atomic_write_text(str(p), "world")
    assert p.read_text() == "world"
    remove_path(str(p))
    assert not p.exists()
    remove_path(str(p))  # 不存在时静默返回


def test_file_change_record_apply_revert_atomic(tmp_path):
    from src.core.file_change_record import FileChangeRecord

    f = tmp_path / "f.txt"
    rec = FileChangeRecord(str(f), None, "content", 1)
    assert rec.apply() is True
    assert f.read_text() == "content"
    assert rec.revert() is True
    assert not f.exists()

    d = tmp_path / "adir"
    rec_d = FileChangeRecord(str(d), None, "", 2, record_type="directory")
    assert rec_d.apply() is True
    assert d.is_dir()
    assert rec_d.revert() is True
    assert not d.exists()


@pytest.mark.asyncio
async def test_file_change_record_apply_revert_async(tmp_path):
    from src.core.file_change_record import FileChangeRecord

    f = tmp_path / "f.txt"
    rec = FileChangeRecord(str(f), None, "async-content", 1)
    assert await rec.apply_async() is True
    assert f.read_text() == "async-content"
    assert await rec.revert_async() is True
    assert not f.exists()


def test_sandbox_context_restores_contextvar(clean_sandbox):
    set_current_message_index(5)
    with SandboxContext(9):
        assert get_current_message_index() == 9
    assert get_current_message_index() == 5


def test_create_sandbox_manager_owner_id(clean_sandbox):
    m = create_sandbox_manager(owner_id="session-1")
    assert get_sandbox_manager() is m
    assert m.owner_id == "session-1"


def test_session_sandbox_owner_isolation(clean_sandbox):
    """同一 ChatSession 复用沙盒；不同实例重建（多会话隔离）。"""
    from src.core.session import ChatSession

    s1 = ChatSession()
    s1.initialize()
    sm1 = get_sandbox_manager()
    assert sm1 is not None and sm1.owner_id == id(s1)

    s1.initialize()  # 同一会话重复初始化 → 复用（保留状态）
    assert get_sandbox_manager() is sm1

    s2 = ChatSession()
    s2.initialize()
    sm2 = get_sandbox_manager()
    assert sm2 is not None and sm2 is not sm1 and sm2.owner_id == id(s2)
