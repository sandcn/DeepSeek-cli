"""Python 版本兼容层测试 — 覆盖 src/_compat.py。

验证 dataclass 装饰器在 Python 3.9 下的 slots 兼容行为。
"""

import sys

import pytest

from src._compat import dataclass


@dataclass
class Plain:
    x: int = 0


@dataclass(slots=True)
class Slotted:
    y: str = ""
    z: int = 0


def test_plain_dataclass_init():
    p = Plain(x=5)
    assert p.x == 5


def test_plain_dataclass_default():
    assert Plain().x == 0


def test_plain_dataclass_eq():
    assert Plain(x=1) == Plain(x=1)


def test_slotted_dataclass_init():
    s = Slotted(y="hi", z=2)
    assert s.y == "hi"
    assert s.z == 2


def test_slotted_dataclass_defaults():
    s = Slotted()
    assert s.y == ""
    assert s.z == 0


def test_slotted_dataclass_no_dict():
    s = Slotted()
    # slots=True 时实例不应有 __dict__
    assert not hasattr(s, "__dict__")


def test_slotted_dataclass_repr():
    s = Slotted(y="a")
    assert "a" in repr(s)
