"""测试 bash 工具

测试策略
--------
- 每个测试类关注一个概念，每个测试方法覆盖单一行为
- 执行简单安全命令（echo / pwd / true / false）
- 使用 unittest.mock.patch 控制 _HAS_PTY 切换 _run_pty / _run_pipe
- 遵循 Arrange/Act/Assert 模式
"""

from __future__ import annotations

import asyncio
import os
import pytest
from unittest.mock import patch

from src.tools.bash import BashFunc, _strip_ansi


# ═══════════════════════════════════════════════════════════════════════════
# 1. __init__ 参数
# ═══════════════════════════════════════════════════════════════════════════

class TestInit:
    """BashFunc.__init__ 参数赋值。"""

    def test_command_only(self):
        b = BashFunc(command="echo hello")
        assert b.command == "echo hello"
        assert b.cwd is None

    def test_command_and_cwd(self):
        b = BashFunc(command="pwd", cwd="/tmp")
        assert b.command == "pwd"
        assert b.cwd == "/tmp"

    def test_command_timeout_default(self):
        b = BashFunc(command="echo hello")
        assert b.timeout == 300

    def test_command_timeout_custom(self):
        b = BashFunc(command="echo hello", timeout=600)
        assert b.timeout == 600
        b2 = BashFunc(command="echo hello", timeout=120)
        assert b2.timeout == 120


# ═══════════════════════════════════════════════════════════════════════════
# 2. from_args
# ═══════════════════════════════════════════════════════════════════════════

class TestFromArgs:
    """from_args 从字典创建实例。"""

    def test_required_only(self):
        b = BashFunc.from_args({"command": "echo hi"})
        assert b.command == "echo hi"
        assert b.cwd is None

    def test_with_cwd(self):
        b = BashFunc.from_args({"command": "pwd", "cwd": "/tmp"})
        assert b.command == "pwd"
        assert b.cwd == "/tmp"

    def test_extra_params_ignored(self):
        b = BashFunc.from_args({"command": "echo hi", "extra": "ignored"})
        assert b.command == "echo hi"

    def test_missing_command_raises(self):
        with pytest.raises(ValueError, match="缺少必需参数"):
            BashFunc.from_args({})


# ═══════════════════════════════════════════════════════════════════════════
# 3. _check_cwd_or_return
# ═══════════════════════════════════════════════════════════════════════════

class TestCheckCwdOrReturn:
    """_check_cwd_or_return cwd 存在性检查。"""

    def test_cwd_none_returns_none(self):
        result = BashFunc._check_cwd_or_return(None)
        assert result is None

    def test_cwd_exists_returns_none(self, tmp_path):
        result = BashFunc._check_cwd_or_return(str(tmp_path))
        assert result is None

    def test_cwd_not_exists_returns_error(self, tmp_path):
        fake = str(tmp_path / "nonexistent")
        result = BashFunc._check_cwd_or_return(fake)
        assert result is not None
        assert "工作目录不存在" in result

    def test_cwd_is_file_returns_error(self, tmp_path):
        p = tmp_path / "afile.txt"
        p.write_text("")
        result = BashFunc._check_cwd_or_return(str(p))
        assert result is not None
        assert "工作目录不存在" in result


# ═══════════════════════════════════════════════════════════════════════════
# 4. _get_subprocess_env
# ═══════════════════════════════════════════════════════════════════════════

class TestGetSubprocessEnv:
    """_get_subprocess_env 环境变量设置。"""

    def test_python_unbuffered_set(self):
        env = BashFunc._get_subprocess_env()
        assert env.get("PYTHONUNBUFFERED") == "1"

    def test_pager_removed(self):
        env = BashFunc._get_subprocess_env()
        assert "PAGER" not in env

    def test_git_pager_set_to_cat(self):
        env = BashFunc._get_subprocess_env()
        assert env.get("GIT_PAGER") == "cat"

    def test_original_env_preserved(self):
        """原始环境变量被保留。"""
        original = os.environ.copy()
        env = BashFunc._get_subprocess_env()
        for k in original:
            if k not in ("PAGER",):
                assert env[k] == original[k], f"丢失环境变量: {k}"

    def test_pager_removed_if_present(self):
        with patch.dict(os.environ, {"PAGER": "less"}, clear=False):
            env = BashFunc._get_subprocess_env()
            assert "PAGER" not in env


# ═══════════════════════════════════════════════════════════════════════════
# 5. display_params
# ═══════════════════════════════════════════════════════════════════════════

