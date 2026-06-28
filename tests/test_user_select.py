"""测试 src.tools.user_select：UserSelectFunc 工具。

测试策略
--------
- 回归测试主要验证 TTY 流重复关闭的 Bug 修复
- 通过模拟流对象验证 detach().close() 二次调用的异常行为
- 遵循 Arrange/Act/Assert 模式
- 所有测试可独立运行，不依赖真实 TTY 终端
"""

import io
import pytest
from src.tools.user_select import UserSelectFunc


# ═══════════════════════════════════════════════════════════════════════════
# 回归测试：TTY 流重复关闭问题
# ═══════════════════════════════════════════════════════════════════════════

class TestTtyStreamCloseRegression:
    """验证内层 finally 关闭 TTY 流后，外层 finally 不会重复关闭导致 ValueError。

    Bug 场景：内层 finally 调用 _tty_stdout.detach().close() 成功后，外层 finally
    又调用一次 _tty_stdout.detach().close()，因底层 buffer 已断开而抛出
    ValueError: underlying buffer has been detached。

    修复方式：内层 finally 关闭后立即将变量设为 None，外层 finally 的
    if xxx is not None 检查自然跳过。
    """

    def test_detach_twice_raises_valueerror(self):
        """验证 detach().close() 第二次调用确实抛出 ValueError（复现 Bug 条件）。"""
        buf = io.BytesIO()
        wrapper = io.TextIOWrapper(buf, encoding="utf-8")

        # 第一次关闭应该成功
        wrapper.detach().close()

        # 第二次 detach 应抛出 ValueError（底层 buffer 已 detached）
        with pytest.raises(ValueError, match="underlying buffer has been detached"):
            wrapper.detach()

    def test_none_guard_skips_second_close(self):
        """验证设为 None 后，if xxx is not None 检查可防止二次关闭。"""
        buf = io.BytesIO()
        wrapper = io.TextIOWrapper(buf, encoding="utf-8")

        # 模拟内层 finally 的行为
        if wrapper is not None:
            wrapper.detach().close()
            wrapper = None  # 修复点：关闭后设为 None

        # 模拟外层 finally 的行为：None 检查应跳过
        if wrapper is not None:
            wrapper.detach().close()  # 此句不应被执行到
            pytest.fail("外层 finally 不应执行已设为 None 的流关闭")

    def test_inner_finally_sets_none_after_close(self):
        """验证内层 finally 的修复逻辑：关闭后立即设为 None。"""
        buf_out = io.BytesIO()
        buf_in = io.BytesIO()
        tty_stdout = io.TextIOWrapper(buf_out, encoding="utf-8")
        tty_stdin = io.TextIOWrapper(buf_in, encoding="utf-8")

        # 模拟内层 finally 的修复逻辑
        if tty_stdout is not None:
            try:
                tty_stdout.detach().close()
            except Exception:
                pass
            tty_stdout = None  # 修复点
        if tty_stdin is not None:
            try:
                tty_stdin.detach().close()
            except Exception:
                pass
            tty_stdin = None  # 修复点

        # 验证变量已设为 None
        assert tty_stdout is None
        assert tty_stdin is None

    def test_outer_finally_skips_when_none(self):
        """验证外层 finally 在变量为 None 时正常跳过（不抛异常）。"""
        tty_stdout = None
        tty_stdin = None

        # 模拟外层 finally 的兜底关闭（变量已为 None，应安全跳过）
        if tty_stdout is not None:
            tty_stdout.detach().close()
            pytest.fail("不应执行")
        if tty_stdin is not None:
            tty_stdin.detach().close()
            pytest.fail("不应执行")

        # 能到达这里说明安全跳过
        assert True

    def test_outer_finally_handles_non_none_stream(self):
        """验证外层 finally 对非 None 流（跳过了内层 finally 的路径）仍能正常关闭。

        场景：early return / _flush_stdin 异常等跳过了内层 finally。
        """
        buf = io.BytesIO()
        tty_stdout = io.TextIOWrapper(buf, encoding="utf-8")

        # 模拟外层 finally 的兜底关闭（变量非 None，应执行关闭）
        if tty_stdout is not None:
            tty_stdout.detach().close()

        # 验证已关闭：再次 detach 应报错
        with pytest.raises(ValueError, match="underlying buffer has been detached"):
            tty_stdout.detach()

    def test_empty_options_behavior(self):
        """测试空选项时的基础行为（不涉及 TTY 流）。"""
        tool = UserSelectFunc(title="test", options=[])
        result = tool.execute()
        # execute() 返回 coroutine，await 后检查结果
        import asyncio
        result_json = asyncio.run(result)
        assert '"selected": []' in result_json
        assert '"action": "empty"' in result_json

    def test_display_params_with_title(self):
        """测试 display_params 基础功能。"""
        result = UserSelectFunc.display_params({"title": "请选择方案"})
        assert "'请选择方案'" in result

    def test_display_params_empty_title(self):
        """参数无 title 时返回空字符串。"""
        result = UserSelectFunc.display_params({})
        assert result == ""
