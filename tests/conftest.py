"""测试套件全局 conftest：时间预算隔离 + TUI 视图测试共享桩。

两道职责：

1. **覆盖率插桩下的时间预算隔离**（原 ``tests/conftest.py`` 随 2026-10-23
   测试集删除而丢失，CI 注释仍在引用本钩子）：``pytest --cov`` 会逐行插桩，
   帧预算 / 性能型用例的耗时被放大数倍而必然超时。CI 因此**分两步**跑
   （先不带 coverage 跑全量，再带 coverage 只校验阈值），本钩子在
   ``--cov`` 模式下把这类用例显式 skip，保证「覆盖率门槛」与「性能预算」
   互不污染。

2. **TUI 视图测试共享桩**（``permissive_model``）：全屏/底部视图组件经
   ``getattr(model, ...)`` 探测大量可选字段，逐个视图手写完整模型既脆又
   冗长。宽容桩把「未声明属性」返回递归桩，使「视图能渲染」这类结构性
   断言（注册表装配正确、无 AttributeError/KeyError）可以集中表达；具体
   字段语义仍由各视图自己的测试覆盖。

注意：宽容桩**不是**万能 mock——它不模拟算术/比较语义之外的行为，视图若
对未声明字段做真运算，测试应当补真实字段而不是放宽桩。
"""

from __future__ import annotations

import os
from typing import Any, Iterator

import pytest

#: 时间预算 / 性能型用例（文件名特征）——覆盖率插桩下跳过，见模块 docstring。
_TIME_BUDGET_FILE_MARKERS = (
    "test_canvas_diff_perf.py",
    "test_floating_perf.py",
    "test_frame_budget.py",
)

#: 时序敏感用例（nodeid 片段）：插桩 + xdist 并发下帧调度抖动会改变期望的
#: 屏幕行数（非 coverage 模式下稳定通过；CI 先跑无插桩全量，故不影响功能验证）。
_TIME_SENSITIVE_NODEIDS = (
    "test_small_screen_cropped_window_stable",
)


def _coverage_measuring(config: pytest.Config) -> bool:
    """当前会话是否启用 coverage 逐行插桩。"""
    if not config.pluginmanager.hasplugin("pytest_cov"):
        return False
    source = getattr(config.option, "cov_source", None)
    return bool(source)


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item],
) -> None:
    """``--cov`` 模式下跳过时间预算型用例（插桩会放大耗时导致误报超时）。"""
    if not _coverage_measuring(config):
        return
    marker = pytest.mark.skip(reason="coverage 插桩会放大耗时：时间预算用例仅在不带 --cov 时运行")
    for item in items:
        name = os.path.basename(str(item.fspath))
        if name in _TIME_BUDGET_FILE_MARKERS or any(
            key in item.nodeid for key in _TIME_SENSITIVE_NODEIDS
        ):
            item.add_marker(marker)


# ═══════════════════════════════════════════════════════════
# 颜色能力隔离（autouse）
# ═══════════════════════════════════════════════════════════
#: 测试默认固定为「256 色终端」：清除 COLORTERM / NO_COLOR 并重置真彩能力
#: 缓存。否则在 COLORTERM=truecolor 的终端上跑测试时，TUI / renderer 会把
#: 256 色号渲染成 24-bit 序列，颜色断言（``\x1b[38;5;…m``）随本地环境随机
#: 失败。需要验证真彩路径的用例自行 `monkeypatch.setenv("COLORTERM",
#: "truecolor")` 并调用 `_reset_color_capability()` 即可。


def _reset_color_capability() -> None:
    """重置 TUI / renderer 的真彩检测缓存与 Style ANSI 缓存。"""
    from src.renderer.ansi.style import _reset_truecolor_cache as _reset_renderer
    from src.tui.core.color import _reset_truecolor_cache as _reset_tui
    from src.tui.core.style import Style

    _reset_tui()
    _reset_renderer()
    Style.to_ansi.cache_clear()


@pytest.fixture(autouse=True)
def _stable_color_capability(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """把测试环境的颜色能力固定为 256 色（与 CI 默认一致）。"""
    monkeypatch.delenv("COLORTERM", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    _reset_color_capability()
    yield
    _reset_color_capability()


class PermissiveValue:
    """递归宽容值：任意属性/调用/迭代都返回自身，布尔为假、数值为 0。

    仅用于「未声明字段探测」路径；需要真实语义的字段请显式赋值。
    """

    def __getattr__(self, name: str) -> "PermissiveValue":
        return self

    def __call__(self, *args: Any, **kwargs: Any) -> "PermissiveValue":
        return self

    def __iter__(self) -> Iterator[Any]:
        return iter(())

    def __len__(self) -> int:
        return 0

    def __bool__(self) -> bool:
        return False

    def __int__(self) -> int:
        return 0

    def __index__(self) -> int:
        return 0

    def __str__(self) -> str:
        return ""

    def __add__(self, other: Any) -> int:
        return 0

    __radd__ = __add__

    def __sub__(self, other: Any) -> int:
        return 0

    __rsub__ = __sub__

    def __lt__(self, other: Any) -> bool:
        return False

    def __le__(self, other: Any) -> bool:
        return True

    def __gt__(self, other: Any) -> bool:
        return False

    def __ge__(self, other: Any) -> bool:
        return True

    def __eq__(self, other: Any) -> bool:
        return False

    __hash__ = object.__hash__


class PermissiveModel:
    """TUI 视图组件的最小可用模型桩（未声明属性回退 :class:`PermissiveValue`）。"""

    def __init__(self, width: int = 80, height: int = 24, **fields: Any) -> None:
        self.width = width
        self.height = height
        self.fullscreen = ""
        self.bottom_view = ""
        self.blocks: list = []
        self.input_text = ""
        for key, value in fields.items():
            setattr(self, key, value)

    def __getattr__(self, name: str) -> Any:
        # 私有属性不做宽容（避免掩盖实现缺陷，如 __deepcopy__/__getstate__ 探测）。
        if name.startswith("__"):
            raise AttributeError(name)
        return PermissiveValue()


@pytest.fixture
def permissive_model() -> type[PermissiveModel]:
    """返回宽容模型桩类（视图测试按需实例化并注入视图状态）。"""
    return PermissiveModel