class TestDisplayParams:
    """display_params 参数摘要。"""

    def test_command_shown(self):
        r = BashFunc.display_params({"command": "echo hello"})
        assert "echo hello" in r

    def test_sanitize_newline(self):
        r = BashFunc.display_params({"command": "echo a\nb"})
        assert "/n" in r

    def test_long_command_not_truncated(self):
        """长命令不再被截断，返回完整内容。"""
        long_cmd = "echo " + "a" * 100
        r = BashFunc.display_params({"command": long_cmd}, max_len=20)
        assert "a" * 100 in r

    def test_empty_command(self):
        r = BashFunc.display_params({"command": ""})
        assert "''" in r or r == ""


# ═══════════════════════════════════════════════════════════════════════════
# 6. _strip_ansi
# ═══════════════════════════════════════════════════════════════════════════

class TestStripAnsi:
    """_strip_ansi ANSI 转义码剥离。"""

    def test_no_ansi(self):
        assert _strip_ansi("hello world") == "hello world"

    def test_color_code_removed(self):
        assert _strip_ansi("\x1b[31mred\x1b[0m") == "red"

    def test_bold_code_removed(self):
        assert _strip_ansi("\x1b[1mbold\x1b[0m") == "bold"

    def test_multiple_codes_removed(self):
        result = _strip_ansi("\x1b[32mgreen\x1b[33myellow\x1b[0m")
        assert result == "greenyellow"

    def test_carriage_return_preserved(self):
        """\\r (回车) 保留，用于进度条等行内覆盖效果。分屏由 DECSTBM 保障。"""
        assert _strip_ansi("line\r\n") == "line\r\n"

    def test_trailing_newline_preserved(self):
        """输出行有 \\n 时不应额外添加 \\n（防双重换行）。"""
        assert _strip_ansi("hello\n") == "hello\n"
        assert _strip_ansi("hello world\n") == "hello world\n"

    def test_no_trailing_newline_no_cr(self):
        """输出行无 \\n 且无 \\r 时保持原样（后续追加逻辑自行处理）。"""
        assert _strip_ansi("hello") == "hello"
        assert _strip_ansi("incomplete") == "incomplete"

    def test_trailing_cr_preserved(self):
        """行尾 \\r 保留，用于进度条行内覆盖。"""
        assert _strip_ansi("progress\r") == "progress\r"
        assert _strip_ansi("overwrite\r") == "overwrite\r"

    def test_ansi_and_cr(self):
        result = _strip_ansi("\x1b[K\r\nprogress 50%\r\n")
        assert result == "\r\nprogress 50%\r\n"

    def test_empty_string(self):
        assert _strip_ansi("") == ""

    def test_only_ansi(self):
        assert _strip_ansi("\x1b[0m\x1b[31m") == ""

    def test_complex_escape(self):
        result = _strip_ansi("\x1b[38;5;196m256color\x1b[0m")
        assert result == "256color"

    def test_cursor_movement_removed(self):
        result = _strip_ansi("\x1b[2J\x1b[Hclear")
        assert result == "clear"

    def test_private_mode_set_reset_removed(self):
        """\\x1b[?25l 隐藏光标等带 ? 的私有序列。"""
        assert _strip_ansi("\x1b[?25l") == ""
        assert _strip_ansi("\x1b[?25h") == ""
        assert _strip_ansi("\x1b[?1049h") == ""
        assert _strip_ansi("\x1b[?1049l") == ""

    def test_scroll_region_removed(self):
        """DECSTBM 设滚动区序列。"""
        assert _strip_ansi("\x1b[3;20r") == ""

    def test_non_csi_escape_removed(self):
        """非 CSI 转义序列（\\x1b7、\\x1b8、\\x1bM、\\x1bD 等）。"""
        assert _strip_ansi("\x1b7") == ""   # DECSC 保存光标
        assert _strip_ansi("\x1b8") == ""   # DECRC 恢复光标
        assert _strip_ansi("\x1bM") == ""   # RI 反向换行
        assert _strip_ansi("\x1bD") == ""   # IND 索引

    def test_clear_screen_removed(self):
        """各类清屏序列。"""
        assert _strip_ansi("\x1b[2Jclear") == "clear"
        assert _strip_ansi("\x1b[3Jclear") == "clear"
        assert _strip_ansi("\x1b[1Jclear") == "clear"

    def test_cursor_position_removed(self):
        """光标定位序列。"""
        assert _strip_ansi("\x1b[10;20Hhithere") == "hithere"
        assert _strip_ansi("\x1b[5Ghello") == "hello"

    def test_scroll_up_down_removed(self):
        """滚动序列。"""
        assert _strip_ansi("\x1b[3S") == ""   # SU 上滚
        assert _strip_ansi("\x1b[3T") == ""   # SD 下滚

    def test_insert_delete_lines_removed(self):
        """插入/删除行序列（分屏破坏性最大）。"""
        assert _strip_ansi("\x1b[2L") == ""
        assert _strip_ansi("\x1b[2M") == ""

    def test_erase_in_line_removed(self):
        """行内擦除序列。"""
        assert _strip_ansi("\x1b[K") == ""
        assert _strip_ansi("\x1b[0K") == ""
        assert _strip_ansi("\x1b[1K") == ""
        assert _strip_ansi("\x1b[2K") == ""

    def test_color_code_preserved_content(self):
        """颜色序列剥离后正常保留文字。"""
        result = _strip_ansi("\x1b[38;5;196mred\x1b[0m")
        assert result == "red"

    def test_mixed_dangerous_sequences(self):
        """混合危险序列：清屏+光标归位。"""
        result = _strip_ansi("\x1b[2J\x1b[H\x1b[?25lloading...\x1b[?25h")
        assert result == "loading..."

    def test_non_csi_with_intermediate_byte(self):
        """非 CSI 序列含中间字节（如 \\x1b(B 字符集选择）。"""
        assert _strip_ansi("\x1b(B") == ""
        assert _strip_ansi("\x1b)A") == ""
        assert _strip_ansi("\x1b%G") == ""

    def test_non_csi_intermediate_with_text(self):
        """含中间字节的转义序列剥离后正常保留两侧文字。"""
        result = _strip_ansi("before\x1b(Bafter")
        assert result == "beforeafter"
        result = _strip_ansi("a\x1b%Gb")
        assert result == "ab"

    def test_osc_bel_terminated(self):
        """OSC 序列（\\x07 BEL 终止）：设终端标题等。"""
        result = _strip_ansi("\x1b]0;My Title\x07")
        assert result == ""
        result = _strip_ansi("text\x1b]0;title\x07more")
        assert result == "textmore"

    def test_osc_st_terminated(self):
        """OSC 序列（\\x1b\\\\ ST 终止）。"""
        result = _strip_ansi("\x1b]0;My Title\x1b\\")
        assert result == ""

    def test_osc_hyperlink(self):
        """OSC 8 超链接：打开和关闭标签都应剥离，文字保留。"""
        result = _strip_ansi("\x1b]8;;https://example.com\x1b\\Click here\x1b]8;;\x1b\\")
        assert result == "Click here"
        # 多个超链接
        result = _strip_ansi("\x1b]8;;url1\x1b\\link1\x1b]8;;\x1b\\ \x1b]8;;url2\x1b\\link2\x1b]8;;\x1b\\")
        assert result == "link1 link2"

    def test_dcs_sequence(self):
        """DCS 序列（Device Control String）。"""
        result = _strip_ansi("\x1bP0;1;2q\x1b\\")
        assert result == ""

    def test_sos_sequence(self):
        """SOS 序列（Start of String）。"""
        result = _strip_ansi("\x1bXsome data\x1b\\")
        assert result == ""

    def test_pm_sequence(self):
        """PM 序列（Privacy Message）。"""
        result = _strip_ansi("\x1b^private\x1b\\")
        assert result == ""

    def test_apc_sequence(self):
        """APC 序列（Application Program Command）。"""
        result = _strip_ansi("\x1b_app data\x1b\\")
        assert result == ""

    def test_osc_no_terminator_fallback(self):
        """OSC 序列无终止符时回退为截断非 CSI 序列（\\x1b] 被剥离）。"""
        result = _strip_ansi("\x1b]0;incomplete")
        # \x1b] 被非 CSI 分支匹配（] 是有效终结字节）
        assert "\x1b" not in result
        assert "incomplete" in result

    def test_intermediate_byte_does_not_interfere_with_csi(self):
        """中间字节分支不应干扰 CSI 序列的正常匹配。"""
        result = _strip_ansi("\x1b[31mred\x1b[0m")
        assert result == "red"
        result = _strip_ansi("\x1b[2Jclear")
        assert result == "clear"


