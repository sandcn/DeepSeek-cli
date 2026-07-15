"""Widgets 模块 — 控件库。

包含 Widget 基类和标准控件。

## 控件列表

| 控件 | 文件 | 说明 |
|------|------|------|
| Widget | base.py | 交互式控件基类（焦点/键盘/鼠标） |
| Input | input.py | 单行文本输入（光标/遮罩/回车提交） |
| Button | button.py | 按钮（多种样式变体） |
| Select | select.py | 下拉选择（展开/收起/导航） |
| Checkbox | checkbox.py | 复选框（ON/OFF 切换） |
| Menu | menu.py | 菜单（垂直/水平选项列表） |
| Dialog | dialog.py | 对话框（标题+内容+按钮行） |
"""

from tui_framework.widgets.base import TuiComponent, Widget
from tui_framework.widgets.input import Input
from tui_framework.widgets.button import Button
from tui_framework.widgets.select import Select
from tui_framework.widgets.checkbox import Checkbox
from tui_framework.widgets.menu import Menu
from tui_framework.widgets.dialog import Dialog

__all__ = [
    "TuiComponent",
    "Widget",
    "Input",
    "Button",
    "Select",
    "Checkbox",
    "Menu",
    "Dialog",
]
