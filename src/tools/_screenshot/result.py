"""截图结果类型与异常。"""

from __future__ import annotations

from dataclasses import dataclass


class ScreenshotError(RuntimeError):
    """截图失败（无窗口 / 权限不足 / 平台工具缺失等）。

    消息面向大模型，需自带可执行的下一步提示（如「安装 xdotool」）。
    """


class NoWindowError(ScreenshotError):
    """目标进程当前没有可截图的可见窗口。

    单独成类供调用方识别并重试（GUI 程序启动后窗口出现存在延迟）。
    """


@dataclass
class CaptureResult:
    """一次成功截图的结果。

    Attributes:
        path: 图片落盘路径（PNG）。
        width: 图片宽度（像素）。
        height: 图片高度（像素）。
        window_pid: 被截窗口所属的**系统窗口 PID**（Windows 下为 WINPID）。
        window_title: 窗口标题（可能为空串）。
        backend: 执行截图的平台后端名（windows / x11 / macos）。
    """

    path: str
    width: int
    height: int
    window_pid: int
    window_title: str
    backend: str

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "width": self.width,
            "height": self.height,
            "window_pid": self.window_pid,
            "window_title": self.window_title,
            "backend": self.backend,
        }


__all__ = ["CaptureResult", "NoWindowError", "ScreenshotError"]
