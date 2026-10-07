"""窗口输入注入的结果类型与异常。"""

from __future__ import annotations

from dataclasses import dataclass, field


class InputError(RuntimeError):
    """输入注入失败（无窗口 / 平台工具缺失 / 系统调用失败等）。

    消息面向大模型，需自带可执行的下一步提示（如「安装 xdotool」）。
    """


class NoWindowError(InputError):
    """目标进程当前没有可接收输入的可见窗口。

    单独成类供调用方识别并重试（GUI 程序启动后窗口出现存在延迟）。
    """


class ActionError(InputError):
    """输入动作参数非法（坐标越界 / 按钮未知 / 键名未知 / 文本为空等）。"""


@dataclass
class InputResult:
    """一次成功输入注入的结果。

    Attributes:
        action: 动作名（click / move / drag / scroll / key / type）。
        backend: 执行注入的平台后端名（windows / x11 / macos）。
        window_pid: 接收输入的窗口所属进程 PID。
        window_title: 窗口标题（可能为空串）。
        detail: 平台与动作相关细节（坐标、按钮、按键序列、投递方式等）。
        window_selector: 本次使用的窗口选择器文本（空 = 主窗口）。
        window_handle: 实际命中的窗口句柄文本（Windows 为 ``0x…`` 十六进制，
            X11 为窗口 id，macOS 为窗口号；空 = 后端未提供）。回传它便于调用方
            核对「选择器实际打到了哪个窗口」——例如 ``#N`` 在弹层关闭后会落到
            另一个窗口，只有句柄 / 标题才能暴露这种漂移。
        window_frame: 实际命中窗口的**截图坐标系**（``{screen_x, screen_y,
            width, height}``，与 ``op=screenshot`` 产物一致）：``screen_x`` /
            ``screen_y`` 是该坐标系左上角的屏幕坐标，``width`` / ``height``
            是可用坐标范围。据此可把输入坐标与截图坐标对齐，并校验点是否
            落在窗口内；``None`` = 后端未提供。
    """

    action: str
    backend: str
    window_pid: int
    window_title: str
    detail: dict = field(default_factory=dict)
    window_selector: str = ""
    window_handle: str = ""
    window_frame: dict | None = None

    def to_dict(self) -> dict:
        payload = {
            "action": self.action,
            "backend": self.backend,
            "window_pid": self.window_pid,
            "window_title": self.window_title,
            "window_selector": self.window_selector or "main",
            "window_handle": self.window_handle,
        }
        payload.update(self.detail)
        if self.window_frame is not None:
            payload["window_frame"] = dict(self.window_frame)
        return payload


__all__ = ["ActionError", "InputError", "InputResult", "NoWindowError"]
