"""bash 工具输出截断测试。

覆盖 ``src/tools/bash._truncate_output``：超过 MAX_LINES 行时保留**尾部
最新行**（而非前 N 行）+ 截断标记。

背景：cygwin 环境下 Windows 移植工具大量输出到 stderr，合并后行数叠加，
导致 stdout 明明不足 1000 行却触发截断。修复方向：截断时保留最后 N 行
（尾部最新内容），大模型拿到的是命令最新的输出/错误信息。
"""

from __future__ import annotations

from src.tools.bash import BashFunc


class TestTruncateOutput:
    """_truncate_output：超过行数上限时保留尾部最新行并追加截断标记。"""

    def test_keep_last_lines(self) -> None:
        output = "\n".join(f"line{i}" for i in range(1100))
        result = BashFunc._truncate_output(output)
        lines = result.split("\n")
        assert lines[-1].startswith("...(输出已截断")
        assert lines[0] == "line100"   # 前 100 行被丢弃，保留最后 1000 行
        assert lines[-2] == "line1099"

    def test_exact_max_no_truncate(self) -> None:
        output = "\n".join(f"line{i}" for i in range(1000))
        assert BashFunc._truncate_output(output) == output

    def test_under_max_no_truncate(self) -> None:
        output = "\n".join(f"line{i}" for i in range(999))
        assert BashFunc._truncate_output(output) == output

    def test_error_info_skipped(self) -> None:
        out = "(命令执行超时，已强制终止)"
        assert BashFunc._truncate_output(out) == out

    def test_empty_skipped(self) -> None:
        assert BashFunc._truncate_output("") == ""
        assert BashFunc._truncate_output(None) is None

    def test_custom_max_lines(self) -> None:
        output = "\n".join(f"line{i}" for i in range(20))
        result = BashFunc._truncate_output(output, max_lines=5)
        lines = result.split("\n")
        assert lines[0] == "line15"
        assert lines[-2] == "line19"
        assert "仅展示最后 5 行" in result

    def test_crlf_tail_lines(self) -> None:
        """CRLF 输出截断后保留尾部（cygwin 场景回归）。"""
        output = "\n".join(f"line{i}\r" for i in range(1200))
        result = BashFunc._truncate_output(output)
        lines = result.split("\n")
        assert lines[-1].startswith("...(输出已截断")
        assert lines[0] == "line200\r"
        assert lines[-2] == "line1199\r"

    def test_stdout_plus_stderr_keeps_tail(self) -> None:
        """stdout + stderr 合并后超限：保留尾部最新行（含尾部 stderr）。

        cygwin 场景：stdout 999 行 + stderr 50 行 → 合并 1050 行，
        用户数 stdout 不足 1000，但合并后触发截断——保留最后 1000 行。
        """
        stdout = "\n".join(f"out{i}" for i in range(999))
        stderr = "\n".join(f"warn{i}" for i in range(50))
        merged = stdout.rstrip("\n") + "\n" + stderr
        result = BashFunc._truncate_output(merged.strip())
        lines = result.split("\n")
        assert "输出已截断" in lines[-1]
        assert len(lines) == 1001  # 1000 行 + 截断标记
        assert lines[0] == "out49"     # 丢弃前 49 行 stdout，保留尾部
        assert lines[-2] == "warn49"   # 尾部 stderr 最新行保留

    # ── 尾换行行数统计修正（用户报告：输出未到 1000 行却被截断） ──

    def test_exact_max_with_trailing_newline_no_truncate(self) -> None:
        """恰好 max_lines 行 + 尾换行 → 不截断。

        回归：旧实现 ``output.split('\\n')`` 把尾换行产生的空串多算 1 行，
        恰好 1000 行（如命令输出以换行结尾）被误判 1001 行触发截断。
        """
        output = "\n".join(f"line{i}" for i in range(1000)) + "\n"
        result = BashFunc._truncate_output(output)
        assert result == output, "恰好 max_lines 行（尾换行）不应触发截断"
        assert "输出已截断" not in result

    def test_over_max_with_trailing_newline_keeps_max(self) -> None:
        """max_lines+1 行 + 尾换行 → 截断且内容恰好 max_lines 行（无多余空行）。"""
        output = "\n".join(f"line{i}" for i in range(1001)) + "\n"
        result = BashFunc._truncate_output(output)
        lines = result.split("\n")
        assert lines[-1].startswith("...(输出已截断")
        # 截断标记前内容行 = 1000（不含尾换行空串、不含空行）
        assert lines[-2] == "line1000"
        assert lines[-3] == "line999"
        # 内容首行为 line1（丢弃 line0），无前导空行
        assert lines[0] == "line1"

    def test_over_max_trailing_blank_lines_keeps_max(self) -> None:
        """超过上限且保留行以空行结尾：rstrip 归一化，标记前无多余空行。"""
        output = "\n".join(f"line{i}" for i in range(1002)) + "\n\n"  # 1002 内容行 + 1 逻辑空行
        result = BashFunc._truncate_output(output)
        lines = result.split("\n")
        assert lines[-1].startswith("...(输出已截断")
        # 1002 内容行 + 1 逻辑空行 = 1003 逻辑行 → 保留最后 1000 逻辑行
        # （含空行占位 → 实际内容行 = 999；rstrip 归一化掉尾空行，标记前无空行）
        assert lines[0] == "line3"
        assert lines[-2] == "line1001"
        assert lines[-2] != ""  # 标记前无多余空行（rstrip 生效）
        assert lines[-3] == "line1000"

    def test_long_output_starting_with_paren_truncated(self) -> None:
        """>max_lines 行且以 '(' 开头 → 仍然截断。

        回归：旧实现 ``output.startswith('(')`` 把「命令真实输出以 ( 开头」
        误判为错误提示而跳过截断，超长内容直接给大模型撑爆上下文。
        错误提示本身仅 1 行，远低于上限，不受影响。
        """
        output = "(" + "\n".join(f"err{i}" for i in range(1500))
        result = BashFunc._truncate_output(output)
        assert "输出已截断" in result
        lines = result.split("\n")
        assert lines[-1].startswith("...(输出已截断")
        assert lines[-2] == "err1499"  # 尾部最新行保留
        assert lines[0] == "err500"    # 丢弃前 500 行（含 '(err0'）

    def test_short_paren_started_info_kept(self) -> None:
        """短错误提示以 '(' 开头 → 原样返回（1 行远低于上限，不受影响）。"""
        out = "(命令执行超时，已强制终止)"
        assert BashFunc._truncate_output(out) == out

    def test_progress_bar_cr_not_truncated(self) -> None:
        """裸 \\r 进度条（无 \\n）不拆行、不截断。

        进度条 ``10%\\r20%\\r30%`` 在终端显示为 1 行（覆盖效果），
        按 \\n 拆分的逻辑行数=1，不受 2000 次刷新影响。
        """
        output = "\r".join(f"{i}%" for i in range(2000))
        result = BashFunc._truncate_output(output)
        assert result == output
        assert "输出已截断" not in result

    def test_internal_blank_lines_counted(self) -> None:
        """中间空行计入行数（split 保留空元素），但尾换行不虚增。"""
        # 998 行内容 + 1 个中间空行 + 尾换行 = 999 逻辑行 → 不截断
        parts = [f"line{i}" for i in range(500)]
        parts.append("")  # 中间空行
        parts.extend(f"line{i}" for i in range(500, 998))
        output = "\n".join(parts) + "\n"  # 999 逻辑行 + 尾换行
        result = BashFunc._truncate_output(output)
        assert "输出已截断" not in result
        # 999 行内容 + 空行 = 1000 逻辑行 → 不截断（空行算行，尾换行不算）
        parts2 = [f"line{i}" for i in range(500)]
        parts2.append("")
        parts2.extend(f"line{i}" for i in range(500, 999))
        output2 = "\n".join(parts2) + "\n"  # 999 内容 + 1 空行 = 1000 逻辑行
        result2 = BashFunc._truncate_output(output2)
        assert "输出已截断" not in result2
