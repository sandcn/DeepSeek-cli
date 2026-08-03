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