# ═══════════════════════════════════════════════════════════════════════════
# 7. execute — PIPE 模式（_HAS_PTY=False）
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestExecutePipeMode:
    """execute 在 PIPE 模式下执行简单命令。"""

    @patch("src.tools.bash._HAS_PTY", False)
    async def test_echo(self):
        b = BashFunc(command="echo hello")
        result = await b.execute()
        assert result == "hello"

    @patch("src.tools.bash._HAS_PTY", False)
    async def test_pwd(self, tmp_path):
        b = BashFunc(command="pwd", cwd=str(tmp_path))
        result = await b.execute()
        assert result == str(tmp_path)

    @patch("src.tools.bash._HAS_PTY", False)
    async def test_stderr_merged(self):
        """stderr 与 stdout 合并返回。"""
        b = BashFunc(command="echo out && echo err >&2")
        result = await b.execute()
        assert "out" in result
        assert "err" in result

    @patch("src.tools.bash._HAS_PTY", False)
    async def test_cwd_not_exists(self, tmp_path):
        fake = str(tmp_path / "nonexistent")
        b = BashFunc(command="echo hi", cwd=fake)
        result = await b.execute()
        assert "工作目录不存在" in result

    @patch("src.tools.bash._HAS_PTY", False)
    async def test_no_output_returns_placeholder(self):
        b = BashFunc(command="true")
        result = await b.execute()
        assert result == "(无输出)"

    @patch("src.tools.bash._HAS_PTY", False)
    async def test_nonzero_exit_with_output(self):
        b = BashFunc(command="echo hello && false")
        result = await b.execute()
        # stderr may or may not appear, but stdout "hello" should
        assert "hello" in result

    @patch("src.tools.bash._HAS_PTY", False)
    async def test_nonzero_exit_no_output(self):
        b = BashFunc(command="false")
        result = await b.execute()
        # false has no output, so should return "(无输出)"
        assert result == "(无输出)"

    @patch("src.tools.bash._HAS_PTY", False)
    async def test_multiline_output(self):
        b = BashFunc(command="echo -e 'line1\nline2'")
        result = await b.execute()
        assert "line1" in result
        assert "line2" in result

    @patch("src.tools.bash.print_to_terminal")
    @patch("src.tools.bash._HAS_PTY", False)
    async def test_execute_prints_command_to_terminal(self, mock_print):
        """execute() 调用时应在终端输出命令（$ cmd 格式）。"""
        b = BashFunc(command="echo hello")
        result = await b.execute()
        # 返回值正常
        assert result == "hello"
        # print_to_terminal 被调用，且参数包含命令内容
        mock_print.assert_awaited()
        call_args = mock_print.await_args_list
        any_match = any(
            "echo hello" in str(args) for args in call_args
        )
        assert any_match, f"print_to_terminal 应包含命令 'echo hello'，实际调用: {call_args}"


