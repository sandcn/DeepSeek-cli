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
