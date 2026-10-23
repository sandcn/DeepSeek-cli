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
                "click/drag/scroll/key/type/release。read 增量读取并清空缓冲"
                "（不等待，默认最后 1000 行）；wait 等待完成，超时返回 timeout=true "
                "且任务继续运行；kill 杀进程树并校验。clipboard/record/release "
                "无需 task_id；GUI 输入 op 用 window 选目标窗口（缺省 main，"
                "可 #N/title:子串），可用 element 按控件名操作。"
                "例：bash_opt({\"task_id\": \"bg-abc123\", \"op\": \"read\", "
                "\"max_lines\": 100})；截图：bash_opt({\"op\": \"screenshot\", "
                "\"window\": \"mintty\", \"shot\": \"shot.png\"})"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": (
                            "后台任务 ID（bg-xxx）；"
                            "clipboard/record/release 及 screen 截图可省略"
                        ),
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
                        "description": "超时秒数（wait/wait_window 默认 300，0 不限）",
                    },
                    "allow_hidden": {
                        "type": "boolean",
                        "description": "隐藏/最小化窗口是否也返回（默认 false）",
                    },
                    "text": {
                        "type": "string",
                        "description": "输入文本（stdin/type/clipboard 用；支持 Unicode）",
                    },
                    "newline": {
                        "type": "boolean",
                        "description": "stdin 末尾是否追加换行（默认 true）",
                    },
                    "encoding": {
                        "type": "string",
                        "description": "文本编码（默认 utf-8；GBK 控制台传 gbk）",
                    },
                    "max_lines": {
                        "type": "integer",
                        "description": "read 最多返回行数（默认 1000；0 不按行截断）",
                    },
                    "key": {
                        "type": "string",
                        "description": "按键名（如 ctrl+c / f5 / esc / a，支持组合键）",
                    },
                    "window": {
                        "type": "string",
                        "description": (
                            "窗口选择器（缺省 main；"
                            "active/#N/title:子串/class:/pid:/handle:）"
                        ),
                    },
                    "grid": {
                        "type": ["number", "boolean"],
                        "description": "截图上叠加等距坐标参考线（像素；0 自动，省略不画）",
                    },
                    "screen": {
                        "type": ["boolean", "string", "number"],
                        "description": "整屏/多显示器截取（true / primary / 序号；可省 task_id）",
                    },
                    "margin": {
                        "type": "number",
                        "description": "按控件（element）截图时区域外扩像素",
                    },
                    "shot": {
                        "type": ["string", "boolean"],
                        "description": "输入后自动截图保存（路径；true 存 bash_opt_shots/）",
                    },
                    "settle": {
                        "type": "number",
                        "description": "注入后等待秒数（上限 30）",
                    },
                    "window_action": {
                        "type": "string",
                        "enum": ["activate", "maximize", "minimize", "restore",
                                 "close", "move", "resize", "fit",
                                 "always_on_top", "not_on_top",
                                 "get_geometry", "save_geometry",
                                 "restore_geometry"],
                        "description": (
                            "窗口动作：activate/maximize/minimize/restore/"
                            "close/move/resize/fit 等"
                        ),
                    },
                    "width": {
                        "type": "number",
                        "description": "窗口目标宽度（resize/move，像素）",
                    },
                    "height": {
                        "type": "number",
                        "description": "窗口目标高度（resize/move，像素）",
                    },
                    "path": {
                        "type": "string",
                        "description": "文件路径（截图输出 / 标注源图 / record 宏文件）",
                    },
                    "crop": {
                        "type": "string",
                        "description": "只截窗口内区域 x,y,width,height（原点为整窗左上角）",
                    },
                    "x": {
                        "type": ["number", "string"],
                        "description": "窗口内 X 坐标（像素或 center/left/right/50%/center+20）",
                    },
                    "y": {
                        "type": ["number", "string"],
                        "description": "窗口内 Y 坐标（像素或 center/top/bottom/50%/center+20）",
                    },
                    "to_x": {
                        "type": ["number", "string"],
                        "description": "拖动终点 X（像素或语义值）",
                    },
                    "to_y": {
                        "type": ["number", "string"],
                        "description": "拖动终点 Y（像素或语义值）",
                    },
                    "from_x": {
                        "type": ["number", "string"],
                        "description": "拖动起点 X（像素或语义值）",
                    },
                    "from_y": {
                        "type": ["number", "string"],
                        "description": "拖动起点 Y（像素或语义值）",
                    },
                    "button": {
                        "type": "string",
                        "enum": ["left", "right", "middle"],
                        "description": "鼠标按钮：left（默认）/ right / middle",
                    },
                    "count": {
                        "type": "number",
                        "description": "点击次数（默认 1，上限 100）",
                    },
                    "hold": {
                        "type": "number",
                        "description": "长按秒数（click/key；省略用最小按下时长）",
                    },
                    "interval": {
                        "type": "number",
                        "description": "连按/多击间隔秒数（默认由后端决定）",
                    },
                    "dwell": {
                        "type": "number",
                        "description": "悬停停留秒数（hover，默认 0.6）",
                    },
                    "dx": {
                        "type": "number",
                        "description": "相对当前光标水平偏移（move，像素）",
                    },
                    "dy": {
                        "type": "number",
                        "description": "相对当前光标垂直偏移（move，像素）",
                    },
                    "modifiers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "动作期间按住的修饰键（ctrl/alt/shift/meta）",
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down", "left", "right"],
                        "description": "滚动方向：up / down（默认）/ left / right",
                    },
                    "amount": {
                        "type": "number",
                        "description": "滚动量（默认 3，上限 100）",
                    },
                    "duration": {
                        "type": "number",
                        "description": "动作耗时秒数（drag 默认 0.3；move 平滑用）",
                    },
                    "steps": {
                        "type": "number",
                        "description": "插值步数（drag 默认 20；move 平滑点数）",
                    },
                    "method": {
                        "type": "string",
                        "enum": ["auto", "sendinput", "message"],
                        "description": "输入注入方式：auto（默认）/ sendinput / message",
                    },
                    "phase": {
                        "type": "string",
                        "enum": ["press", "down", "up"],
                        "description": "按键阶段：press（默认）/ down / up",
                    },
                    "repeat": {
                        "type": "number",
                        "description": "连按次数（key/keys，phase=press 时生效）",
                    },
                    "relative_event": {
                        "type": "boolean",
                        "description": "move 发纯相对位移事件（游戏视角，不受光标锁定影响）",
                    },
                    "hold_keys": {
                        "type": ["string", "array"],
                        "items": {"type": "string"},
                        "description": "动作期间按住的任意键（如 [\"w\",\"shift\"]，最多 16 个）",
                    },
                    "keys": {
                        "type": ["string", "array"],
                        "items": {"type": "string"},
                        "description": "release 要释放的键（留空=全部）",
                    },
                    "buttons": {
                        "type": ["string", "array"],
                        "items": {"type": "string"},
                        "description": "release 要释放的鼠标按钮（留空=全部）",
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
                        "description": "步骤数组（sequence/record；每步含 op，可混排动作与等待）",
                    },
                    "on_error": {
                        "type": "string",
                        "enum": ["stop", "continue"],
                        "description": "步骤失败时：stop（默认）/ continue",
                    },
                    "clipboard_action": {
                        "type": "string",
                        "enum": ["set", "get", "clear", "append"],
                        "description": "剪贴板操作：set / get / clear / append",
                    },
                    "element": {
                        "type": "string",
                        "description": "控件名或子串（先 elements 枚举；自动换算中心坐标）",
                    },
                    "via": {
                        "type": "string",
                        "enum": ["typing", "clipboard"],
                        "description": "type 输入方式：typing（默认逐字符）/ clipboard（粘贴）",
                    },
                    "paste_key": {
                        "type": "string",
                        "description": "粘贴组合键（via=clipboard，默认 ctrl+v）",
                    },
                    "restore_clipboard": {
                        "type": "boolean",
                        "description": "粘贴后恢复原剪贴板（默认 true）",
                    },
                    "wait_for": {
                        "type": "string",
                        "description": "注入后等待：change / stable / 秒数（默认不等待）",
                    },
                    "wait_timeout": {
                        "type": "number",
                        "description": "wait_for 超时秒数（默认 5）",
                    },
                    "diff": {
                        "type": "boolean",
                        "description": "注入前后比较截图，返回变化比例与区域（默认 false）",
                    },
                    "tolerance": {
                        "type": "number",
                        "description": "颜色比较容差（每通道，默认 8）",
                    },
                    "max_elements": {
                        "type": "number",
                        "description": "控件清单最大条数（默认 200，上限 2000）",
                    },
                    "template": {
                        "type": "string",
                        "description": "模板图片路径（locate 图像匹配用）",
                    },
                    "query": {
                        "type": "string",
                        "description": "要 OCR 查找的文字（locate 用）",
                    },
                    "ocr_lang": {
                        "type": "string",
                        "description": "OCR 语言标签（BCP-47，如 zh-Hans-CN / en-US）",
                    },
                    "max_results": {
                        "type": "number",
                        "description": "最大匹配条数（locate，默认上限 50）",
                    },
                    "min_scale": {
                        "type": "number",
                        "description": "模板最小缩放比例（默认 1.0）",
                    },
                    "max_scale": {
                        "type": "number",
                        "description": "模板最大缩放比例（默认 1.0）",
                    },
                    "scale_steps": {
                        "type": "number",
                        "description": "模板缩放搜索档数（默认 1）",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["point", "region", "find"],
                        "description": "取色模式：point（点）/ region（区域统计）/ find（找色）",
                    },
                    "color": {
                        "type": "string",
                        "description": "颜色（#RRGGBB 或名称，如 #ff0000 / red）",
                    },
                    "region": {
                        "type": "string",
                        "description": "区域 x,y,width,height（pixel 用）",
                    },
                    "min_pixels": {
                        "type": "number",
                        "description": "find 模式连通块最小像素数（过滤噪点）",
                    },
                    "max_regions": {
                        "type": "number",
                        "description": "find 模式最多返回候选区域数（默认 10）",
                    },
                    "output": {
                        "type": "string",
                        "description": "标注结果输出路径（省略则覆盖源图 / 存 bash_opt_shots/）",
                    },
                    "boxes": {
                        "type": "array",
                        "items": {"type": ["string", "object", "array"]},
                        "description": "矩形列表（每项 'x,y,w,h' / 序列 / 字典）",
                    },
                    "points": {
                        "type": "array",
                        "items": {"type": ["string", "object", "array"]},
                        "description": "点列表（每项 'x,y' / 序列 / 字典）",
                    },
                    "labels": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "与 boxes/points 一一对应的标签文本",
                    },
                    "macro": {
                        "type": "string",
                        "description": "宏名（record 保存 / replay 读取，存 bash_opt_macros/）",
                    },
                    "times": {
                        "type": "number",
                        "description": "replay 重复次数（默认 1，上限 20）",
                    },
                    "append": {
                        "type": "boolean",
                        "description": "record 时追加到同名宏末尾（默认 false）",
                    },
                },
                "required": ["op"],
            },
        },
    }


__all__ = ["build_bash_opt_schema"]