# ═══════════════════════════════════════════════════════════════════════════
# 8. execute — PTY 模式（_HAS_PTY=True）
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestExecutePtyMode:
    """execute 在 PTY 模式下执行简单命令。"""

    @patch("src.tools.bash._HAS_PTY", True)
    async def test_echo(self):
        b = BashFunc(command="echo hello")
        result = await b.execute()
        assert result == "hello"

    @patch("src.tools.bash._HAS_PTY", True)
    async def test_pwd(self, tmp_path):
        b = BashFunc(command="pwd", cwd=str(tmp_path))
        result = await b.execute()
        assert result == str(tmp_path)

    @patch("src.tools.bash._HAS_PTY", True)
    async def test_cwd_not_exists(self, tmp_path):
        fake = str(tmp_path / "nonexistent")
        b = BashFunc(command="echo hi", cwd=fake)
        result = await b.execute()
        assert "工作目录不存在" in result

    @patch("src.tools.bash._HAS_PTY", True)
    async def test_no_output_returns_placeholder(self):
        b = BashFunc(command="true")
        result = await b.execute()
        assert result == "(无输出)"

    @patch("src.tools.bash.print_to_terminal")
    @patch("src.tools.bash._HAS_PTY", True)
    async def test_execute_prints_command_to_terminal(self, mock_print):
        """execute() 调用时应在终端输出命令（$ cmd 格式）— PTY 模式。"""
        b = BashFunc(command="echo hello")
        result = await b.execute()
        assert result == "hello"
        mock_print.assert_awaited()
        call_args = mock_print.await_args_list
        any_match = any(
            "echo hello" in str(args) for args in call_args
        )
        assert any_match, f"print_to_terminal 应包含命令 'echo hello'，实际调用: {call_args}"


