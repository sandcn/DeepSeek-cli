"""输入动作序列（``op=sequence`` 的解析层）测试。

覆盖 ``_window_input.sequence``：结构校验（数组 / 步骤对象 / 未知 op / 步数
上限 / 各步骤必填参数）、步骤属性（window / settle / shot）与 ``wait_seconds``。
"""

from __future__ import annotations

import pytest

from src.tools._window_input.action import INPUT_OPS
from src.tools._window_input.sequence import (
    MAX_SEQUENCE_STEPS,
    SEQUENCE_KINDS,
    SequenceError,
    parse_sequence,
    wait_seconds,
)


def test_sequence_kinds_cover_input_ops_and_specials():
    assert set(INPUT_OPS).issubset(set(SEQUENCE_KINDS))
    assert {"wait", "screenshot", "window"}.issubset(set(SEQUENCE_KINDS))


def test_parse_sequence_accepts_action_steps():
    steps = parse_sequence([
        {"op": "click", "x": 10, "y": 20},
        {"op": "type", "text": "hi", "settle": 0.5},
        {"op": "key", "key": "enter", "shot": True},
    ])
    assert [step.kind for step in steps] == ["click", "type", "key"]
    assert [step.index for step in steps] == [1, 2, 3]
    assert steps[1].settle == 0.5
    assert steps[2].shot is True
    assert "op" not in steps[0].params  # op 已剥离，剩余参数交给构建器


def test_parse_sequence_accepts_single_step_mapping():
    steps = parse_sequence({"op": "click", "x": 1, "y": 2})
    assert len(steps) == 1 and steps[0].kind == "click"


def test_parse_sequence_accepts_special_steps():
    steps = parse_sequence([
        {"op": "wait", "seconds": 0.2},
        {"op": "screenshot", "path": "shot.png"},
        {"op": "window", "window_action": "always_on_top", "window": "#1"},
    ])
    assert wait_seconds(steps[0]) == 0.2
    assert steps[1].params["path"] == "shot.png"
    assert steps[2].window == "#1"
    assert steps[2].params["window_action"] == "always_on_top"


def test_parse_sequence_uses_action_key_alias():
    steps = parse_sequence([{"action": "click", "x": 0, "y": 0}])
    assert steps[0].kind == "click"


def test_parse_sequence_rejects_bad_structures():
    with pytest.raises(SequenceError):
        parse_sequence(None)
    with pytest.raises(SequenceError):
        parse_sequence("click")
    with pytest.raises(SequenceError):
        parse_sequence([])
    with pytest.raises(SequenceError):
        parse_sequence([{"x": 1, "y": 2}])          # 缺 op
    with pytest.raises(SequenceError):
        parse_sequence([{"op": "unknown"}])         # 未知 op
    with pytest.raises(SequenceError):
        parse_sequence([["click"]])                 # 步骤不是对象


def test_parse_sequence_rejects_special_step_without_required_params():
    with pytest.raises(SequenceError):
        parse_sequence([{"op": "screenshot"}])
    with pytest.raises(SequenceError):
        parse_sequence([{"op": "window"}])
    with pytest.raises(SequenceError):
        parse_sequence([{"op": "wait"}])
    with pytest.raises(SequenceError):
        parse_sequence([{"op": "wait", "seconds": -1}])
    with pytest.raises(SequenceError):
        parse_sequence([{"op": "wait", "seconds": 9999}])


def test_parse_sequence_rejects_too_many_steps():
    steps = [{"op": "click", "x": 0, "y": 0}] * (MAX_SEQUENCE_STEPS + 1)
    with pytest.raises(SequenceError) as error:
        parse_sequence(steps)
    assert "步骤过多" in str(error.value)


def test_step_window_and_settle_properties():
    step = parse_sequence([{"op": "click", "x": 0, "y": 0,
                            "window": " popup ", "settle": "1.5"}])[0]
    assert step.window == "popup"
    assert step.settle == 1.5
    assert "click" in step.describe()
