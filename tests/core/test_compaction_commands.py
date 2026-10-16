"""压缩命令插件单元测试（``/compact`` 与 ``/context``）。"""

from __future__ import annotations

from types import SimpleNamespace

from src.core.adapters.config import MockConfigAdapter
from src.core.compaction import ManualCompactionError
from src.core.commands.plugins.compact_plugin import CompactPlugin
from src.core.commands.plugins.context_plugin import ContextPlugin, _parse_token_arg
from src.core.context_manager import ContextManager


def _messages(n=20, content_len=400):
    messages = [{"role": "system", "content": "sys"}]
    for i in range(n):
        messages.append({"role": "user", "content": f"m{i}-" + "x" * content_len})
    return messages


def _config_port(**compaction):
    data = {"model_context_tokens": 2000, "provider": "deepseek",
            "compaction": {"threshold_ratio": 0.5, "headroom_tokens": 100,
                           "retain_ratio": 0.1, **compaction}}
    return MockConfigAdapter(data)


def _make_cm(config_port=None):
    return ContextManager(
        _messages(), "test-model",
        summarize_fn=lambda msgs, model=None: ("", "## condensed", {}, []),
        config_port=config_port or _config_port(),
    )


def _ctx(cm, config_port=None, arg=""):
    return SimpleNamespace(
        session=SimpleNamespace(context_manager=cm),
        context_manager=cm,
        arg=arg,
        config_port=config_port,
        state={},
    )


# ── /compact ──────────────────────────────────────────────

def test_compact_plugin_compacts(monkeypatch):
    cm = _make_cm()
    plugin = CompactPlugin()
    outs: list = []
    monkeypatch.setattr(plugin, "output", outs.append)
    assert plugin.execute(_ctx(cm)) is True
    assert outs and "已压缩" in outs[0]


def test_compact_plugin_reports_no_history(monkeypatch):
    cm = _make_cm()
    cm.messages[:] = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    plugin = CompactPlugin()
    outs: list = []
    monkeypatch.setattr(plugin, "output", outs.append)
    plugin.execute(_ctx(cm))
    assert outs and "暂无可压缩" in outs[0]


def test_compact_plugin_reports_unavailable(monkeypatch):
    plugin = CompactPlugin()
    outs: list = []
    monkeypatch.setattr(plugin, "output", outs.append)
    plugin.execute(_ctx(None))
    assert outs and "不可用" in outs[0]


def test_compact_plugin_maps_failure_code(monkeypatch):
    plugin = CompactPlugin()
    outs: list = []
    monkeypatch.setattr(plugin, "output", outs.append)

    class _CM:
        def compact_now(self):
            raise ManualCompactionError("busy", "busy now")

    plugin.execute(_ctx(_CM()))
    assert outs and "暂不可用" in outs[0]


# ── /context ──────────────────────────────────────────────

def test_parse_token_arg():
    assert _parse_token_arg("256k") == 256_000
    assert _parse_token_arg("1m") == 1_000_000
    assert _parse_token_arg("262144") == 262_144
    assert _parse_token_arg("") is None
    assert _parse_token_arg("abc") is None
    assert _parse_token_arg("-1") is None


class _WritableConfig(MockConfigAdapter):
    """模拟 ConfigPort 的 CONFIG_KEYS 映射写出（内存）。"""

    def set(self, key, value):
        if key == "MODEL_CONTEXT_TOKENS":
            self._data["model_context_tokens"] = int(value)
            return
        super().set(key, value)


def test_context_plugin_show(monkeypatch):
    cm = _make_cm()
    plugin = ContextPlugin()
    outs: list = []
    monkeypatch.setattr(plugin, "output", outs.append)
    plugin.execute(_ctx(cm, config_port=_config_port()))
    joined = "\n".join(outs)
    assert "上下文窗口" in joined
    assert "容量" in joined


def test_context_plugin_set_window(monkeypatch):
    cm = _make_cm()
    config_port = _WritableConfig({"model_context_tokens": 2000, "provider": "deepseek"})
    plugin = ContextPlugin()
    outs: list = []
    monkeypatch.setattr(plugin, "output", outs.append)
    ctx = _ctx(cm, config_port=config_port, arg="256k")
    plugin.execute(ctx)
    assert config_port.get_model_context_tokens() == 256_000
    assert ctx.state.get("model_context_tokens") == 256_000
    assert outs and "已设为 256000" in outs[-1]


def test_context_plugin_rejects_bad_arg(monkeypatch):
    plugin = ContextPlugin()
    outs: list = []
    monkeypatch.setattr(plugin, "output", outs.append)
    plugin.execute(_ctx(_make_cm(), config_port=_config_port(), arg="nope"))
    assert outs and "用法" in outs[0]
