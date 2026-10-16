"""窗口探测（``_window_input.probe_window``）单元测试。

``probe_window`` 是 ``bash_opt`` ``op=keys`` 自动路由的判定依据：只定位窗口
不注入；非法 PID / 无可用后端 / 后端未实现定位 / 定位异常一律返回 ``None``
（调用方据此回退终端通道）。
"""

from __future__ import annotations

from src.tools import _window_input as window_input_module


class _StubBackend:
    def __init__(self, *, result=None, error: Exception | None = None,
                 with_locate: bool = True):
        self._result = result
        self._error = error
        self.locate_calls: list[int] = []
        if with_locate:
            self.locate = self._locate

    def supports(self) -> bool:
        return True

    def _locate(self, pid):
        self.locate_calls.append(pid)
        if self._error is not None:
            raise self._error
        return self._result

    def send(self, pid, action):  # pragma: no cover - 契约占位
        raise NotImplementedError


def _use_backend(monkeypatch, backend):
    monkeypatch.setattr(window_input_module, "resolve_backend", lambda: backend)


def test_probe_window_returns_target(monkeypatch):
    sentinel = object()
    backend = _StubBackend(result=sentinel)
    _use_backend(monkeypatch, backend)
    assert window_input_module.probe_window(1234) is sentinel
    assert backend.locate_calls == [1234]


def test_probe_window_none_when_no_window(monkeypatch):
    backend = _StubBackend(result=None)
    _use_backend(monkeypatch, backend)
    assert window_input_module.probe_window(1234) is None


def test_probe_window_swallows_backend_error(monkeypatch):
    backend = _StubBackend(error=RuntimeError("枚举窗口失败"))
    _use_backend(monkeypatch, backend)
    assert window_input_module.probe_window(1234) is None


def test_probe_window_without_locate_is_none(monkeypatch):
    backend = _StubBackend(with_locate=False)
    _use_backend(monkeypatch, backend)
    assert window_input_module.probe_window(1234) is None


def test_probe_window_without_backend_is_none(monkeypatch):
    _use_backend(monkeypatch, None)
    assert window_input_module.probe_window(1234) is None


def test_probe_window_rejects_invalid_pid(monkeypatch):
    backend = _StubBackend(result=object())
    _use_backend(monkeypatch, backend)
    for pid in (0, -1, None, "1234", True, 1.5):
        assert window_input_module.probe_window(pid) is None
    assert backend.locate_calls == []
