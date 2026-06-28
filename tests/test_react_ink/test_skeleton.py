"""Skeleton 组件单元测试。"""
from __future__ import annotations

import re

from src.chat_ui.components.skeleton import Skeleton

_ANSI_RE = re.compile(r'\033\[[\d;]*m')

def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub('', text)

def _has_ansi(text: str) -> bool:
    return bool(_ANSI_RE.search(text))

def _get_ansi_codes(text: str) -> list[str]:
    return _ANSI_RE.findall(text)


class TestSkeletonVariants:
    def test_text_variant_multiple_lines(self):
        sk = Skeleton(variant="text", lines=3, width=20)
        output = str(sk.render())
        stripped = _strip_ansi(output)
        lines = stripped.split("\n")
        assert len(lines) == 3

    def test_text_variant_last_line_shorter(self):
        sk = Skeleton(variant="text", lines=3, width=20)
        output = str(sk.render())
        stripped = _strip_ansi(output)
        lines = stripped.split("\n")
        assert len(lines[-1]) < len(lines[0])

    def test_circle_variant(self):
        sk = Skeleton(variant="circle")
        output = str(sk.render())
        assert "\u25CB" in _strip_ansi(output)  # ○

    def test_rect_variant(self):
        sk = Skeleton(variant="rect", width=10)
        output = str(sk.render())
        assert "\u258C" in _strip_ansi(output)  # ▌

    def test_invalid_variant_falls_back_to_text(self):
        sk = Skeleton(variant="invalid")
        assert sk._variant == "text"

    def test_single_line_text(self):
        sk = Skeleton(variant="text", lines=1, width=20)
        output = str(sk.render())
        stripped = _strip_ansi(output)
        assert "\n" not in stripped


class TestSkeletonDimStyle:
    def test_animated_dim(self):
        sk = Skeleton(variant="text", animated=True)
        output = str(sk.render())
        codes = _get_ansi_codes(output)
        assert any("2" in c and "3" not in c for c in codes), f"应含 dim(2): {codes}"


class TestSkeletonEdgeCases:
    def test_min_lines(self):
        sk = Skeleton(variant="text", lines=0)
        assert sk._lines >= 1

    def test_min_width(self):
        sk = Skeleton(variant="text", width=1)
        assert sk._width >= 4

    def test_empty_variant_string(self):
        sk = Skeleton(variant="")
        assert sk._variant == "text"


class TestSkeletonUpdate:
    def test_update_variant(self):
        sk = Skeleton(variant="text")
        assert sk.update({"variant": "circle"}) is True
        assert sk.update({"variant": "circle"}) is False

    def test_update_lines(self):
        sk = Skeleton(variant="text", lines=2)
        assert sk.update({"lines": 5}) is True

    def test_update_no_change(self):
        sk = Skeleton(variant="text", lines=3, width=20)
        assert sk.update({"variant": "text", "lines": 3}) is False


class TestSkeletonRenderVNode:
    def test_render_vnode(self):
        sk = Skeleton(variant="text", lines=3)
        vnode = sk.render_vnode()
        assert vnode.type == "skeleton"
        assert vnode.key == "skeleton"
        assert vnode.props.get("variant") == "text"
        assert vnode.props.get("lines") == 3
