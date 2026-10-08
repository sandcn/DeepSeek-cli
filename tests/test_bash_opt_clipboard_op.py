"""bash_opt 剪贴板能力测试（op=clipboard 与 type 的 via='clipboard'）。

覆盖：op=clipboard 的 set / get / clear / append 与参数校验、剪贴板错误提示；
type via='clipboard' 的「写入 → 发粘贴键 → 恢复原剪贴板」流程与
restore_clipboard=false 的行为。
"""

from __future__ import annotations

import json

from src.tools import bash_opt as bash_opt_module
from src.tools._clipboard import ClipboardError
from src.tools._window_input.result import InputResult
from src.tools.bash_opt import BashOptFunc


class _FakeAgent:
    def __init__(self, records: dict):
        self._background_tasks = records
        self._subagent_tasks = {}


def _record(pid=4321):
    return {"read_buffer": "", "status": "running", "done": False, "pid": pid,
            "task": None}


class _Clipboard:
    """内存剪贴板替身（记录读写调用）。"""

    def __init__(self, content="原文"):
        self.content = content
        self.writes: list[str] = []
        self.reads = 0

    def read(self) -> str:
        self.reads += 1
        return self.content

    def write(self, text: str) -> None:
        self.writes.append(text)
        self.content = text

    def install(self, monkeypatch, *, read_error=None, write_error=None):
        def _read():
            if read_error is not None:
                raise read_error
            return self.read()

        def _write(text):
            if write_error is not None:
                raise write_error
            self.write(text)

        monkeypatch.setattr(bash_opt_module, "read_clipboard_text", _read)
        monkeypatch.setattr(bash_opt_module, "write_clipboard_text", _write)


def _patch_send(monkeypatch, recorder: list):
    def _impl(pid, action):
        recorder.append(action)
        return InputResult(action=action.name, backend="windows", window_pid=pid,
                           window_title="App", detail={"delivery": "sendinput"})
    monkeypatch.setattr(bash_opt_module, "send_window_input", _impl)


def _func(rec=None, **kwargs):
    func = BashOptFunc(task_id="bg-1", **kwargs)
    func.set_agent(_FakeAgent({"bg-1": rec or _record()}))
    return func


# ── op=clipboard ────────────────────────────────────────

async def test_clipboard_set_writes_text(monkeypatch):
    clip = _Clipboard()
    clip.install(monkeypatch)
    payload = json.loads(await _func(op="clipboard", text="hello").execute())
    assert clip.writes == ["hello"]
    assert payload["clipboard_action"] == "set" and payload["length"] == 5


async def test_clipboard_get_reads_text(monkeypatch):
    clip = _Clipboard("原始内容")
    clip.install(monkeypatch)
    payload = json.loads(await _func(op="clipboard").execute())
    assert payload["text"] == "原始内容" and payload["length"] == 4
    assert clip.writes == []


async def test_clipboard_get_truncates_long_text(monkeypatch):
    clip = _Clipboard("x" * 20)
    monkeypatch.setattr(BashOptFunc, "_CLIPBOARD_MAX_CHARS", 5)
    clip.install(monkeypatch)
    payload = json.loads(await _func(op="clipboard", clipboard_action="get").execute())
    assert payload["text"] == "xxxxx" and payload["truncated"] is True


async def test_clipboard_clear_and_append(monkeypatch):
    clip = _Clipboard("base")
    clip.install(monkeypatch)
    payload = json.loads(
        await _func(op="clipboard", clipboard_action="clear").execute())
    assert payload["clipboard_action"] == "clear" and clip.content == ""
    payload = json.loads(
        await _func(op="clipboard", clipboard_action="append", text="-tail").execute())
    assert payload["clipboard_action"] == "append"
    assert clip.content == "-tail" and payload["length"] == 5


async def test_clipboard_set_requires_text(monkeypatch):
    clip = _Clipboard()
    clip.install(monkeypatch)
    result = await _func(op="clipboard", clipboard_action="set").execute()
    assert "需要 text 参数" in result


async def test_clipboard_rejects_unknown_action(monkeypatch):
    clip = _Clipboard()
    clip.install(monkeypatch)
    result = await _func(op="clipboard", clipboard_action="paste").execute()
    assert "clipboard_action 取值非法" in result


async def test_clipboard_reports_backend_error(monkeypatch):
    clip = _Clipboard()
    clip.install(monkeypatch, write_error=ClipboardError("剪贴板被占用"))
    result = await _func(op="clipboard", text="x").execute()
    assert result.startswith("(剪贴板操作失败") and "剪贴板被占用" in result


# ── type via='clipboard' ────────────────────────────────

async def test_type_via_clipboard_pastes_and_restores(monkeypatch):
    clip = _Clipboard("原剪贴板")
    clip.install(monkeypatch)
    recorder: list = []
    _patch_send(monkeypatch, recorder)
    payload = json.loads(await _func(
        op="type", text="要输入的长文本", via="clipboard").execute())
    # 先写入剪贴板，再恢复原内容
    assert clip.writes == ["要输入的长文本", "原剪贴板"]
    assert [action.name for action in recorder] == ["key"]
    assert recorder[0].shortcut.display() == "ctrl+v"
    assert payload["via"] == "clipboard"
    assert payload["pasted_characters"] == 7
    assert payload["clipboard_restored"] is True


async def test_type_via_clipboard_can_keep_clipboard(monkeypatch):
    clip = _Clipboard("原剪贴板")
    clip.install(monkeypatch)
    recorder: list = []
    _patch_send(monkeypatch, recorder)
    payload = json.loads(await _func(
        op="type", text="abc", via="clipboard",
        restore_clipboard=False).execute())
    assert clip.writes == ["abc"]
    assert payload["clipboard_restored"] is False


async def test_type_via_clipboard_uses_explicit_paste_key(monkeypatch):
    clip = _Clipboard()
    clip.install(monkeypatch)
    recorder: list = []
    _patch_send(monkeypatch, recorder)
    await _func(op="type", text="x", via="clipboard",
                paste_key="shift+insert").execute()
    assert recorder[0].shortcut.display() == "shift+insert"


async def test_type_via_clipboard_reports_write_failure(monkeypatch):
    clip = _Clipboard()
    clip.install(monkeypatch, write_error=ClipboardError("剪贴板不可写"))
    result = await _func(op="type", text="x", via="clipboard").execute()
    assert result.startswith("(输入失败") and "剪贴板" in result


async def test_type_via_rejects_unknown_value(monkeypatch):
    clip = _Clipboard()
    clip.install(monkeypatch)
    result = await _func(op="type", text="x", via="magic").execute()
    assert "via 取值非法" in result
