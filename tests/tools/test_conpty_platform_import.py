"""非原生 Windows 环境导入回归（Cygwin/MSYS2 msys/POSIX 无 ``msvcrt``）。

历史故障：``_conpty`` 模块级 ``import msvcrt``，而 ``src.tools.bash`` 无条件
``from . import _conpty``；在没有 msvcrt 的解释器上，整个工具包导入失败 →
应用启动即 ``❌ 致命错误: No module named 'msvcrt'``。

修复后：``msvcrt`` 缺失时降级为 None，``is_available()`` 返回 False，
调用方回退 PIPE 路径；``ConPtyProcess`` 构造不触碰 msvcrt。
"""

from __future__ import annotations

import asyncio
import builtins
import importlib.util
import os
import threading

from src.tools import _conpty


def _load_without_msvcrt(monkeypatch):
    """按「没有 msvcrt 的 POSIX 解释器」形态加载一份全新的 ``_conpty``。"""
    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "msvcrt":
            raise ImportError("No module named 'msvcrt'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    # _load_kernel32 以 os.name 判定平台；模拟非原生 Windows
    monkeypatch.setattr(os, "name", "posix")
    spec = importlib.util.spec_from_file_location(
        "_conpty_no_msvcrt", _conpty.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_module_import_survives_missing_msvcrt(monkeypatch):
    module = _load_without_msvcrt(monkeypatch)
    assert module.msvcrt is None
    assert module.is_available() is False


def test_process_construction_without_msvcrt(monkeypatch):
    module = _load_without_msvcrt(monkeypatch)
    process = module.ConPtyProcess(
        pid=1,
        process_handle=0,
        pcon_state={"handle": 0},
        input_write_handle=0,
        output_read_handle=0,
        reader=asyncio.StreamReader(),
        done_event=threading.Event(),
        exit_code_box={},
    )
    assert process.write_fd is None
    process.close()
