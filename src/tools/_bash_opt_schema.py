"""bash_opt 工具 JSON Schema — 纯数据定义（与执行逻辑分离）。

``bash_opt.py`` 只保留工具执行逻辑，``to_tool_schema`` 退化为一行委托；
本模块无副作用，``INPUT_OPS``（GUI 输入类 op 名单）的唯一真源在
``src/tools/_window_input/action.py``，此处延迟导入以避免导入环。

每个 op 的用法例 / 反例集中在 :data:`OP_GUIDE`（与 ``op`` 枚举同序），由
:func:`_render_op_guide` 渲染进模型可见的 ``function.description``——模型
常见的错误不是「不知道有某个 op」，而是用错 op（read 当 wait、keys 代替
kill、屏幕绝对坐标点窗口、对 GUI 用 stdin 等），反例直接给出替代做法。

GUI 窗口输入 op（click / move / hover / drag / scroll / key / type）统一支持
``path``：传了就在**输入注入完成后**立即截取目标窗口（同一 ``window`` 选择器），
文件名由 ``path`` 指定；不传则只注入输入、不产生截图。用法例因此统一带上
``"path":"shot.png"``，让模型默认养成「操作后留证」的习惯。

GUI 能力只有三类：光标（鼠标）操作、截图、键盘输入；参数只保留完成这三类
操作所必需的项（坐标 / 按钮 / 次数 / 滚动 / 拖动 / 窗口 / 截图区域），
长按、连按、平滑、相对位移、投递方式、按键阶段、修饰键、悬停时长等调参项
以及窗口枚举 / 控件清单 / 剪贴板 / 图像文字定位 / 取色 / 标注 / 宏 / 序列
等能力均已移除。
"""

from __future__ import annotations

from typing import Any


def _input_ops() -> tuple:
    from ._window_input.action import INPUT_OPS

    return INPUT_OPS


#: 每个 op 的用法例（``usage``，可直接照抄的调用）与反例（``counter``，最常见
#: 的误用及其替代做法）。顺序与 ``op`` 枚举一致，由 :func:`_render_op_guide`
#: 渲染进模型可见的 ``function.description``。
#:
#: 写成「用法例 + 反例」而不是只列能力，是因为模型最容易犯的错不是不知道有
#: 这个 op，而是**用错 op**（拿 read 等完成、拿 keys 代替 kill、拿屏幕绝对
#: 坐标点窗口、对 GUI 用 stdin 等）。反例直接给出替代 op，模型据此即可自纠。
OP_GUIDE: dict[str, dict[str, str]] = {
    "read": {
        "usage": '{"task_id":"bg-1","op":"read"} 读增量、立即返回',
        "counter": "用 read 轮询等命令结束（等完成应 wait）",
    },
    "wait": {
        "usage": '{"task_id":"bg-1","op":"wait","timeout":60} 取最终输出',
        "counter": "交互中 wait（先 stdin/keys，看进度用 read）",
    },
    "kill": {
        "usage": '{"task_id":"bg-1","op":"kill"} 杀进程树并校验',
        "counter": "用 keys 发 ctrl+c 代替 kill（常无效）",
    },
    "stdin": {
        "usage": '{"task_id":"bg-1","op":"stdin","text":"y"} 写一行',
        "counter": "给 GUI 输文本（用 type/key）",
    },
    "close_stdin": {
        "usage": '{"task_id":"bg-1","op":"close_stdin"} 送 EOF',
        "counter": "当结束任务（PTY 无真 EOF，用 kill）",
    },
    "keys": {
        "usage": '{"task_id":"bg-1","op":"keys","key":"ctrl+c"} 单键',
        "counter": "一次多键或整段文本（分次或 type）",
    },
    "screenshot": {
        "usage": '{"task_id":"bg-1","op":"screenshot","path":"shot.png"}',
        "counter": "无 path / 任务已结束截窗口（截桌面 screen=true）",
    },
    "click": {
        "usage": ('{"task_id":"bg-1","op":"click","x":"center","y":"50%",'
                  '"count":2,"path":"shot.png"} 点击后自动截图'),
        "counter": "屏幕绝对坐标 / 把 path 当坐标（path 只用于操作后截图）",
    },
    "move": {
        "usage": ('{"task_id":"bg-1","op":"move","x":100,"y":80,'
                  '"path":"shot.png"} 只移光标，操作后自动截图'),
        "counter": "代替 click/hover（不会点击/悬停）",
    },
    "hover": {
        "usage": ('{"task_id":"bg-1","op":"hover","x":"center","y":"top+5",'
                  '"path":"shot.png"} 悬停后自动截图'),
        "counter": "当点击用（悬停不按下）",
    },
    "drag": {
        "usage": ('{"task_id":"bg-1","op":"drag","to_x":200,"to_y":120,'
                  '"path":"shot.png"} 起点省略=窗口中心，操作后自动截图'),
        "counter": "漏 to_x/to_y 或只给一半起点",
    },
    "scroll": {
        "usage": ('{"task_id":"bg-1","op":"scroll","direction":"down",'
                  '"amount":5,"path":"shot.png"} 滚动后自动截图'),
        "counter": "移动/点击（只滚轮）；amount≤100",
    },
    "key": {
        "usage": ('{"task_id":"bg-1","op":"key","key":"ctrl+shift+s",'
                  '"path":"shot.png"} 按键后自动截图'),
        "counter": "输整段文本（用 type）",
    },
    "type": {
        "usage": ('{"task_id":"bg-1","op":"type","text":"hello",'
                  '"newline":true,"path":"shot.png"} 输入后自动截图'),
        "counter": "终端输入命令（用 stdin）；以为默认带回车",
    },
    "release": {
        "usage": '{"op":"release"} 无需 task_id',
        "counter": "传 task_id 想续命/重启任务",
    },
}


