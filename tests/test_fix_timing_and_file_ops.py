"""修复回归测试 — 单调时钟耗时统计 + 文件路径/编码守卫 + 静态配置对齐。

覆盖：
  1. 耗时/速率计算误用墙钟 ``time.time()``（NTP 校时跳变会导致负耗时/速度突变）
     → 耗时口径统一改单调时钟，展示用时间戳仍保留墙钟；
  2. ``atomic_write_file`` 经 fs 能力接缝写入时丢弃 ``errors`` 参数（与本地
     回退路径行为不一致）；
  3. DOS 设备名（CON/NUL/PRN…）在 POSIX 上被误判为危险路径；
  4. ruff ``target-version`` 与 ``requires-python`` 不一致（会建议出 3.10+
     才能在 3.9 上运行的改写）；
  5. MCP/截图模块的未使用局部变量。
"""

from __future__ import annotations

import os
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


# ── 1. 单调时钟 ──────────────────────────────────────────────


@pytest.mark.parametrize("rel_path", [
    "src/core/stats/_token_speed.py",
    "src/api/model_async.py",
    "src/core/compression.py",
    "src/core/compaction/engine.py",
    "src/tools/file_base.py",
])
def test_elapsed_modules_do_not_use_wall_clock(rel_path):
    src = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
    assert "time.time()" not in src


@pytest.mark.parametrize("rel_path", [
    "src/observability/facade.py",
    "src/core/telemetry/metrics.py",
])
def test_uptime_uses_monotonic(rel_path):
    src = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
    assert "time.monotonic()" in src
    assert "time.time() - self._start_time" not in src


def test_session_elapsed_ignores_wall_clock_backjump(monkeypatch):
    from src.core.stats import _stats_core as core

    core.reset_stats()
    # 墙钟回退 10000 秒：会话耗时不得变成负值
    monkeypatch.setattr(core.time, "time", lambda: -10000.0)
    assert core.get_session_elapsed() >= 0.0


def test_session_start_time_remains_wall_clock():
    import time as _time

    from src.core.stats import _stats_core as core

    core.reset_stats()
    assert abs(core.get_session_start_time() - _time.time()) < 10.0


def test_token_speed_tracker_still_reports_tokens():
    from src.core.stats._token_speed import _TokenSpeedTracker

    tracker = _TokenSpeedTracker()
    tracker.add_token_size(10)
    tracker.add_token_size(5)
    snap = tracker.stats_snapshot()
    assert snap["total_tokens"] == 15
    assert snap["avg_speed"] >= 0.0
    assert tracker.window_speed >= 0.0


# ── 2. fs 能力接缝透传 errors ─────────────────────────────────


def test_atomic_write_file_forwards_errors_to_seam(monkeypatch, tmp_path):
    import src.tools.file_ops as fo

    captured: dict = {}

    class _Seam:
        def write_text(self, path, content, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(fo, "_fs_service", lambda: _Seam())
    fo.atomic_write_file(
        str(tmp_path / "a.txt"), "hi", encoding="utf-8", errors="strict",
    )
    assert captured.get("encoding") == "utf-8"
    assert captured.get("errors") == "strict"


def test_atomic_write_file_local_path_still_writes(tmp_path):
    import src.tools.file_ops as fo

    target = tmp_path / "b.txt"
    fo.atomic_write_file(str(target), "内容\n", encoding="utf-8", errors="strict")
    assert target.read_text(encoding="utf-8") == "内容\n"


def test_local_fs_provider_write_text_accepts_errors(tmp_path):
    from src.core.adapters.capabilities import LocalFsProvider

    target = tmp_path / "c.txt"
    LocalFsProvider().write_text(
        str(target), "内容", encoding="utf-8", errors="replace",
    )
    assert target.read_text(encoding="utf-8") == "内容"


# ── 3. DOS 设备名只在 Windows 判定 ───────────────────────────


def test_dos_device_name_allowed_on_posix():
    from src.tools.file_ops import validate_path_security

    if os.name == "nt":
        pytest.skip("Windows 上确属特殊设备名")
    validate_path_security("CON")
    validate_path_security(os.path.join(os.getcwd(), "NUL"))


def test_dos_device_name_blocked_on_windows(monkeypatch):
    import src.tools.file_ops as fo

    monkeypatch.setattr(fo.os, "name", "nt")
    with pytest.raises(ValueError):
        fo.validate_path_security("CON")
    with pytest.raises(ValueError):
        fo.validate_path_security("NUL.txt")


def test_etc_passwd_blocked_on_posix():
    """系统关键路径按原始路径拒绝（Termux 上 /etc/passwd 的 realpath 为 /system/etc/passwd）。"""
    from src.tools.file_ops import validate_path_security

    if os.name == "nt":
        pytest.skip("POSIX 系统关键路径用例")
    with pytest.raises(ValueError):
        validate_path_security("/etc/passwd")


def test_system_critical_path_checked_before_realpath(monkeypatch):
    """realpath 解析到别处时仍按原始归一化路径命中系统关键路径。"""
    import src.tools.file_ops as fo

    if os.name == "nt":
        pytest.skip("POSIX 路径语义用例")
    monkeypatch.setattr(fo, "system_critical_paths", lambda: frozenset({"/critical/secret"}))
    monkeypatch.setattr(fo.os.path, "realpath", lambda p: "/resolved/elsewhere")
    with pytest.raises(ValueError):
        fo.validate_path_security("/critical/secret")


# ── 4. 静态配置对齐 ──────────────────────────────────────────


def test_ruff_target_version_matches_min_python():
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.9"' in text
    assert 'target-version = "py39"' in text


def test_mypy_python_version_intentionally_kept_above_min():
    """mypy ``python_version`` 有意保持 3.13（不得改回 3.9）。

    实测对比（``python -m mypy src/tui``）：
      - python_version=3.13 → 1281 条（现有基线）；
      - python_version=3.9  → 2948 条（联合注解/``dataclass_transform``
        在 3.9 模式下的成片误报，会掩盖真实问题）。
    运行期 3.9 兼容由在 3.9.16 上执行的全量测试保障；ruff target 已对齐 py39。
    """
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'python_version = "3.13"' in text
    assert 'python_version = "3.9"' not in text


# ── 5. 未使用变量清理 ────────────────────────────────────────


def test_mcp_client_has_no_unused_pages_var():
    src = (REPO_ROOT / "src" / "mcp" / "client.py").read_text(encoding="utf-8")
    assert "pages = 0" not in src
    assert "pages = page + 1" not in src


def test_x11_window_list_has_no_unused_desktop_var():
    src = (REPO_ROOT / "src" / "tools" / "_screenshot" / "x11.py").read_text(
        encoding="utf-8",
    )
    assert "window_id, _desktop, pid_text" not in src