# ═══════════════════════════════════════════════════════════════════════════
# 9. display
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestDisplay:
    """BashFunc.display — 显示加执行。"""

    @patch("src.tools.bash._HAS_PTY", False)
    async def test_display_returns_result(self):
        b = BashFunc(command="echo hello")
        result = await b.display()
        assert result == "hello"

    @patch("src.tools.bash._HAS_PTY", False)
    async def test_display_cwd_error(self, tmp_path):
        fake = str(tmp_path / "nonexistent")
        b = BashFunc(command="echo hi", cwd=fake)
        result = await b.display()
        assert "工作目录不存在" in result

    @patch("src.tools.bash._HAS_PTY", False)
    async def test_display_no_output(self):
        b = BashFunc(command="true")
        result = await b.display()
        assert result == "(无输出)"


# ═══════════════════════════════════════════════════════════════════════════
# 10. web_display
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestWebDisplay:
    """BashFunc.web_display — WebUI 显示。"""

    @patch("src.tools.bash._HAS_PTY", False)
    async def test_web_display_echo(self):
        b = BashFunc(command="echo hello")
        result = await b.web_display()
        assert result == "hello"

    @patch("src.tools.bash._HAS_PTY", False)
    async def test_web_display_cwd_error(self, tmp_path):
        fake = str(tmp_path / "nonexistent")
        b = BashFunc(command="echo hi", cwd=fake)
        result = await b.web_display()
        assert "工作目录不存在" in result


# ═══════════════════════════════════════════════════════════════════════════
# 11. _run_pipe / _run_pty 直接测试（低层行为）
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestRunPipe:
    """_run_pipe 直接测试。"""

    async def test_basic_command(self):
        b = BashFunc(command="echo hello")
        result = await b._run_pipe(show_command=False, show_output=False)
        assert result == "hello"

    async def test_no_output(self):
        b = BashFunc(command="true")
        result = await b._run_pipe(show_command=False, show_output=False)
        assert result == "(无输出)"

    async def test_stderr_in_output(self):
        b = BashFunc(command="echo out && echo err >&2")
        result = await b._run_pipe(show_command=False, show_output=False)
        assert "out" in result
        assert "err" in result


@pytest.mark.asyncio
class TestRunPty:
    """_run_pty 直接测试（需 PTY 可用）。"""

    async def test_basic_command(self):
        try:
            import pty  # noqa: F401
        except ImportError:
            pytest.skip("PTY not available on this platform")

        b = BashFunc(command="echo hello")
        result = await b._run_pty(show_command=False, show_output=False)
        # PTY 输出可能带换行符，strip 后应为 "hello"
        assert result.strip() == "hello"

    async def test_no_output(self):
        try:
            import pty  # noqa: F401
        except ImportError:
            pytest.skip("PTY not available on this platform")

        b = BashFunc(command="true")
        result = await b._run_pty(show_command=False, show_output=False)
        assert result == "(无输出)"


# ═══════════════════════════════════════════════════════════════════════════
# 12. _is_pty_available
# ═══════════════════════════════════════════════════════════════════════════

class TestIsPtyAvailable:
    """_is_pty_available 检测 PTY 可用性。"""

    def test_returns_bool(self):
        result = BashFunc._is_pty_available()
        assert isinstance(result, bool)


