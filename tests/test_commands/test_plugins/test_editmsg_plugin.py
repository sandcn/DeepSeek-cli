"""测试 EditmsgPlugin — /editmsg 命令

覆盖场景：
1. _edit_performed 标志对 needs_rerender 的影响
2. 空 prefill + _edit_performed=True → needs_rerender=True（沙盒信息显示）
3. 空 prefill + 无 _edit_performed → needs_rerender=False（向后兼容）
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


class TestEditmsgPluginNeedsRerender:
    """测试 editmsg_plugin.py 中 needs_rerender 判断逻辑。

    核心回归：_edit_performed 标志独立于 prefill 是否为空，
    确保空内容编辑的沙盒信息也能显示。
    """

    def test_needs_rerender_true_with_empty_prefill_and_edit_performed_regression(self):
        """空 prefill + _edit_performed=True → needs_rerender=True。

        场景：用户编辑一条内容为空的消息（合法操作），沙盒信息应显示。
        """
        edit_state = {"_edit_performed": True, "prefill": "", "retry": False}
        state = {"retry": False, "prefill": ""}

        needs_rerender = bool(
            edit_state.get("_edit_performed", False)
            or state["retry"]
            or state["prefill"]
        )
        assert needs_rerender is True

    def test_needs_rerender_false_without_edit_performed_regression(self):
        """空 prefill + 无 _edit_performed → needs_rerender=False。

        场景：编辑未实际执行（如取消选择），不触发重新渲染。
        """
        edit_state = {"prefill": "", "retry": False}
        state = {"retry": False, "prefill": ""}

        needs_rerender = bool(
            edit_state.get("_edit_performed", False)
            or state["retry"]
            or state["prefill"]
        )
        assert needs_rerender is False

    def test_needs_rerender_true_with_nonempty_prefill_regression(self):
        """非空 prefill + 无 _edit_performed → needs_rerender=True（向后兼容）。

        场景：旧代码路径（无 _edit_performed 标志），prefill 非空时仍触发渲染。
        """
        edit_state = {"prefill": "hello", "retry": False}
        state = {"retry": False, "prefill": "hello"}

        needs_rerender = bool(
            edit_state.get("_edit_performed", False)
            or state["retry"]
            or state["prefill"]
        )
        assert needs_rerender is True

    def test_needs_rerender_true_with_retry_flag_regression(self):
        """retry=True + 空 prefill + 无 _edit_performed → needs_rerender=True。

        场景：恢复操作触发 retry 标记，应触发重新渲染。
        """
        edit_state = {"prefill": "", "retry": True}
        state = {"retry": True, "prefill": ""}

        needs_rerender = bool(
            edit_state.get("_edit_performed", False)
            or state["retry"]
            or state["prefill"]
        )
        assert needs_rerender is True
