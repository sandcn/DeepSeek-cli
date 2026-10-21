"""``/usage`` 命令 — 右侧用量浮窗开关语义与已删除命令清单。"""

from __future__ import annotations

from types import SimpleNamespace

import src.core.commands._usage_cmd as usage_cmd
from src.core.commands._usage_cmd import UsageCommand
from src.plugins.manifest_entries import COMMAND_PLUGIN_ENTRIES

_REMOVED = {"help", "sandbox", "undo", "cost"}


class _FakeChatUI:
    """最小 ChatUI 替身：记录浮窗开关调用与 provider。"""

    def __init__(self) -> None:
        self.open_state = False
        self.open_calls = 0
        self.close_calls = 0
        self.provider = None

    def is_usage_floating_window_open(self) -> bool:
        return self.open_state

    def open_usage_floating_window(self, provider) -> bool:
        self.open_calls += 1
        self.provider = provider
        self.open_state = True
        return True

    def close_usage_floating_window(self) -> bool:
        self.close_calls += 1
        if not self.open_state:
            return False
        self.open_state = False
        return True


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(
        arg="", state={"model": "test-model"}, config_port=None, messages=[],
    )


def _patch_provider(monkeypatch, sections):
    monkeypatch.setattr(usage_cmd, "build_usage_sections", lambda _ctx: sections)


def test_usage_toggles_open_then_close(monkeypatch):
    ui = _FakeChatUI()
    sections = [{"title": "Token 用量", "rows": [("合计", "1.2kt", "info", None)]}]
    _patch_provider(monkeypatch, sections)
    monkeypatch.setattr(usage_cmd, "_active_chat_ui", lambda: ui)

    plugin = UsageCommand()
    assert plugin.execute(_ctx()) is True
    assert ui.open_calls == 1
    assert ui.open_state is True
    assert ui.provider() == sections

    # 第二次 /usage 关闭（不重复开窗）
    assert plugin.execute(_ctx()) is True
    assert ui.close_calls == 1
    assert ui.open_state is False
    assert ui.open_calls == 1

    # 第三次重新打开
    assert plugin.execute(_ctx()) is True
    assert ui.open_calls == 2
    assert ui.open_state is True


def test_usage_meta_is_read_only_direct_during_round():
    meta = UsageCommand().meta
    assert meta.name == "usage"
    assert meta.direct_during_round is True
    assert meta.queue_only is False


def test_removed_commands_absent_from_manifest():
    names = {
        str(entry.get("config", {}).get("name", ""))
        for entry in COMMAND_PLUGIN_ENTRIES
    }
    assert not (_REMOVED & names)
    assert "usage" in names


def test_removed_commands_not_declared():
    # 导入命令模块即触发 declare_command_plugin（声明，不注册）。
    import src.core.commands  # noqa: F401 — 触发内置命令模块导入
    import src.core.commands.plugins  # noqa: F401

    from src.core.commands.base import declared_command_plugin

    for name in sorted(_REMOVED):
        assert declared_command_plugin(name) is None, name


def _token_rows(monkeypatch, stats):
    """构建统计区块并返回 Token 用量行（固定价格，屏蔽真实配置）。"""
    import src.core.stats as stats_mod

    monkeypatch.setattr(stats_mod, "get_token_stats", lambda: stats)
    monkeypatch.setattr(stats_mod, "get_per_second_speed", lambda: 0.0)
    monkeypatch.setattr(stats_mod, "get_session_elapsed", lambda: 0.0)
    monkeypatch.setattr(usage_cmd, "_token_prices", lambda _ctx, _model: {
        "input": 1.0, "output": 2.0, "input_cache_hit": 0.1,
    })
    sections = usage_cmd.build_usage_sections(_ctx())
    token = next(sec for sec in sections if sec["title"] == "Token 用量")
    return token["rows"]


def _cache_hit_rate_row(monkeypatch, stats):
    rows = _token_rows(monkeypatch, stats)
    return next(item for item in rows if item[0] == "缓存命中率")


def test_build_usage_sections_includes_cache_hit_rate(monkeypatch):
    row = _cache_hit_rate_row(monkeypatch, {
        "input": 1000,
        "output": 120,
        "calls": 3,
        "input_cache_hit": 875,
        "input_cache_miss": 125,
    })
    assert row[1] == "87.5%"


def test_build_usage_sections_shows_dash_without_cache_split(monkeypatch):
    row = _cache_hit_rate_row(monkeypatch, {
        "input": 1000,
        "output": 120,
        "calls": 1,
        "input_cache_hit": 0,
        "input_cache_miss": 0,
    })
    assert row[1] == "\u2014"


def test_build_usage_sections_uses_agent_input_for_display_total(monkeypatch):
    rows = _token_rows(monkeypatch, {
        "input": 1000,
        "input_delta": 300,
        "output": 100,
        "calls": 2,
        "input_cache_hit": 875,
        "input_cache_miss": 125,
    })
    by_label = {row[0]: row for row in rows}
    assert by_label["输入（Agent 新增）"][1] == "300t"
    assert by_label["输入（未命中）"][1] == "125t"  # 计费明细保持原始值
    assert by_label["合计"][1] == "400t"  # Agent 新增 300 + 输出 100
