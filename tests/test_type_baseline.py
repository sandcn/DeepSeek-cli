"""类型检查基线（P0-2）— 防 mypy 错误数回升 + dataclass_transform 行为固化。

背景：``src/_compat.py`` 的 ``dataclass`` 是标准库 ``dataclasses.dataclass``
的运行时包装（兼容 Python<3.10 的 ``slots`` 参数）。静态类型检查器无法自动
推断该包装生成的 ``__init__`` 签名，导致全部 ``@dataclass`` 类（如
``ink.output.StyledRun``）的构造调用被误报 "Too many arguments"（历史 1000+
条）。修复：为 ``dataclass`` 声明 ``@dataclass_transform``（PEP 681）。

本文件固化两点：
  1. ``dataclass_transform`` 已挂载 + 包装后的 dataclass 运行时行为正确
     （构造/默认值/frozen/``field(init=False)``/slots）；
  2. mypy 全项目错误数不超过基线（默认跳过；设 ``RUN_MYPY_BASELINE=1`` 启用，
     因 mypy 全量检查耗时约 1 分钟，不进入默认快速测试路径）。
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

#: mypy 错误数基线（``python -m mypy src/tui``）。
#: 2026-10 dataclass_transform 修复前 2403 → dataclass_transform 修复后 1403
#: → P1-1 巨型组件拆分（事件处理/协议/渲染器/搜索辅助提取为模块级函数）
#: 后 1281 → 2026-10 类型治理（mixin 宿主契约 ``TYPE_CHECKING`` 声明、
#: ``h()`` children 类型放宽、PEP 562 代理模块属性声明、``_next_hook``
#: 泛型化、``callable`` → ``Callable[..., Any]``、视图 Optional 状态收窄、
#: 属性声明 ``object`` → ``Any`` 等）后 **528**（冷缓存实测；同口径
#: ``python -m mypy`` 全项目 604）。
#: 后续新增错误须修复或先下调基线下调来源再更新本值（禁止上调后放任增长）。
_MYPY_ERROR_BASELINE = 530

#: 基线检查范围（与 pyproject ``[tool.mypy] files`` 对齐的 tui 子集）
_MYPY_TARGET = "src/tui"

#: 分模块基线（``python -m mypy src`` 单次运行，按报错文件所属顶层包分桶）。
#:
#: 背景：原基线只守 ``src/tui``，其余模块（renderer / core / tools / …）的
#: 类型退化无人拦截。本表为每个顶层包设定上限（**只允许下调**）：新增包须
#: 以 0 为起点（未登记包出现任何错误即失败），下调请同步改本表。
#:
#: 口径说明：数字来自 ``python -m mypy src``（Python 3.9.16）按文件分桶计数，
#: ``"(src 顶层)"`` 为 ``src/*.py`` 直接文件（无子包）。
_MYPY_MODULE_BASELINE = {
    "src/tui": 229,
    "src/renderer": 108,
    "src/core": 84,
    "src/tools": 76,
    "src/plugins": 30,
    "src/config": 14,
    "src/api": 13,
    "src/kernel": 9,
    "src/app_loop": 8,
    "src/prompt_builder": 7,
    "src/mcp": 7,
    "src/clawbot": 6,
    "(src 顶层)": 6,
    "src/skills": 3,
    "src/app_init": 3,
    "src/notifications": 2,
}

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _mypy_available() -> bool:
    """mypy 可执行文件或模块是否可用。"""
    if shutil.which("mypy") is not None:
        return True
    return importlib.util.find_spec("mypy") is not None


def _count_mypy_errors(output: str) -> int:
    """统计 mypy 输出中的 error 行数（排除 note/摘要行）。"""
    return sum(1 for line in output.splitlines() if ": error:" in line)


# ═══════════════════════════════════════════════════════════
# dataclass_transform 行为固化（无需 mypy，默认运行）
# ═══════════════════════════════════════════════════════════


def test_compat_dataclass_is_dataclass_transform_decorated():
    """``src._compat.dataclass`` 已声明为 dataclass_transform（PEP 681）。

    通过 ``__dataclass_transform__`` 属性探测——``typing.dataclass_transform``
    装饰后会在被装饰对象上写入该标记（CPython 实现行为）；无该属性时静态
    检查器无法推断包装类签名（历史误报根因）。
    """
    from src._compat import dataclass

    marker = getattr(dataclass, "__dataclass_transform__", None)
    # 3.11 起标准库实现写入标记；若解释器无该属性（<3.11 且无 typing_extensions
    # 的降级分支），跳过属性断言但仍验证运行时行为（下方用例）。
    if marker is None:
        pytest.skip("当前解释器未提供 dataclass_transform 运行时标记")
    assert isinstance(marker, dict)


def test_compat_dataclass_constructs_and_defaults():
    """经 ``_compat.dataclass`` 装饰的类：构造/默认值/相等性正常。"""
    from src._compat import dataclass

    @dataclass
    class Point:
        x: int
        y: int = 0

    assert Point(1).x == 1
    assert Point(1).y == 0
    assert Point(1, 2) == Point(1, 2)
    assert Point(1, 2) != Point(1, 3)


def test_compat_dataclass_frozen_and_init_false_field():
    """frozen + ``field(init=False)``（StyledRun 同构用法）行为正确。"""
    from dataclasses import field

    from src._compat import dataclass

    @dataclass(frozen=True)
    class Run:
        text: str
        style: object | None = None
        width: int = field(init=False, repr=False, compare=False, default=0)

    r = Run("abc")
    assert r.width == 0
    # init=False 字段不参与构造签名（传入会 TypeError——固化签名）
    with pytest.raises(TypeError):
        Run("abc", None, 5)  # type: ignore[call-arg]
    # frozen：不可变
    with pytest.raises(Exception):
        r.text = "x"  # type: ignore[misc]


def test_compat_dataclass_slots_applied_when_supported():
    """``slots=True`` 在 Python>=3.10 生效（实例无 ``__dict__``）。"""
    from src._compat import dataclass

    @dataclass(slots=True)
    class Slotted:
        a: int = 1

    inst = Slotted()
    assert inst.a == 1
    if sys.version_info >= (3, 10):
        assert not hasattr(inst, "__dict__")


# ═══════════════════════════════════════════════════════════
# mypy 错误数基线（默认跳过；RUN_MYPY_BASELINE=1 启用）
# ═══════════════════════════════════════════════════════════


@pytest.mark.skipif(
    os.environ.get("RUN_MYPY_BASELINE") != "1",
    reason="mypy 全量检查耗时较长；设 RUN_MYPY_BASELINE=1 启用",
)
def test_mypy_error_count_within_baseline():
    """mypy（``src/tui``）错误数不得超过基线（防类型退化）。"""
    if not _mypy_available():
        pytest.skip("mypy 不可用")

    proc = subprocess.run(
        [sys.executable, "-m", "mypy", _MYPY_TARGET],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
    )
    output = proc.stdout + proc.stderr
    count = _count_mypy_errors(output)
    assert count <= _MYPY_ERROR_BASELINE, (
        f"mypy 错误数 {count} 超过基线 {_MYPY_ERROR_BASELINE}：\n{output[-4000:]}"
    )


# ═══════════════════════════════════════════════════════════
# 分模块 mypy 基线（默认跳过；RUN_MYPY_BASELINE=1 启用）
# ═══════════════════════════════════════════════════════════


def _count_mypy_errors_by_module(output: str) -> dict:
    """把 mypy 输出按报错文件所属顶层包分桶计数。

    ``src/tui/app/x.py:1: error: ...`` → ``src/tui``；
    ``src/paths.py:1: error: ...`` → ``"(src 顶层)"``。
    """
    counts: dict = {}
    for line in output.splitlines():
        if ": error:" not in line:
            continue
        path = line.split(":", 1)[0].strip()
        parts = path.replace("\\", "/").split("/")
        if len(parts) >= 3 and parts[0] == "src":
            bucket = f"src/{parts[1]}"
        else:
            bucket = "(src 顶层)"
        counts[bucket] = counts.get(bucket, 0) + 1
    return counts


@pytest.mark.skipif(
    os.environ.get("RUN_MYPY_BASELINE") != "1",
    reason="mypy 全量检查耗时较长；设 RUN_MYPY_BASELINE=1 启用",
)
def test_mypy_errors_per_module_within_baseline():
    """每个顶层包的错误数不得超过分模块基线（防「只守 tui」的类型退化）。

    未登记在 ``_MYPY_MODULE_BASELINE`` 的包以 0 为上限——新增包必须类型干净，
    或显式登记基线后下调维护。
    """
    if not _mypy_available():
        pytest.skip("mypy 不可用")

    proc = subprocess.run(
        [sys.executable, "-m", "mypy", "src"],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
    )
    output = proc.stdout + proc.stderr
    counts = _count_mypy_errors_by_module(output)
    over = {
        module: (count, _MYPY_MODULE_BASELINE.get(module, 0))
        for module, count in counts.items()
        if count > _MYPY_MODULE_BASELINE.get(module, 0)
    }
    assert not over, (
        "以下模块 mypy 错误数超过分模块基线（只允许下调，见 _MYPY_MODULE_BASELINE）：\n"
        + "\n".join(
            f"  {module}: {count} > {baseline}" for module, (count, baseline) in sorted(over.items())
        )
    )