# ═══════════════════════════════════════════════════════════════════════════
# 13. 集成测试：PIPE + PTY 双路径等价性
# ═══════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════
# 14. ESC 中断测试
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestInterrupt:
    """ESC 中断 bash 执行（回归测试）。"""

    @patch("src.tools.bash._HAS_PTY", False)
    async def test_pipe_interrupt_long_command(self):
        """PIPE 模式下执行长命令，ESC 中断后返回 '(命令已被中断)'。"""
        from src.api.interrupt_async import request_interrupt_async, reset_interrupt_async

        reset_interrupt_async()  # 确保初始状态干净

        b = BashFunc(command="echo start && sleep 5 && echo end")

        async def _interrupt_after_delay():
            await asyncio.sleep(0.3)  # 等命令启动
            request_interrupt_async()

        results = await asyncio.gather(
            b._run_pipe(show_command=False, show_output=False),
            _interrupt_after_delay(),
        )

        reset_interrupt_async()  # 清理中断信号

        assert results[0] == "(命令已被中断)"

    @patch("src.tools.bash._HAS_PTY", False)
    async def test_pipe_interrupt_immediate(self):
        """PIPE 模式下，在命令执行前就请求中断，应同样返回中断消息。"""
        from src.api.interrupt_async import request_interrupt_async, reset_interrupt_async

        reset_interrupt_async()

        b = BashFunc(command="echo should_not_run")

        # 在命令启动前就设置中断
        request_interrupt_async()

        result = await b._run_pipe(show_command=False, show_output=False)

        reset_interrupt_async()

        assert result == "(命令已被中断)"

    @patch("src.tools.bash._HAS_PTY", False)
    async def test_pipe_interrupt_no_side_effect_on_next_command(self):
        """中断后清除信号，下一个命令应正常执行。"""
        from src.api.interrupt_async import request_interrupt_async, reset_interrupt_async

        # 先中断
        reset_interrupt_async()
        b1 = BashFunc(command="sleep 3")
        async def _interrupt():
            await asyncio.sleep(0.2)
            request_interrupt_async()
        result1 = (await asyncio.gather(b1._run_pipe(), _interrupt()))[0]
        assert result1 == "(命令已被中断)"

        # 清除中断信号后再执行
        reset_interrupt_async()
        b2 = BashFunc(command="echo after_cleanup")
        result2 = await b2._run_pipe()
        assert result2 == "after_cleanup"

    @patch("src.tools.bash._HAS_PTY", True)
    async def test_pty_interrupt_long_command(self):
        """PTY 模式下执行长命令，ESC 中断后返回 '(命令已被中断)'。"""
        try:
            import pty  # noqa: F401
        except ImportError:
            pytest.skip("PTY not available on this platform")

        from src.api.interrupt_async import request_interrupt_async, reset_interrupt_async

        reset_interrupt_async()

        b = BashFunc(command="echo start && sleep 5 && echo end")

        async def _interrupt_after_delay():
            await asyncio.sleep(0.3)
            request_interrupt_async()

        results = await asyncio.gather(
            b._run_pty(show_command=False, show_output=False),
            _interrupt_after_delay(),
        )

        reset_interrupt_async()

        assert results[0] == "(命令已被中断)"


@pytest.mark.asyncio
class TestModeEquivalence:
    """PIPE 和 PTY 模式对简单命令应返回一致结果。"""

    @patch("src.tools.bash._HAS_PTY", False)
    async def test_pipe_simple_output(self):
        b = BashFunc(command="echo hello")
        r = await b.execute()
        assert r == "hello"

    @patch("src.tools.bash._HAS_PTY", True)
    async def test_pty_simple_output(self):
        b = BashFunc(command="echo hello")
        r = await b.execute()
        assert r == "hello"


# ═══════════════════════════════════════════════════════════════════════════
# 15. _truncate_output 行数截断测试
# ═══════════════════════════════════════════════════════════════════════════

class TestTruncateOutput:
    """BashFunc._truncate_output 输出行数截断。"""

    def test_below_limit_not_truncated(self):
        """输出行数不超过 1000 行时不截断。"""
        output = '\n'.join([f"line{i}" for i in range(10)])
        result = BashFunc._truncate_output(output)
        assert result == output
        assert "(输出已截断" not in result

    def test_exactly_limit_not_truncated(self):
        """输出正好 1000 行时不截断。"""
        output = '\n'.join([f"line{i}" for i in range(1000)])
        result = BashFunc._truncate_output(output)
        assert result == output

    def test_over_limit_truncated(self):
        """输出超过 1000 行时截断，保留前 1000 行并添加截断标记。"""
        lines = [f"line{i}" for i in range(1005)]
        output = '\n'.join(lines)
        result = BashFunc._truncate_output(output)
        result_lines = result.split('\n')
        # 前 1000 行保留
        assert result_lines[0] == "line0"
        assert result_lines[999] == "line999"
        # 第 1001 行是截断标记（索引 1000）
        assert "输出已截断" in result_lines[1000]
        # 总行数 = 1000 + 1(截断标记) = 1001
        assert len(result_lines) == 1001

    def test_custom_max_lines(self):
        """支持自定义 max_lines 参数。"""
        output = '\n'.join([f"line{i}" for i in range(20)])
        result = BashFunc._truncate_output(output, max_lines=5)
        result_lines = result.split('\n')
        assert result_lines[0] == "line0"
        assert result_lines[4] == "line4"
        assert "输出已截断" in result_lines[5]
        assert len(result_lines) == 6

    def test_error_message_not_truncated(self):
        """以 '(' 开头的错误/提示信息不截断。"""
        output = (
            "(无输出)"
        )
        # 模拟超长但以 '(' 开头的输出
        long_error = '(' + 'x' * 10000 + ')'
        result = BashFunc._truncate_output(long_error)
        assert result == long_error

    def test_empty_string(self):
        """空字符串返回空字符串。"""
        assert BashFunc._truncate_output("") == ""

    def test_single_line_below_limit(self):
        """单行输出不超过限制时原样返回。"""
        result = BashFunc._truncate_output("hello world")
        assert result == "hello world"
        assert "(输出已截断" not in result

    def test_single_line_over_limit_is_not_truncated(self):
        """单行输出（无换行符）即使超过 1000 行也不截断（因为没有换行符来分割）。"""
        # 注意：没有换行符时 split('\n') 返回 1 个元素，所以不会触发截断
        # 这实际上是期望行为——单行超大文本不会被错误截断
        output = "a" * 10000
        result = BashFunc._truncate_output(output)
        assert result == output

    def test_empty_lines_counted(self):
        """空行应被计入行数，触发截断。"""
        # 999 行有效内容 + 2 空行 = 1001 行，应触发截断
        lines = [f"line{i}" for i in range(999)] + ['', '']
        output = '\n'.join(lines)
        result = BashFunc._truncate_output(output)
        result_lines = result.split('\n')
        # 前 999 行有效 + 第 1000 行是空行 = 截断线（max_lines=1000）
        assert result_lines[0] == "line0"
        assert result_lines[998] == "line998"
        assert result_lines[999] == ""          # 第 1000 行是空行
        assert "输出已截断" in result_lines[1000]  # 第 1001 行是截断标记
        assert len(result_lines) == 1001


