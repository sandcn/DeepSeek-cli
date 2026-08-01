"""test_color_capability — TrueColor 终端能力检测（Claude TUI parity 步骤 1.2）。

覆盖环境变量判定的三种场景：COLORTERM=truecolor → TrueColor；
COLORTERM 空 → Color256 降级；NO_COLOR 置位 → 强制降级；
TERM=xterm-direct → TrueColor。
"""

from __future__ import annotations

import os
from unittest.mock import patch

from src.tui.core.color import (
    TrueColor,
    Color256,
    auto_color,
    detect_truecolor,
    _reset_truecolor_cache,
)


def _with_env(**env):
    """构造环境变量上下文（先清缓存 + 恢复旧值）。"""
    old = {k: os.environ.get(k) for k in env}
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    _reset_truecolor_cache()
    return old


class TestDetectTrueColor:
    def test_colorterm_truecolor(self) -> None:
        old = _with_env(COLORTERM="truecolor", NO_COLOR=None, TERM="xterm-256color")
        try:
            assert detect_truecolor() is True
        finally:
            _restore_env(old)

    def test_colorterm_24bit(self) -> None:
        old = _with_env(COLORTERM="24bit", NO_COLOR=None, TERM="xterm-256color")
        try:
            assert detect_truecolor() is True
        finally:
            _restore_env(old)

    def test_no_color_forces_256(self) -> None:
        old = _with_env(COLORTERM="truecolor", NO_COLOR="1", TERM="xterm-256color")
        try:
            assert detect_truecolor() is False
        finally:
            _restore_env(old)

    def test_term_direct(self) -> None:
        old = _with_env(COLORTERM=None, NO_COLOR=None, TERM="xterm-direct")
        try:
            assert detect_truecolor() is True
        finally:
            _restore_env(old)

    def test_default_falls_back_256(self) -> None:
        old = _with_env(COLORTERM=None, NO_COLOR=None, TERM="xterm-256color")
        try:
            assert detect_truecolor() is False
        finally:
            _restore_env(old)


class TestBestEffortIntegration:
    def test_best_effort_truecolor_when_supported(self) -> None:
        old = _with_env(COLORTERM="truecolor", NO_COLOR=None, TERM="xterm-256color")
        try:
            assert isinstance(TrueColor.best_effort(10, 20, 30), TrueColor)
        finally:
            _restore_env(old)

    def test_best_effort_256_when_unsupported(self) -> None:
        old = _with_env(COLORTERM=None, NO_COLOR=None, TERM="xterm-256color")
        try:
            assert isinstance(TrueColor.best_effort(10, 20, 30), Color256)
        finally:
            _restore_env(old)

    def test_auto_color_integration(self) -> None:
        old = _with_env(COLORTERM="24bit", NO_COLOR=None, TERM="xterm-256color")
        try:
            assert isinstance(auto_color(200, 100, 50), TrueColor)
        finally:
            _restore_env(old)


def _restore_env(old: dict) -> None:
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    _reset_truecolor_cache()
