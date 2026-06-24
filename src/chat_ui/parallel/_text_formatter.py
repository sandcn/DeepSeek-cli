"""文本格式化工具"""


class TextFormatter:
    """文本格式化工具"""

    @staticmethod
    def format_duration(seconds: float) -> str:
        """
        格式化持续时间为可读格式

        Args:
            seconds: 秒数

        Returns:
            格式化后的时间字符串
        """
        if seconds < 60:
            return f"{seconds:.0f}s"
        elif seconds < 3600:
            minutes = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{minutes}m{secs}s"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            return f"{hours}h{minutes}m"

    @staticmethod
    def format_token_count(tokens: int) -> str:
        if tokens >= 1000:
            return f"{tokens / 1000:.1f}k"
        else:
            return str(tokens)

    @staticmethod
    def format_compact_speed(speed: float) -> str:
        """格式化紧凑速度，始终使用 /s，不反转为 s/tok。"""
        if speed <= 0:
            return "0/s"
        if speed >= 0.1:
            value = f"{speed:.1f}"
        else:
            value = f"{speed:.2f}"
        value = value.rstrip("0").rstrip(".")
        return f"{value}/s"