# ═══════════════════════════════════════════════════════════════════════════
# 16. _has_dangerous_command — 危险命令拦截
# ═══════════════════════════════════════════════════════════════════════════

from src.tools.bash import _has_dangerous_command


class TestDangerousCommands:
    """_has_dangerous_command 危险命令运行时拦截测试

    覆盖以下新增模式（步骤 1 P0 安全修复）：
      - rm -rf /* 通配符根路径
      - su/doas/pkexec 提权
      - chmod 777 权限开放
    """

    def test_rm_rf_slash(self):
        """rm -rf / 被拦截"""
        assert _has_dangerous_command("rm -rf /") is not None

    def test_rm_rf_slash_star(self):
        """rm -rf /* 被拦截"""
        result = _has_dangerous_command("rm -rf /*")
        assert result is not None
        assert "通配符" in result or "根目录" in result

    def test_rm_recursive_slash_star(self):
        """rm --recursive /* 被拦截"""
        assert _has_dangerous_command("rm --recursive /*") is not None

    def test_rm_rf_home_star(self):
        """rm -rf /home/* 不匹配（不是根目录通配符，但 rm -rf / 模式匹配 /home）"""
        # rm -rf /home/x 中的 / 会被 \brm\s+(-rf|--recursive)\s+/ 匹配
        result = _has_dangerous_command("rm -rf /home/user")
        assert result is not None  # 包含 / 的路径被拦截

    def test_su_command_blocked(self):
        """su 提权命令被拦截"""
        assert _has_dangerous_command("su root") is not None

    def test_su_standalone_blocked(self):
        """单独 su 命令被拦截"""
        assert _has_dangerous_command("su") is not None

    def test_doas_blocked(self):
        """doas 提权命令被拦截"""
        assert _has_dangerous_command("doas apt install vim") is not None

    def test_pkexec_blocked(self):
        """pkexec 提权命令被拦截"""
        assert _has_dangerous_command("pkexec nano /etc/hosts") is not None

    def test_chmod_777_blocked(self):
        """chmod 777 权限开放被拦截"""
        assert _has_dangerous_command("chmod 777 /tmp/script.sh") is not None

    def test_chmod_777_directory_blocked(self):
        """chmod -R 777 目录被拦截"""
        assert _has_dangerous_command("chmod -R 777 /var/www") is not None

    def test_chmod_644_allowed(self):
        """chmod 644 不被拦截（非 777）"""
        assert _has_dangerous_command("chmod 644 file.txt") is None

    def test_sudo_blocked(self):
        """sudo 被拦截"""
        assert _has_dangerous_command("sudo rm /tmp/test") is not None

    def test_mkfs_blocked(self):
        """mkfs 被拦截"""
        assert _has_dangerous_command("mkfs.ext4 /dev/sda1") is not None

    def test_dd_blocked(self):
        """dd 被拦截"""
        assert _has_dangerous_command("dd if=/dev/zero of=/dev/sda") is not None

    def test_chown_blocked(self):
        """chown 被拦截"""
        assert _has_dangerous_command("chown root:root /etc/passwd") is not None

    def test_normal_command_allowed(self):
        """正常命令不被拦截"""
        assert _has_dangerous_command("echo hello") is None
        assert _has_dangerous_command("ls -la") is None
        assert _has_dangerous_command("rm file.txt") is None  # 非递归删除单个文件
        assert _has_dangerous_command("git status") is None

    def test_sudo_not_false_positive_in_words(self):
        """含 'sudo' 字符串的正常命令不应被误拦截

        例如 'pseudo' 不应被拦截，因为 \\bsudo\\b 使用单词边界。
        """
        assert _has_dangerous_command("echo pseudo") is None
        assert _has_dangerous_command("echo sudon't") is None

    def test_chmod_not_false_positive_without_777(self):
        """不含 777 的 chmod 命令不被拦截"""
        assert _has_dangerous_command("chmod +x script.sh") is None
        assert _has_dangerous_command("chmod 755 file.txt") is None


