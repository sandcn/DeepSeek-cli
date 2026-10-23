"""bash_opt 工具 JSON Schema — 纯数据定义（与执行逻辑分离）。

``bash_opt.py`` 只保留工具执行逻辑，``to_tool_schema`` 退化为一行委托；
本模块无副作用，``INPUT_OPS``（GUI 输入类 op 名单）的唯一真源在
``src/tools/_window_input/action.py``，此处延迟导入以避免导入环。
"""

from __future__ import annotations

from typing import Any


def _input_ops() -> tuple:
    from ._window_input.action import INPUT_OPS

    return INPUT_OPS


def build_bash_opt_schema() -> dict[str, Any]:
    """构建 bash_opt 的 OpenAI function schema（每次调用返回全新 dict）。"""
    INPUT_OPS = _input_ops()
    return {
        "type": "function",
        "function": {
            "name": "bash_opt",
            "description": (
                "按 task_id 操作后台 bash 任务或 GUI；op：read/wait/kill/stdin/"
                "close_stdin/keys/screenshot/windows/window/elements/wait_window/"
                "clipboard/locate/pixel/annotate/record/replay/sequence/move/hover/"
                "click/drag/scroll/key/type/release。read 默认返回最后 1000 行，"
                "wait 超时返回 timeout=true。"
                "例：bash_opt({\"task_id\": \"bg-abc123\", \"op\": \"read\", "
                "\"max_lines\": 100})；截图：bash_opt({\"op\": \"screenshot\", "
                "\"window\": \"mintty\", \"shot\": \"shot.png\"})"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "后台任务 ID（bg-xxx；部分 op 可省略，如 bg-abc123）",
                    },
                    "op": {
                        "type": "string",
                        "enum": ["read", "wait", "kill", "stdin",
                                 "close_stdin", "keys",
                                 "screenshot", "windows", "window",
                                 "elements", "wait_window", "clipboard",
                                 "locate", "pixel", "annotate",
                                 "record", "replay", "sequence",
                                 *INPUT_OPS],
                        "description": "要执行的操作（见功能描述）",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "超时秒数（wait 默认 300，0 不限）",
                    },
                    "allow_hidden": {
                        "type": "boolean",
                        "description": "隐藏窗口是否也返回",
                    },
                    "text": {
                        "type": "string",
                        "description": "输入文本",
                    },
                    "newline": {
                        "type": "boolean",
                        "description": "末尾是否追加换行",
                    },
                    "encoding": {
                        "type": "string",
                        "description": "文本编码（默认 utf-8）",
                    },
                    "max_lines": {
                        "type": "integer",
                        "description": "最多返回行数（默认 1000，0 不限）",
                    },
                    "key": {
                        "type": "string",
                        "description": "按键名",
                    },
                    "window": {
                        "type": "string",
                        "description": "窗口选择器",
                    },
                    "grid": {
                        "type": ["number", "boolean"],
                        "description": "坐标参考线间距",
                    },
                    "screen": {
                        "type": ["boolean", "string", "number"],
                        "description": "整屏或多显示器",
                    },
                    "margin": {
                        "type": "number",
                        "description": "控件区域外扩像素",
                    },
                    "shot": {
                        "type": ["string", "boolean"],
                        "description": "自动截图路径",
                    },
                    "settle": {
                        "type": "number",
                        "description": "注入后等待秒数",
                    },
                    "window_action": {
                        "type": "string",
                        "enum": ["activate", "maximize", "minimize", "restore",
                                 "close", "move", "resize", "fit",
                                 "always_on_top", "not_on_top",
                                 "get_geometry", "save_geometry",
                                 "restore_geometry"],
                        "description": "窗口控制动作",
                    },
                    "width": {
                        "type": "number",
                        "description": "窗口宽度",
                    },
                    "height": {
                        "type": "number",
                        "description": "窗口高度",
                    },
                    "path": {
                        "type": "string",
                        "description": "文件路径",
                    },
                    "crop": {
                        "type": "string",
                        "description": "截取区域",
                    },
                    "x": {
                        "type": ["number", "string"],
                        "description": "窗口内 X 坐标",
                    },
                    "y": {
                        "type": ["number", "string"],
                        "description": "窗口内 Y 坐标",
                    },
                    "to_x": {
                        "type": ["number", "string"],
                        "description": "拖动终点 X",
                    },
                    "to_y": {
                        "type": ["number", "string"],
                        "description": "拖动终点 Y",
                    },
                    "from_x": {
                        "type": ["number", "string"],
                        "description": "拖动起点 X",
                    },
                    "from_y": {
                        "type": ["number", "string"],
                        "description": "拖动起点 Y",
                    },
                    "button": {
                        "type": "string",
                        "enum": ["left", "right", "middle"],
                        "description": "鼠标按钮",
                    },
                    "count": {
                        "type": "number",
                        "description": "点击次数",
                    },
                    "hold": {
                        "type": "number",
                        "description": "长按秒数",
                    },
                    "interval": {
                        "type": "number",
                        "description": "间隔秒数",
                    },
                    "dwell": {
                        "type": "number",
                        "description": "悬停停留秒数",
                    },
                    "dx": {
                        "type": "number",
                        "description": "水平相对偏移",
                    },
                    "dy": {
                        "type": "number",
                        "description": "垂直相对偏移",
                    },
                    "modifiers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "按住的修饰键",
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down", "left", "right"],
                        "description": "滚动方向",
                    },
                    "amount": {
                        "type": "number",
                        "description": "滚动量",
                    },
                    "duration": {
                        "type": "number",
                        "description": "动作耗时秒数",
                    },
                    "steps": {
                        "type": "number",
                        "description": "插值步数",
                    },
                    "method": {
                        "type": "string",
                        "enum": ["auto", "sendinput", "message"],
                        "description": "输入注入方式",
                    },
                    "phase": {
                        "type": "string",
                        "enum": ["press", "down", "up"],
                        "description": "按键阶段",
                    },
                    "repeat": {
                        "type": "number",
                        "description": "连按次数",
                    },
                    "relative_event": {
                        "type": "boolean",
                        "description": "是否发送纯相对位移",
                    },
                    "hold_keys": {
                        "type": ["string", "array"],
                        "items": {"type": "string"},
                        "description": "动作期间按住的键",
                    },
                    "keys": {
                        "type": ["string", "array"],
                        "items": {"type": "string"},
                        "description": "要释放的键",
                    },
                    "buttons": {
                        "type": ["string", "array"],
                        "items": {"type": "string"},
                        "description": "要释放的鼠标按钮",
                    },
                    "actions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "op": {
                                    "type": "string",
                                    "description": "步骤类型",
                                },
                            },
                        },
                        "description": "动作步骤数组",
                    },
                    "on_error": {
                        "type": "string",
                        "enum": ["stop", "continue"],
                        "description": "失败时停止或继续",
                    },
                    "clipboard_action": {
                        "type": "string",
                        "enum": ["set", "get", "clear", "append"],
                        "description": "剪贴板操作",
                    },
                    "element": {
                        "type": "string",
                        "description": "控件名或过滤子串",
                    },
                    "via": {
                        "type": "string",
                        "enum": ["typing", "clipboard"],
                        "description": "输入方式",
                    },
                    "paste_key": {
                        "type": "string",
                        "description": "粘贴组合键",
                    },
                    "restore_clipboard": {
                        "type": "boolean",
                        "description": "粘贴后恢复剪贴板",
                    },
                    "wait_for": {
                        "type": "string",
                        "description": "等待界面变化或稳定",
                    },
                    "wait_timeout": {
                        "type": "number",
                        "description": "wait_for 超时秒数",
                    },
                    "diff": {
                        "type": "boolean",
                        "description": "是否比较注入前后截图",
                    },
                    "tolerance": {
                        "type": "number",
                        "description": "颜色容差",
                    },
                    "max_elements": {
                        "type": "number",
                        "description": "控件清单最大条数",
                    },
                    "template": {
                        "type": "string",
                        "description": "模板图片路径",
                    },
                    "query": {
                        "type": "string",
                        "description": "要查找的文字",
                    },
                    "ocr_lang": {
                        "type": "string",
                        "description": "OCR 语言标签",
                    },
                    "max_results": {
                        "type": "number",
                        "description": "最大匹配条数",
                    },
                    "min_scale": {
                        "type": "number",
                        "description": "最小缩放比例",
                    },
                    "max_scale": {
                        "type": "number",
                        "description": "最大缩放比例",
                    },
                    "scale_steps": {
                        "type": "number",
                        "description": "缩放档数",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["point", "region", "find"],
                        "description": "取色模式",
                    },
                    "color": {
                        "type": "string",
                        "description": "颜色",
                    },
                    "region": {
                        "type": "string",
                        "description": "取色或查找区域",
                    },
                    "min_pixels": {
                        "type": "number",
                        "description": "连通块最小像素数",
                    },
                    "max_regions": {
                        "type": "number",
                        "description": "最大候选区域数",
                    },
                    "output": {
                        "type": "string",
                        "description": "输出路径",
                    },
                    "boxes": {
                        "type": "array",
                        "items": {"type": ["string", "object", "array"]},
                        "description": "矩形列表",
                    },
                    "points": {
                        "type": "array",
                        "items": {"type": ["string", "object", "array"]},
                        "description": "点列表",
                    },
                    "labels": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "标签列表",
                    },
                    "macro": {
                        "type": "string",
                        "description": "宏名",
                    },
                    "times": {
                        "type": "number",
                        "description": "重复次数",
                    },
                    "append": {
                        "type": "boolean",
                        "description": "是否追加",
                    },
                },
                "required": ["op"],
            },
        },
    }


__all__ = ["build_bash_opt_schema"]