def _render_op_guide() -> str:
    """把 :data:`OP_GUIDE` 渲染为模型可见的「用法例 / 反例」清单。

    Returns:
        以换行开头的多行文本（紧跟功能描述正文，避免与上句粘连）。每个 op
        占一行：``op：✓ <用法例>；✗ <反例>``。``✓`` / ``✗`` 便于模型与用户
        快速区分「照抄这条」与「别这么做」。
    """
    lines = ["", "每 op 用法例（✓）/ 反例（✗）："]
    for op, guide in OP_GUIDE.items():
        lines.append(f"{op}：✓ {guide['usage']}；✗ {guide['counter']}")
    return "\n".join(lines)


def build_bash_opt_schema() -> dict[str, Any]:
    """构建 bash_opt 的 OpenAI function schema（每次调用返回全新 dict）。"""
    INPUT_OPS = _input_ops()
    return {
        "type": "function",
        "function": {
            "name": "bash_opt",
            "description": (
                "按 task_id 操作后台 bash 任务或 GUI。read 为增量读取：读后清空、"
                "立即返回、不接管任务生命周期（默认最后 1000 行，max_lines=0 "
                "不限）；wait 等待完成并取 stdout/stderr/returncode，超时返回 "
                "timeout=true 且任务继续运行；kill 杀进程树并校验残留。GUI 只有"
                "光标、截图、键盘：window 选目标窗口（缺省 main，可 #N/title:"
                "子串），坐标以窗口截图左上角为原点，支持 center/50%/center+20 "
                "等语义值；screenshot 用 path 存 PNG（可 crop/screen）；GUI 窗口"
                "输入 op（click/move/hover/drag/scroll/key/type）传 path 即在操作"
                "完成后自动截图（文件名由 path 指定，固定截目标窗口），不传 path "
                "只注入输入。GUI 流程例："
                "bash({\"command\": \"notepad.exe\", \"background\": true}) 启动取得 "
                "task_id → bash_opt({\"task_id\": \"bg-1\", \"op\": \"screenshot\", "
                "\"path\": \"shot.png\"}) 截图 → read_image 看图 → click/type 操作 → "
                "wait 或 kill 结束。反例：bash 不带 background 启动 GUI（start "
                "立即返回、进程脱离任务，bash_opt 找不到窗口）。"
                + _render_op_guide()
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "后台任务 ID（bg-xxx）；release 与 screen 截图可省略",
                    },
                    "op": {
                        "type": "string",
                        "enum": ["read", "wait", "kill", "stdin",
                                 "close_stdin", "keys", "screenshot",
                                 *INPUT_OPS],
                        "description": "要执行的操作（见功能描述）",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "超时秒数（wait 默认 300，0 不限）",
                    },
                    "text": {
                        "type": "string",
                        "description": "输入文本（stdin/type 用；支持 Unicode）",
                    },
                    "newline": {
                        "type": "boolean",
                        "description": "stdin/type 末尾是否追加换行（stdin 默认 true）",
                    },
                    "encoding": {
                        "type": "string",
                        "description": "stdin 文本编码（默认 utf-8；GBK 控制台传 gbk）",
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
                        "description": "窗口选择器（缺省 main；active/#N/title:子串）",
                    },
                    "path": {
                        "type": "string",
                        "description": "截图输出路径；op=screenshot 必填，GUI 输入 op 传它则操作后自动截图（缺扩展名补 .png）",
                    },
                    "crop": {
                        "type": "string",
                        "description": "只截窗口内区域 x,y,width,height（原点为整窗左上角）",
                    },
                    "screen": {
                        "type": ["boolean", "string", "number"],
                        "description": "整屏/多显示器截取（true / primary / 序号；可省 task_id）",
                    },
                    "x": {
                        "type": ["number", "string"],
                        "description": "窗口内 X 坐标（像素或 center/left/right/50%/center+20）",
                    },
                    "y": {
                        "type": ["number", "string"],
                        "description": "窗口内 Y 坐标（像素或 center/top/bottom/50%/center+20）",
                    },
                    "button": {
                        "type": "string",
                        "enum": ["left", "right", "middle"],
                        "description": "鼠标按钮：left（默认）/ right / middle",
                    },
                    "count": {
                        "type": "number",
                        "description": "点击次数（默认 1；2 = 双击）",
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
                    "from_x": {
                        "type": ["number", "string"],
                        "description": "拖动起点 X（省略时从窗口中心开始）",
                    },
                    "from_y": {
                        "type": ["number", "string"],
                        "description": "拖动起点 Y（省略时从窗口中心开始）",
                    },
                    "to_x": {
                        "type": ["number", "string"],
                        "description": "拖动终点 X（必填）",
                    },
                    "to_y": {
                        "type": ["number", "string"],
                        "description": "拖动终点 Y（必填）",
                    },
                },
                "required": ["op"],
            },
        },
    }


__all__ = ["build_bash_opt_schema", "OP_GUIDE"]
