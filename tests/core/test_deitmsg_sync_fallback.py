"""/deitmsg 同步降级路径回归测试（未定义名 NameError 修复）。

背景：``DeitmsgPlugin.async_execute`` 内局部导入颜色常量
（``from ....core.constants import YELLOW, RESET, GREEN, DIM``），但同步降级
路径 ``execute`` 引用 ``YELLOW``/``RESET`` 时同样式常量并不在其作用域 →
``NameError``。该异常又被 ``except Exception`` 吞掉（仅 debug 日志），导致
非交互环境执行 ``/deitmsg`` 时**提示串从未写出**（f-string 求值先于
``write()`` 失败）——用户以为命令无响应。

修复：常量导入提升到模块级（唯一真源 ``src.core.constants``，纯常量模块无
循环导入）。本测试锁定：常量在模块作用域可见 + ``execute`` 实际写出提示 +
返回 True（不抛异常）。
"""

from __future__ import annotations

from src.core.commands.plugins import deitmsg_plugin as _mod
from src.core.commands.plugins.deitmsg_plugin import DeitmsgPlugin


def test_module_level_color_constants_available():
    """YELLOW/RESET/GREEN/DIM 在模块作用域可见（修复前仅在 async_execute 局部）。"""
    for name in ("YELLOW", "RESET", "GREEN", "DIM"):
        assert hasattr(_mod, name), f"缺少颜色常量 {name}（NameError 隐患）"


def test_execute_writes_fallback_notice(monkeypatch):
    """同步降级路径写出提示（修复前 NameError 被吞 → 无任何输出）。"""
    written: list[str] = []

    class _Port:
        def write(self, text, *args, **kwargs):
            written.append(text)

    monkeypatch.setattr(
        "src.core.adapters.output.get_default_output_port", lambda: _Port(),
    )
    ok = DeitmsgPlugin().execute(ctx=object())
    assert ok is True
    assert written, "同步降级路径未输出任何提示（NameError 被吞）"
    assert "/deitmsg" in written[0]
    # 提示串含 ANSI 颜色常量展开（YELLOW/RESET 已定义 → 非字面量占位）
    assert _mod.YELLOW in written[0]
    assert _mod.RESET in written[0]


def test_execute_never_raises_without_output_port(monkeypatch):
    """输出端口不可用时仍返回 True（不抛异常给调用方）。"""
    class _Boom:
        def write(self, text, *args, **kwargs):
            raise OSError("port closed")

    monkeypatch.setattr(
        "src.core.adapters.output.get_default_output_port", lambda: _Boom(),
    )
    assert DeitmsgPlugin().execute(ctx=object()) is True