# ═══════════════════════════════════════════════════════════════════════════
# 17. _kill_process_tree / _collect_descendants — 进程树杀死
# ═══════════════════════════════════════════════════════════════════════════

from src.tools.bash import _kill_process_tree, _collect_descendants


class TestKillProcessTree:
    """_kill_process_tree 和 _collect_descendants 进程树杀死测试。

    测试策略
    --------
    - 使用 unittest.mock.patch 模拟 os.kill / os.killpg / os.listdir
    - 非 Linux 平台：验证仅调用 killpg，跳过 /proc 扫描
    - Linux 平台：验证 killpg + /proc 扫描补杀后代
    - 异常路径：OSError 被捕获不崩溃
    """

    @patch("src.tools.bash.os.killpg")
    @patch("src.tools.bash.sys.platform", "android")
    def test_non_linux_skip_proc(self, mock_killpg):
        """非 Linux 平台（非 'linux' 前缀）跳过 /proc 扫描，仅调用 killpg。"""
        _kill_process_tree(12345)
        mock_killpg.assert_called_once_with(12345, 9)  # SIGKILL = 9

    @patch("src.tools.bash.os.killpg")
    @patch("src.tools.bash.sys.platform", "linux")
    def test_linux_killpg_and_descendants(self, mock_killpg):
        """Linux 平台先 killpg 再补杀后代。"""
        # 让 _collect_descendants 往 result 列表填充 2 个后代PID
        with (
            patch("src.tools.bash._collect_descendants") as mock_desc,
            patch("src.tools.bash.os.kill") as mock_kill,
        ):
            mock_desc.side_effect = lambda _pid, result: result.extend([100, 101])

            _kill_process_tree(12345)

            # killpg 应被调用（杀进程组）
            mock_killpg.assert_called_once_with(12345, 9)
            # 后代补杀：两个后代各被 kill 一次
            assert mock_kill.call_count == 2
            mock_kill.assert_any_call(100, 9)
            mock_kill.assert_any_call(101, 9)

    @patch("src.tools.bash.os.killpg", side_effect=OSError("ESRCH"))
    @patch("src.tools.bash.sys.platform", "android")
    def test_killpg_oserror_caught(self, mock_killpg):
        """killpg 抛出 OSError 时被捕获，不崩溃。"""
        _kill_process_tree(99999)  # 不应抛出异常

    @patch("src.tools.bash.os.kill", side_effect=OSError("EPERM"))
    @patch("src.tools.bash.os.killpg")
    @patch("src.tools.bash.sys.platform", "linux")
    def test_descendant_kill_oserror_caught(self, mock_killpg, mock_kill):
        """/proc 后代补杀时 OSError 被捕获，不崩溃。"""
        # mock _collect_descendants 确保触发 os.kill 分支
        with patch("src.tools.bash._collect_descendants") as mock_desc:
            mock_desc.side_effect = lambda _pid, result: result.extend([200])
            _kill_process_tree(12345)
            # os.kill 应被调用（mocked side_effect=OSError，不应崩溃）
            assert mock_kill.call_count >= 1

    @patch("src.tools.bash.os.listdir", return_value=["1", "2", "3"])
    @patch("src.tools.bash.sys.platform", "linux")
    def test_collect_descendants_proc_error_caught(self, mock_listdir):
        """/proc 读取因 OSError 失败时不崩溃。"""
        # mock open 来模拟 /proc/1/status 等读取异常
        import builtins
        original_open = builtins.open

        def mock_open_side_effect(*args, **kwargs):
            if '/proc/' in args[0]:
                raise IOError("Permission denied")
            return original_open(*args, **kwargs)

        with patch("builtins.open", side_effect=mock_open_side_effect):
            result: list[int] = []
            _collect_descendants(1, result)
            assert result == []  # 异常被捕获，结果为空列表
