"""Windows 后端枚举显示器前先确保进程 DPI 感知。

背景（真实用例）：第一次 ``op=screenshot screen='primary'`` 返回的显示器
是 1707x960（150% 缩放下的逻辑尺寸），之后同样调用却变成 2560x1440（物理
像素）——因为进程在第一次截图时才被设为 DPI 感知。同一接口前后结果不一致，
整屏截图与窗口坐标（物理像素）无法对齐，坐标换算会错。

修复：``WindowsBackend.list_monitors`` 在枚举前先 ``ensure_process_dpi_aware``。
"""

from __future__ import annotations

from src.tools._screenshot import win as win_module
from src.tools._screenshot.win import WindowsBackend


def test_list_monitors_enables_dpi_awareness_first(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(win_module.winapi, "ensure_process_dpi_aware",
                        lambda: calls.append("dpi") or True)
    monkeypatch.setattr(
        win_module.winapi, "list_monitors",
        lambda: [{"left": 0, "top": 0, "width": 2560, "height": 1440,
                  "primary": True}],
    )

    monitors = WindowsBackend().list_monitors()

    assert calls == ["dpi"]
    assert len(monitors) == 1
    assert (monitors[0].width, monitors[0].height) == (2560, 1440)
    assert monitors[0].primary is True
