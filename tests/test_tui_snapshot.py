"""src/tui/_snapshot — Token 速度快照惰性加载单元测试。

覆盖：
  - 首次调用惰性加载 get_token_speed_snapshot 并缓存
  - 缓存命中（二次调用不再导入）
  - 导入失败时标记不可用并返回 None
"""

from __future__ import annotations

import pytest

import src.tui._snapshot as snap


@pytest.fixture(autouse=True)
def reset_snapshot_cache():
    saved = snap._TOKEN_SPEED_SNAPSHOT
    snap._TOKEN_SPEED_SNAPSHOT = None
    yield
    snap._TOKEN_SPEED_SNAPSHOT = saved


def test_get_snapshot_loads_from_api_stats(monkeypatch):
    marker = {"loaded": True}

    def fake_loader():
        return marker

    monkeypatch.setattr(snap, "get_token_speed_snapshot", fake_loader, raising=False)
    # 构造「导入成功」路径：把 src.api.stats.get_token_speed_snapshot 换成假的
    import src.api.stats as api_stats

    monkeypatch.setattr(api_stats, "get_token_speed_snapshot", fake_loader)
    assert snap._get_snapshot() is fake_loader


def test_get_snapshot_caches_result():
    import src.api.stats as api_stats

    loads = []

    def fake_loader():
        loads.append(1)
        return lambda: 1

    import src.tui._snapshot as snap_mod

    # 直接操纵模块状态模拟缓存
    snap_mod._TOKEN_SPEED_SNAPSHOT = fake_loader
    first = snap_mod._get_snapshot()
    second = snap_mod._get_snapshot()
    assert first is second is fake_loader


def test_get_snapshot_import_failure_returns_none(monkeypatch):
    """导入失败：标记 False（不可用），返回 None。

    将 sys.modules['src.api.stats'] 置 None 令 from-import 抛 ImportError。
    """
    import sys

    saved = sys.modules.get("src.api.stats")
    sys.modules["src.api.stats"] = None
    try:
        assert snap._get_snapshot() is None
    finally:
        if saved is not None:
            sys.modules["src.api.stats"] = saved
        else:
            sys.modules.pop("src.api.stats", None)
    # 已标记不可用，二次调用不重试导入
    assert snap._TOKEN_SPEED_SNAPSHOT is False
    assert snap._get_snapshot() is None


def test_get_snapshot_false_marker_returns_none():
    """缓存为 False（不可用标记）时返回 None。"""
    saved = snap._TOKEN_SPEED_SNAPSHOT
    snap._TOKEN_SPEED_SNAPSHOT = False
    try:
        assert snap._get_snapshot() is None
    finally:
        snap._TOKEN_SPEED_SNAPSHOT = saved
