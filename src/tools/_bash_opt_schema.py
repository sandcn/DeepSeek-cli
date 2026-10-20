"""bash_opt 工具 JSON Schema — 纯数据定义（与执行逻辑分离）。

从 ``src/tools/bash_opt.py`` 的 ``BashOptFunc.to_tool_schema`` 抽出（该方法
曾是单函数 880 行、几乎全部为 schema 字面量）。抽出后：

- ``bash_opt.py`` 只保留工具执行逻辑，``to_tool_schema`` 退化为一行委托；
- 本模块无副作用、无第三方依赖（除 ``INPUT_OPS`` 常量），便于比对与维护。

``INPUT_OPS``（GUI 输入类 op 名单）的唯一真源在
``src/tools/_window_input/action.py``，此处延迟导入以避免与工具模块形成
导入环（schema 仅在注册期构建一次，延迟导入无热路径代价）。
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
                "按 task_id 操作后台 bash 任务（由 bash background=true 启动）。"
                "op：read（读取当前已产生的全部输出并清空缓冲，立即返回不等待完成；"
                "包含前台超时自动转后台之前的输出，默认最多返回最后 1000 行）、"
                "wait（等待完成取结果 JSON：task_id/status/stdout/stderr/returncode，"
                "timeout 秒，默认 300/0 无限；超时同样返回 JSON"
                "（timeout=true / done=false / waited / hint），任务继续运行）、"
                "kill（杀整个进程树：进程组 + 全部递归后代（Windows 原生用"
                "Toolhelp32 进程表收集，根进程已退出时也能找到孤儿后代），"
                "并按命令行里出现过的程序名兜底 daemon 化的 GUI（Cygwin 下"
                "mintty 启动后父进程即退出、脱离进程树与 Job，只能按名兜底；"
                "按名匹配限定在任务启动后 60 秒内，避免误杀用户同名程序），"
                "杀后校验是否真正退出、未死自动补杀，最多 3 轮尝试，残留进程"
                "在结果中报告；记录的 PID 已被系统复用（创建时间不匹配）时"
                "拒绝杀，避免误杀无关进程）、stdin（发文本到 stdin，需 text）、"
                "keys（发送按键，需 key：目标进程有 GUI 窗口时自动作为窗口级"
                "键盘消息注入该窗口，否则写入终端；支持 ctrl+c 等组合键、"
                "esc/pageup 等别名与单个字符，repeat 可一次连按多次；"
                "Ctrl+C 在 ConPTY 下对部分程序有效、不保证中断所有程序）、"
                "screenshot（把该命令进程树的窗口截图存为 PNG，需 path，"
                "可选 crop 指定只截取的像素区域，格式 'x,y,width,height'，"
                "可选 window 选择目标窗口（'#1'/'title:子串'/'popup' 等）、"
                "grid 叠加坐标参考线）、"
                "windows（列出该命令进程树的全部窗口及其句柄/标题/几何/Z 序）、"
                "window（控制窗口，window_action=activate/maximize/minimize/"
                "restore/close/move/resize/fit，用 window 选择目标窗口）、"
                "move/hover/click/drag/scroll/key/type（向该命令进程树的 GUI 窗口注入"
                "鼠标移动（绝对 x/y 或相对 dx/dy）/悬停/点击（左中右键、"
                "可双击、可 hold 长按）/拖动/滚轮/按键/文本，"
                "key 支持 phase=press/down/up 的按下与弹起分离发送，"
                "window 可选目标窗口，shot 可注入后自动截图，"
                "坐标以窗口截图左上角为原点且可用 screenshot 对照；"
                "element 可按控件名定位（click/move/scroll/drag 用控件中心，"
                "type/key 先点击该控件聚焦）；type 可用 via='clipboard' 走"
                "剪贴板粘贴输入长文本/中文/emoji；wait_for='change'/'stable' "
                "可在注入后等界面变化或稳定；diff=true 会回传注入前后截图差异；"
                "x/y 支持 'center'、'50%'、'center+20' 等语义坐标）、"
                "sequence（一次调用按序执行多个动作：actions 数组，步骤可为"
                "输入动作或 wait/screenshot/window，on_error 决定遇错停止/继续）、"
                "clipboard（读写系统剪贴板：clipboard_action=set/get/clear/append，"
                "set/append 需 text）、"
                "wait_window（等待窗口**可用**：默认等到窗口可见且未最小化"
                "（能截图 / 能注入输入）才返回，allow_hidden=true 时命中的隐藏"
                "窗口也立即返回，timeout 秒）、"
                "elements（列出窗口内控件清单：名称/类型/矩形/可用状态，"
                "含窗口内坐标可直接用于 click；max_elements 限制条数，"
                "element 作为过滤子串）。"
                "另有 locate（截图内按 template 模板匹配或 query OCR 文字定位，"
                "返回可点击坐标）、pixel（读取截图像素颜色：point 点取色 / "
                "region 区域统计 / find 查找颜色连通块）、annotate（在截图上"
                "绘制矩形 / 十字 / 编号标签）、record / replay（把 actions 存为"
                "命名宏 macro 后按 macro 或 path 回放，times 重复）、release"
                "（释放按下的键与鼠标按钮，清理 phase='down' 的悬空状态）——"
                "用法见 op 参数的逐项说明。"
                "task_id 必须是当前对话 bash 后台返回的 bg-xxx。返回：操作结果 JSON 或输出；失败以 ( 开头。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": (
                            "后台 bash 任务的 task_id（bash background=True 返回的 "
                            "'bg-xxx' 格式 ID）。除以下无需任务的 op 外必填："
                            "clipboard（系统剪贴板）、record（写宏文件）、"
                            "release（释放按下的键 / 鼠标按钮）、带 path 的 "
                            "annotate（标注已有 PNG）、screen=… 的 screenshot"
                            "（整屏 / 多显示器截取）。其余 op（read/wait/kill/"
                            "stdin/keys/windows/window/elements/wait_window/"
                            "locate/pixel/sequence/replay 与窗口输入）都需要对应"
                            "后台任务的进程句柄或窗口。"
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
                        "description": (
                            "要执行的操作："
                            "\n- read：读取后台命令当前已产生的全部输出并清空缓冲，"
                            "立即返回（不等待任务完成）；后续 read 只返回新产生的输出，"
                            "默认最多返回最后 1000 行（max_lines 可调，0 = 不按行截断），"
                            "最终完整结果由 wait 获取"
                            "\n- wait：等待任务完成并获取命令输出"
                            "\n- kill：杀死任务所有进程树（进程组 + 全部递归"
                            "后代；Windows 原生用 Toolhelp32 进程表收集，根进程"
                            "已退出时仍能定位孤儿后代；另按命令行里出现过的程序"
                            "名兜底 daemon 化的 GUI——Cygwin 下 mintty 启动后"
                            "脱离父进程，进程树与 Job 都够不着，只能在任务启动"
                            "后 60 秒内按名匹配）；杀完后校验进程是否真正"
                            "退出（僵尸视为已终止），仍有存活则自动补杀（最多 3 "
                            "轮尝试）；校验时仍存活的残留 PID 会在结果中列出；"
                            "记录的 PID 已被系统复用（创建时间不匹配）时拒绝杀"
                            "\n- stdin：向任务 stdin 发送文本输入（需 text；"
                            "encoding 指定文本编码，缺省 utf-8——与 bash 工具为"
                            "子进程统一设置的 stdio 编码一致（Python / Node / "
                            "PowerShell 都按 UTF-8 读 stdin）；需要按系统 ANSI "
                            "代码页（中文 Windows 通常为 gbk / cp936）写入原生"
                            "控制台程序时显式传 encoding='gbk'；Cygwin / MSYS2 "
                            "同样默认 utf-8）"
                            "\n- close_stdin：尝试关闭任务 stdin。"
                            "bash 任务统一在 PTY 中运行，**没有真正的 EOF**——"
                            "只是写入 EOT（ConPTY 为 Ctrl+Z、POSIX 为 Ctrl+D）"
                            "模拟按键，程序不一定会退出（实测 python REPL 收到 "
                            "EOT 后仍在运行）；依赖 stdin EOF 的命令请用 op=kill "
                            "结束任务。PTY 的 stdin 通道不会因此关闭（EOT 只是"
                            "一次按键，之后仍可继续 stdin / keys 写入）"
                            "\n- keys：向任务发送光标/键盘消息（需 key；"
                            "目标进程有 GUI 窗口时自动作为窗口级键盘消息注入该窗口，"
                            "没有 GUI 窗口则写入终端 PTY/stdin；"
                            "支持 ctrl+c 等组合键、esc/pageup 等别名与单个字符）"
                            "注意：组合键事件送达不等于目标控件执行了对应动作"
                            "（如部分 TextBox 收到 Ctrl+A 但不全选），效果需用 "
                            "screenshot / diff 复核；Windows ConPTY 下 ctrl+c "
                            "对部分程序可中断"
                            "（实测可中断 Python 的 sleep），但不保证所有程序——"
                            "进程无响应时请用 op=kill"
                            "\n- screenshot：把任务进程树（含其启动的 GUI 子进程）的窗口"
                            "或整个屏幕截图保存为 PNG 文件（需 path；可选 crop 指定只截取的像素区域，"
                            "screen=true/'primary'/序号 整屏 / 多显示器截取（不再需要进程句柄），"
                            "element+margin 只截某控件区域），"
                            "用于查看图形程序运行画面；"
                            "结果附带 window_x/window_y（截图像素左上角对应的屏幕坐标）"
                            "与 window_rect（窗口外框），便于截图像素与屏幕坐标换算；"
                            "窗口最大化 / 部分超出屏幕时，产物只包含屏幕内可见部分，"
                            "截图原点与外框左上角相差 offset（见结果 hint 的换算公式）；"
                            "多显示器下 screen=序号 指定单个显示器；"
                            "纯命令行进程没有窗口，会返回错误说明"
                            "\n- move/hover/click/drag/scroll/key/type：向任务进程树的 GUI 窗口"
                            "注入输入（鼠标移动（x/y 绝对或 dx/dy 相对；"
                            "duration+steps 可做平滑移动）/悬停/点击（左中右键、"
                            "双击即 count=2、hold 长按）/拖动/滚轮、"
                            "键盘按键（phase=press/down/up 分按下与弹起）、文本）；"
                            "坐标以窗口截图左上角为原点（与 screenshot 产物一致），"
                            "click/scroll 省略坐标时作用于窗口中心；"
                            "键输入需 key，文本输入需 text；纯命令行进程没有窗口，会报错"
                            "\n- windows：列出该后台任务进程树的全部窗口"
                            "（句柄/标题/类名/位置尺寸/Z 序/是否前台/是否主窗口/"
                            "tool_window/client_area），"
                            "z_index 与 '#N' 选择器编号一致、selectable_total 为可操作"
                            "窗口数；selectable_total=0 表示窗口都还不可见 / 已最小化"
                            "（GUI 程序启动初期常见），先 wait_window 等窗口可用、"
                            "或对已有窗口执行 window_action='restore' 再截图 / 输入；"
                            "用于挑选目标窗口（右键菜单、下拉浮层、对话框等独立顶层"
                            "窗口都在其中）；"
                            "tool_window=true 的弹层不参与前台切换，输入 op 会按"
                            "「同应用是否在前台」选通道，鼠标按屏幕坐标命中光标下的"
                            "窗口、键盘交给同一应用的前台窗口；"
                            "client_area=false 表示该窗口没有可换算的客户区"
                            "（不能用 method='message' 投递鼠标坐标）"
                            "\n- window：控制窗口状态与几何（需 window_action="
                            "activate/maximize/minimize/restore/close/move/resize/"
                            "fit/always_on_top/not_on_top/get_geometry/"
                            "save_geometry/restore_geometry，配合 window 选择器"
                            "指定目标窗口；always_on_top 置顶避免被遮挡，"
                            "save_geometry/restore_geometry 记住并恢复几何）"
                            "\n- elements：列出窗口内控件（名称/类型/类名/矩形/"
                            "可用状态），含屏幕坐标与窗口内坐标（window_center_x/"
                            "window_center_y，可直接用于 click）；"
                            "element 可作为过滤子串，max_elements 限制条数。"
                            "Windows 优先走 UI Automation：Chrome/Electron/"
                            "Qt/WPF/UWP 等自绘界面也能枚举到无障碍节点；"
                            "UIA 不可用时回退经典 Win32 子窗口枚举"
                            "\n- wait_window：等待窗口**可用**（按 window 选择器，"
                            "timeout 秒，默认 15 秒）；默认等到窗口可见且未最小化"
                            "（能截图 / 能注入输入）才返回，避免启动初期拿到一个"
                            "visible=false 的窗口直接失败；确需操作隐藏 / 最小化"
                            "窗口时传 allow_hidden=true，或改用 handle:0x…"
                            "\n- clipboard：读写系统剪贴板"
                            "（clipboard_action=set/get/clear/append，"
                            "set/append 需 text；缺省按是否提供 text 推断）；"
                            "配合 key='ctrl+v' 或 type 的 via='clipboard' 粘贴"
                            "长文本/中文/emoji"
                            "\n- locate：在窗口截图里定位「局部图标」或"
                            "「文字」，返回坐标（与输入 op 同源，可直接点击）。"
                            "给 template=模板图片路径 做模板匹配；给 query="
                            "要查找的文字 做 OCR 文字识别（Windows 用系统自带 "
                            "OCR，其它平台用 tesseract），ocr_lang 可指定 OCR "
                            "语言标签（如 zh-Hans-CN / en-US，缺省按用户配置）。"
                            "精确匹配失败时自动做近似匹配（OCR 易混字符与"
                            "下划线 / 破折号等分隔符归一，结果里 match_mode="
                            "'fuzzy' 并提示复核）；中文查询还会在字符折叠失败后"
                            "按**整行相似度**兜底（OCR 把汉字认错，如「钮」→"
                            "「田」，命中时 match_mode='similar'，同样需要复核）。"
                            "适合没有可枚举控件的"
                            "界面（游戏 / canvas / 图片按钮）；crop 可限定"
                            "搜索区域"
                            "\n- sequence：一次调用按序执行多个动作"
                            "（actions 数组，每项形如 {\"op\": \"click\", \"x\": 10}；"
                            "步骤可为输入动作或 wait（seconds）/screenshot（path）/"
                            "window（window_action）；on_error=stop/continue 决定"
                            "遇错停止还是继续）。适合「点输入框→输入→回车→截图」"
                            "这类连续操作，减少往返与中途失焦"
                            "\n- pixel：读取窗口截图像素颜色（mode=point 点取色 / "
                            "region 区域均值与主色 / find 查找目标颜色并按连通块"
                            "返回可点击的候选位置），适合识别状态灯、进度条等"
                            "没有可枚举控件的画面"
                            "\n- annotate：在截图上绘制矩形 / 十字 / 编号标签"
                            "（常配合 locate 的匹配结果复核），产出带标注的图；"
                            "boxes / points / labels 可用 window 截图或 path 指定"
                            "\n- record / replay：把一段操作序列（actions）保存为"
                            "命名宏（macro），之后一条 replay 调用重复回放"
                            "（times 可重复多次），适合重复性的 GUI 任务"
                            "\n- release：释放按下的键与鼠标按钮（keys / buttons "
                            "可只释放指定目标，留空释放全部）——清理 key/click 的 "
                            "phase='down' 留下的悬空状态，是游戏操作中断后的兜底"
                            "\n- 游戏操作增强：move 的 relative_event（发送纯相对"
                            "位移事件做视角控制，不受 ClipCursor / 锁定光标影响）、"
                            "click 的 phase=down/up（按住左键射击 / 拖框）与 hold "
                            "长按、key 的 hold 长按与 interval 连发间隔、hold_keys "
                            "（动作期间按住任意键，如按住 W 同时点击）"
                        ),
                    },
                    "timeout": {
                        "type": "number",
                        "description": (
                            "仅 wait / wait_window 生效：wait 为等待任务完成的"
                            "超时秒数（默认 300；传 0 表示无限等待，支持小数"
                            "如 0.5）——超时后任务继续运行，返回 JSON"
                            "（timeout=true / done=false / waited / hint），"
                            "可再次 wait 或 kill；wait_window 为等待窗口**可用**"
                            "的秒数（默认 15）。"
                        ),
                    },
                    "allow_hidden": {
                        "type": "boolean",
                        "description": (
                            "仅 wait_window：默认 false = 等到窗口可见且未最小化"
                            "（能截图 / 能注入输入）才返回；true = 命中的隐藏 / "
                            "最小化窗口也立即返回（结果里 selectable=false，"
                            "截图与合成输入可能失败，通常先 window_action="
                            "'restore' 更稳）。"
                        ),
                    },
                    "text": {
                        "type": "string",
                        "description": (
                            "stdin / type 操作的文本内容："
                            "stdin 为发送到后台命令 stdin 的内容（按 encoding "
                            "指定的编码编码；缺省 utf-8，与 bash 工具为子进程"
                            "统一设置的 stdio 编码一致（Cygwin / MSYS2 相同）；"
                            "需要 GBK 的原生控制台程序显式传 encoding='gbk'；"
                            "不经过 shell 解释）；"
                            "type 为注入到 GUI 窗口的文本（逐字符输入，支持任意 "
                            "Unicode，'\\n' 与 '\\t' 转为回车/制表键）。"
                        ),
                    },
                    "newline": {
                        "type": "boolean",
                        "description": (
                            "是否在 text 末尾追加换行：stdin 默认 true"
                            "（按「输入一行」语义发送），type 默认 false"
                            "（原样输入，需要回车时传 true 或在 text 中写 '\\n'）。"
                            "close_stdin 不使用该参数。"
                        ),
                    },
                    "encoding": {
                        "type": "string",
                        "description": (
                            "仅 stdin 生效：发送文本使用的编码（Python 编码名）。"
                            "缺省 utf-8——与 bash 工具为子进程统一设置的 stdio "
                            "编码一致（Python / Node / PowerShell 均按 UTF-8 读 "
                            "stdin；Windows 原生与 Cygwin / MSYS2 相同）。需要按"
                            "系统 ANSI 代码页（中文 Windows 通常为 gbk / cp936）"
                            "写入原生控制台程序（cmd 内建 / sort / findstr / more）"
                            "时显式传 encoding='gbk'。"
                        ),
                    },
                    "max_lines": {
                        "type": "integer",
                        "description": (
                            "仅 read 生效：单次返回的最大行数（默认 1000，保留"
                            "尾部最新行；与 bash 工具的 1000 行上限对齐）。"
                            "传 0 表示不按行截断，只受单次返回的字符上限约束"
                            "（超限时仍会截断并标注 truncated / dropped_chars）。"
                        ),
                    },
                    "key": {
                        "type": "string",
                        "description": (
                            "keys / key 操作的按键名（二者共用同一套键名规则）："
                            "keys（自动路由：有 GUI 窗口注入窗口、否则发终端）"
                            "支持修饰键组合（ctrl+c / alt+f4 / "
                            "shift+tab）、紧凑写法（ctrl_c / ctrl-c）、常用别名"
                            "（esc / return / del / ins / pageup / pgup / pgdn / "
                            "next / prior）、光标与编辑键（up/down/left/right/"
                            "home/end/page_up/page_down/insert/delete/backspace/"
                            "tab/enter/escape/space）、f1-f20 与单个字符；"
                            "key（GUI 窗口）用同样的组合键文本，如 'ctrl+shift+s'、"
                            "'alt+f4'、'enter'、'a'（支持 ctrl/alt/shift/meta "
                            "修饰键、编辑与导航键、f1-f24、单个字符）。"
                            "字母 / 数字键走系统虚拟键码，会经过系统输入法（IME）："
                            "中文输入法激活时，连按（repeat）字母键可能被输入法"
                            "吞并或变成候选上屏；需要稳定连按请先切到英文输入法，"
                            "或改用 type（Unicode 注入，不受输入法影响）。"
                        ),
                    },
                    "window": {
                        "type": "string",
                        "description": (
                            "目标窗口选择器（screenshot / 输入 op / window 通用；"
                            "省略即主窗口）："
                            "'main' 主窗口（可见性优先）、'active' 当前前台窗口、"
                            "'#N' 可操作窗口（可见且未最小化）按 Z 序第 N 个"
                            "（1 = 最靠前，如弹出的右键菜单、下拉浮层、对话框；"
                            "N 与 op=windows 清单里的 z_index 一致）、"
                            "'handle:0x1a2b' 按平台窗口句柄、"
                            "'title:子串' / 'class:子串' / 'pid:1234' 按属性匹配、"
                            "'title~:正则' / 'class~:正则' / 're:正则' 按正则匹配"
                            "标题 / 类名、'process:进程名'（如 process:chrome）"
                            "按进程（exe）名匹配、'fuzzy:关键词' 模糊匹配、"
                            "'popup' 弹层 / 浮层（优先无标题的非主窗口；"
                            "WinUI / XAML 有标题的弹出浮层、带属主的弹层、"
                            "tool window 同样命中）、'dialog' 对话框。"
                            "不可见（visible=false）或已最小化的窗口不会被 "
                            "'main' / '#N' / 'popup' / 'dialog' 选中——它们截出来"
                            "是全黑图、输入也打不进去；需要这类窗口时用 "
                            "'handle:0x…' 显式指定。"
                            "先用 op=windows 查看窗口清单（selectable / z_index "
                            "字段），再用同一选择器把截图 / 输入投向任意窗口。"
                            "弹层（清单里 tool_window=true 的右键菜单 / 下拉"
                            "浮层）不参与前台切换，直接把 window 指向它即可用"
                            "合成输入操作（鼠标按屏幕坐标命中光标下的窗口、"
                            "键盘交给同一应用的前台窗口）；"
                            "client_area=false 的窗口不能配合 "
                            "method='message' 使用。"
                        ),
                    },
                    "grid": {
                        "type": ["number", "boolean"],
                        "description": (
                            "仅 screenshot 可选：在截图上叠加等距坐标参考线，值为"
                            "线间距像素（省略 = 不叠加参考线；0 或 true = 按画面"
                            "尺寸自动选步长约 10 格；如 grid=50 每 50 像素一条主线、"
                            "每 25 像素一条次线），便于读图后精确给出 x/y 坐标。"
                        ),
                    },
                    "screen": {
                        "type": ["boolean", "string", "number"],
                        "description": (
                            "仅 screenshot / sequence 的 screenshot 步骤可选："
                            "整屏 / 多显示器截取（不限于目标进程的窗口）。"
                            "true / 'true' / 'all' / '0' = 整个虚拟桌面"
                            "（所有显示器）；"
                            "'primary' = 主显示器；序号（1 / '2'）= 第 N 个"
                            "显示器。省略 / false = 截目标窗口。"
                        ),
                    },
                    "margin": {
                        "type": "number",
                        "description": (
                            "仅 screenshot / sequence 的 screenshot 步骤可选："
                            "与 element 配合，把控件区域向四周外扩的像素数"
                            "（默认 0，可为负表示收缩），用于截控件及其周边一点"
                            "范围。"
                        ),
                    },
                    "shot": {
                        "type": ["string", "boolean"],
                        "description": (
                            "仅输入 op（click/move/drag/scroll/key/type，"
                            "op=keys 走 GUI 窗口时同样支持）可选："
                            "注入完成后自动截图，省去额外一次 screenshot 调用。"
                            "取值为截图路径（无扩展名自动补 .png），或 true 表示"
                            "自动命名到 'bash_opt_shots/' 目录。结果 JSON 的 "
                            "screenshot 字段给出 path/width/height，以及 "
                            "window_x/window_y（截图像素左上角的屏幕坐标）与 "
                            "window_rect（窗口外框）。"
                        ),
                    },
                    "settle": {
                        "type": "number",
                        "description": (
                            "仅输入 op（含 op=keys 走 GUI 窗口时）可选"
                            "（sequence 的步骤也可带）：注入后等待的"
                            "秒数再返回（默认 0；指定 shot 时默认 0.2 秒），用于等"
                            "界面完成刷新，避免截图拍到旧画面。需要「等界面真的变了」"
                            "而不是固定等待时，改用 wait_for='change'。"
                        ),
                    },
                    "window_action": {
                        "type": "string",
                        "enum": ["activate", "maximize", "minimize", "restore",
                                 "close", "move", "resize", "fit",
                                 "always_on_top", "not_on_top",
                                 "get_geometry", "save_geometry",
                                 "restore_geometry"],
                        "description": (
                            "仅 window 操作必填：窗口控制动作。activate 置前激活、"
                            "maximize/minimize/restore 最大化/最小化/还原、"
                            "close 请求关闭（等价点关闭按钮）、move 按屏幕坐标移动"
                            "（需 x/y）、resize 调整尺寸（需 width/height）、"
                            "fit 同时移动并调整尺寸（x/y/width/height 都要）、"
                            "always_on_top/not_on_top 置顶/取消置顶（操作期间防止"
                            "被其它窗口遮挡）、get_geometry 读取当前几何、"
                            "save_geometry 记住当前几何、restore_geometry 恢复"
                            "上次记住的几何（布局固定后再按像素操作更稳）。"
                            "配合 window 选择器指定目标窗口。"
                        ),
                    },
                    "width": {
                        "type": "number",
                        "description": (
                            "仅 window 操作的 resize / fit：目标窗口宽度（像素）。"
                        ),
                    },
                    "height": {
                        "type": "number",
                        "description": (
                            "仅 window 操作的 resize / fit：目标窗口高度（像素）。"
                        ),
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "文件路径：screenshot 必填（截图保存的 PNG；无扩展名"
                            "自动补 .png，父目录不存在会自动创建）；annotate 可"
                            "指定要标注的已有 PNG（省略时从目标窗口现截，需 "
                            "task_id）；locate / pixel 可指定要分析 / 定位的已有 "
                            "PNG（省略时从目标窗口现截，需 task_id）——给 path 时"
                            "无需 task_id，可直接分析 screenshot screen=true 的"
                            "整屏产物或任意图片，此时坐标以图片左上角为原点、"
                            "没有 screen_* 屏幕坐标换算；record / replay 可指定"
                            "宏文件路径（与 macro 二选一）。支持 Windows 盘符路径"
                            "（E:/tmp/a.png、E:\\tmp\\a.png）——Cygwin / MSYS2 下"
                            "自动换算为 /cygdrive/e/tmp/a.png（MSYS2 为 "
                            "/e/tmp/a.png），不会落到工作目录下的 E:/..."
                        ),
                    },
                    "crop": {
                        "type": "string",
                        "description": (
                            "screenshot / locate 可选：窗口内的像素区域，"
                            "格式 'x,y,width,height'（如 '100,50,800,600'），"
                            "以整窗截图左上角为原点（(0,0) 即窗口左上角）；"
                            "locate 给了 path 时以该图片左上角为原点。"
                            "screenshot：只截取该区域，省略时输出整窗原始像素"
                            "（不做任何缩放），区域须完全落在窗口截图内，"
                            "越界会报错并提示窗口实际尺寸；"
                            "locate：把搜索限定在该区域内（template 会先裁剪"
                            "再匹配；query 用识别结果按区域过滤，坐标仍与"
                            "整窗截图同源）。"
                        ),
                    },
                    "x": {
                        "type": ["number", "string"],
                        "description": (
                            "窗口内坐标 X（原点为窗口截图左上角，与 screenshot "
                            "产物一致）。支持像素整数或语义值：'center'/'middle'、"
                            "'left'/'right'、百分比 '50%'、偏移写法"
                            "'center+20'/'center-20'/'left+20'/'right-10'"
                            "（语义值按窗口实际尺寸在注入时换算，窗口缩放也不失准）。"
                            "move 必填；click / scroll 可选，省略则作用于窗口中心；"
                            "drag 用 from_x/from_y 指定起点；也可用 element 按控件名定位。"
                            "pixel 给了 path 时，x / y 以该图片左上角为原点。"
                        ),
                    },
                    "y": {
                        "type": ["number", "string"],
                        "description": (
                            "窗口内坐标 Y（语义值同 x：'center'、'bottom'、'50%'、"
                            "'center-20'、'bottom-30' 等）。与 x 同时提供或同时省略。"
                        ),
                    },
                    "to_x": {
                        "type": ["number", "string"],
                        "description": (
                            "仅 drag：拖动终点的窗口内坐标 X（必填；支持与 x 相同的"
                            "语义值）。"
                        ),
                    },
                    "to_y": {
                        "type": ["number", "string"],
                        "description": "仅 drag：拖动终点的窗口内坐标 Y（必填）。",
                    },
                    "from_x": {
                        "type": ["number", "string"],
                        "description": (
                            "仅 drag 可选：拖动起点的窗口内坐标 X；与 from_y 同时"
                            "省略时从窗口中心按下。给 element 时用控件中心作为起点。"
                        ),
                    },
                    "from_y": {
                        "type": ["number", "string"],
                        "description": "仅 drag 可选：拖动起点的窗口内坐标 Y。",
                    },
                    "button": {
                        "type": "string",
                        "enum": ["left", "right", "middle"],
                        "description": (
                            "仅 click / drag：鼠标按钮（默认 left）。"
                            "right 即右键（右击），middle 为中键。"
                        ),
                    },
                    "count": {
                        "type": "number",
                        "description": (
                            "仅 click：点击次数（默认 1；2 表示双击，最大 100）。"
                            "游戏连点（射击 / 快速选择）可配 interval 控制节奏；"
                            "phase='down'/'up' 分离阶段下恒按一次处理。"
                        ),
                    },
                    "hold": {
                        "type": "number",
                        "description": (
                            "click / key 的长按时长（秒，默认 0 = 不额外保持）。"
                            "省略 / 0 时后端仍会让按下与弹起至少间隔约 0.05 秒"
                            "（逐帧采样的程序，如游戏引擎 XNA/SDL，同一帧内看不到"
                            "按下状态会把整次点击 / 按键吞掉）；>0 时按该时长保持："
                            "click 为「点住不放」（长按拖动 / 蓄力），key 为"
                            "「按住某键一段时间」（游戏持续移动 / 蓄力 / 长按功能"
                            "键）；上限 30 秒。结果里的 press_hold 给出实际按住时长。"
                        ),
                    },
                    "interval": {
                        "type": "number",
                        "description": (
                            "click / key / move 的时间间隔（秒，上限 10）："
                            "click = 多次点击（双击 / 三击）之间的间隔（默认 "
                            "0.05，需小于系统双击时间才会识别为双击）；"
                            "key = 连发（repeat）时相邻两次按键的间隔（默认用"
                            "平台默认约 0.05；游戏连点可设 0.02 加快）；"
                            "move（relative_event） = 相对位移拆步发送时的步间间隔。"
                        ),
                    },
                    "dwell": {
                        "type": "number",
                        "description": (
                            "仅 hover：移动到目标点后的停留时长（秒，默认 0.6，"
                            "上限 30）。用于触发鼠标悬停才出现的 tooltip / 悬浮"
                            "菜单 / 延迟加载。"
                        ),
                    },
                    "dx": {
                        "type": "number",
                        "description": (
                            "仅 move：相对当前光标屏幕位置的水平像素偏移"
                            "（可为负）。与 x/y 互斥——相对移动给 dx/dy，绝对"
                            "移动给 x/y。加 relative_event=true 可改为发送纯"
                            "相对位移事件（游戏视角）。"
                        ),
                    },
                    "dy": {
                        "type": "number",
                        "description": (
                            "仅 move：相对当前光标屏幕位置的垂直像素偏移"
                            "（可为负）。需与 dx 同时提供（不移动的轴给 0）；"
                            "加 relative_event=true 可改为发送纯相对位移事件。"
                        ),
                    },
                    "modifiers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "注入期间按住的修饰键，取值 ctrl / alt / shift / meta"
                            "（可多个）：move/click/drag/scroll 表示按住这些键执行动作"
                            "（如 ['ctrl'] 表示 Ctrl+点击）；key 动作会与 key 参数"
                            "里的组合键合并（两种写法等价）。"
                        ),
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down", "left", "right"],
                        "description": (
                            "仅 scroll：滚动方向（默认 down，向下滚动查看后续内容）。"
                        ),
                    },
                    "amount": {
                        "type": "number",
                        "description": (
                            "仅 scroll：滚动量（默认 3，即约 3 行/格；最大 100）。"
                        ),
                    },
                    "duration": {
                        "type": "number",
                        "description": (
                            "drag / move 可选：动作耗时秒数。"
                            "drag 默认 0.3（0 表示瞬时；需要目标程序识别"
                            "连续移动时调大，最大 10）；move 默认 0（一步直达），"
                            ">0 时在起点与终点之间按 duration 分步平滑移动。"
                        ),
                    },
                    "steps": {
                        "type": "number",
                        "description": (
                            "drag / move 可选：轨迹插值步数。"
                            "drag 默认 20（范围 2-200）；move 默认 1（不插值），"
                            ">1 时按该步数插值分步移动（配合 duration 控制节奏），"
                            "范围 2-200。某些程序（游戏 / 拖选 / 悬停菜单）只"
                            "响应连续移动事件，此时用平滑移动。"
                        ),
                    },
                    "method": {
                        "type": "string",
                        "enum": ["auto", "sendinput", "message"],
                        "description": (
                            "仅 Windows 输入注入可选（其它平台忽略）："
                            "auto（默认，优先合成真实输入事件，无法取得前台时回退消息投递）、"
                            "sendinput（强制合成真实输入事件，需要目标窗口可被置前）、"
                            "message（直接投递 WM_* 窗口消息，不移动真实光标、不需要焦点，"
                            "但目标程序必须处理这些消息）。"
                        ),
                    },
                    "phase": {
                        "type": "string",
                        "enum": ["press", "down", "up"],
                        "description": (
                            "key / click 操作的阶段（默认 press）。"
                            "press（按下并弹起，一次完整按键 / 点击）、"
                            "down（只发送「按下」消息，按住不放）、"
                            "up（只发送「弹起」消息）。"
                            "click 用 down/up 分离可实现「按住左键射击 / 拖框 / "
                            "按住右键瞄准」等跨调用保持按键的游戏操作——配合 "
                            "op=release（或 key/click 的 up 阶段）释放，避免卡键。"
                            "按下与弹起在各平台分别独立发送（Linux 用 xdotool "
                            "keydown/keyup、Windows 用 WM_KEYDOWN/WM_KEYUP 或 "
                            "WM_SYSKEY*/SendInput、macOS 用 Quartz 按键事件）；"
                            "key='ctrl' 可单独按下/弹起修饰键本身。"
                        ),
                    },
                    "repeat": {
                        "type": "number",
                        "description": (
                            "key / keys 操作可选：连按次数（默认 1，最大 100）。"
                            "一次调用连续发送 N 次完整按键，每次之间留固定间隔；"
                            "仅在 phase='press'（默认）时生效，down/up 的长按"
                            "阶段忽略该参数。用于「连按多次」场景（如连按 F12 "
                            "开关调试工具），减少多次调用与中途失焦导致的漏按。"
                        ),
                    },
                    "relative_event": {
                        "type": "boolean",
                        "description": (
                            "仅 move 的相对移动（给了 dx/dy）可选：true 时发送"
                            "**纯相对鼠标位移事件**（不把光标定位到绝对位置）。"
                            "第一人称 / 第三人称游戏读取的正是这种相对位移，且"
                            "不受游戏 ClipCursor（把光标锁到窗口中心）影响——"
                            "旋转视角 / 转镜头请用它。steps 把总位移拆成多个"
                            "等分事件（引擎常忽略单次超大位移），interval 控制"
                            "步间间隔。"
                        ),
                    },
                    "hold_keys": {
                        "type": ["string", "array"],
                        "items": {"type": "string"},
                        "description": (
                            "动作期间额外按住的任意键（点击类 / 移动 / 拖动 / 滚动 / "
                            "hover / key / type 都可用）：字符串（如 'w' 或 "
                            "'w+shift'）或数组（如 ['w', 'shift']），每项是单个"
                            "键（修饰键或普通键）。用于游戏组合键——按住 W 前进的"
                            "同时点击 / 转视角，按住 Shift 跑动等。与 modifiers "
                            "的区别：modifiers 只接受 ctrl/alt/shift/meta，"
                            "hold_keys 接受任意键。"
                        ),
                    },
                    "keys": {
                        "type": ["string", "array"],
                        "items": {"type": "string"},
                        "description": (
                            "仅 release 可选：要释放的键（字符串或数组）；留空 = "
                            "释放本次会话记录的全部已按下键。release 用于清理 "
                            "key/click 的 phase='down' 留下的悬空按下状态。"
                        ),
                    },
                    "buttons": {
                        "type": ["string", "array"],
                        "items": {"type": "string"},
                        "description": (
                            "仅 release 可选：要释放的鼠标按钮（left/right/"
                            "middle，字符串或数组）；留空 = 释放全部已按下的"
                            "鼠标按钮。"
                        ),
                    },
                    "actions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "op": {
                                    "type": "string",
                                    "description": (
                                        "步骤类型：click / move / drag / scroll / "
                                        "key / type / wait / screenshot / window"
                                    ),
                                },
                            },
                        },
                        "description": (
                            "仅 sequence 必填：步骤数组（最多 50 步），每项是一个"
                            "对象，用 op 指定类型："
                            "\n- 输入动作：click / move / drag / scroll / key / type"
                            "（参数与单独调用时相同，另可带 element / via / paste_key /"
                            " restore_clipboard / newline / settle / shot / window /"
                            " wait_for / diff / tolerance）；"
                            "\n- wait：{\"op\": \"wait\", \"seconds\": 0.5} 等待指定秒数；"
                            "\n- screenshot：{\"op\": \"screenshot\", \"path\": \"a.png\"}"
                            " 截图存盘（可选 crop / grid）；"
                            "\n- window：{\"op\": \"window\", \"window_action\": "
                            "\"always_on_top\"} 窗口控制（可选 x/y/width/height）。"
                            "未指定 window 的步骤继承本次调用的 window 选择器；"
                            "适合把「点击输入框 → 输入文本 → 回车 → 截图」一条调用完成。"
                        ),
                    },
                    "on_error": {
                        "type": "string",
                        "enum": ["stop", "continue"],
                        "description": (
                            "仅 sequence / replay 可选：步骤失败时的策略（默认 stop）。"
                            "stop = 立即停止并把已完成步骤与错误一起返回；"
                            "continue = 跳过失败步骤继续执行后续步骤"
                            "（每步的 ok / error 都会如实回报）。"
                        ),
                    },
                    "clipboard_action": {
                        "type": "string",
                        "enum": ["set", "get", "clear", "append"],
                        "description": (
                            "仅 clipboard 可选：set 写入 text、get 读取当前文本、"
                            "clear 清空、append 在原内容后追加 text。"
                            "省略时按是否提供 text 推断（给了 text 即 set，"
                            "否则 get）。"
                        ),
                    },
                    "element": {
                        "type": "string",
                        "description": (
                            "按控件名定位（推荐先用 op=elements 看清单），两种用法："
                            "\n- 输入 op（click / move / scroll / drag / type / key）："
                            "click/move/scroll 用控件中心作为坐标、drag 用控件中心"
                            "作为起点；type/key 先点击该控件聚焦再输入。"
                            "与显式 x/y（drag 的 from_x/from_y）互斥；"
                            "\n- op=elements：作为过滤子串，只返回匹配的控件。"
                            "取值形式：'确定'（按控件文本子串，找不到再按自动化 "
                            "ID、类名、控件类型）、'text:子串'、'class:子串'、"
                            "'id:子串'（自动化 ID / 控件名，如 WinForms 的 "
                            "TextBox.Name）、'type:edit'、'#3'（清单第 3 个控件；"
                            "op=elements 的过滤同样接受 '#N' 写法，与输入 op "
                            "的定位一致）；"
                            "类型匹配同时接受英文类型名与中文标签"
                            "（'edit' / '编辑框'、'button' / '按钮'、'list' / '列表'）。"
                        ),
                    },
                    "via": {
                        "type": "string",
                        "enum": ["typing", "clipboard"],
                        "description": (
                            "仅 type 可选：输入方式（默认 typing）。"
                            "typing = 逐字符合成按键（走 Unicode 注入，不受"
                            "输入法影响）；"
                            "clipboard = 把文本写入系统剪贴板后发送粘贴键"
                            "（默认 ctrl+v，macOS 为 command+v），"
                            "长文本 / 中文 / emoji / 多行文本不会掉字，"
                            "也不会被目标程序误当成快捷键；"
                            "粘贴后默认恢复原剪贴板内容（会先留出片刻让目标程序"
                            "读取剪贴板，避免浏览器 / Electron 粘出旧内容）。"
                        ),
                    },
                    "paste_key": {
                        "type": "string",
                        "description": (
                            "仅 type via='clipboard' 可选：粘贴组合键"
                            "（默认按平台取 ctrl+v / command+v）。"
                            "个别程序需要 shift+insert 等其它粘贴键时可显式指定。"
                        ),
                    },
                    "restore_clipboard": {
                        "type": "boolean",
                        "description": (
                            "仅 type via='clipboard' 可选：粘贴后是否恢复原剪贴板"
                            "内容（默认 true，避免破坏用户剪贴板）。恢复前会等待"
                            "片刻，给目标程序读取剪贴板的时间——浏览器 / Electron / "
                            "远程桌面的粘贴是异步处理，恢复太快会把**旧内容**粘进去"
                            "（表现为「粘出来的还是上一次的剪贴板文本」）；不需要"
                            "保留原剪贴板时传 false 更稳，也少一次剪贴板读写。"
                        ),
                    },
                    "wait_for": {
                        "type": "string",
                        "description": (
                            "仅输入 op（含 op=keys 走 GUI 窗口时）可选："
                            "注入后等待界面满足条件再返回。"
                            "'change' 等待画面发生变化（点击后等界面刷新，"
                            "变化区域会一并回报）；"
                            "'stable' 等待画面稳定（动画 / 加载结束；连续两次"
                            "采样一致即算稳定，光标闪烁 / 时钟这类微小噪声会被"
                            "忽略，并在结果的 ignored_change 里说明）；"
                            "也可传秒数（如 '0.5'，等价于增强版 settle）。"
                            "超时由 wait_timeout 控制（默认 5 秒）；"
                            "无法判定时 satisfied 为 null 并附 reason。"
                        ),
                    },
                    "wait_timeout": {
                        "type": "number",
                        "description": (
                            "仅输入 op（含 op=keys 走 GUI 窗口时）可选："
                            "wait_for 的超时秒数（默认 5，"
                            "上限 120）。超时返回 satisfied=false，"
                            "不会阻塞后续操作。"
                        ),
                    },
                    "diff": {
                        "type": "boolean",
                        "description": (
                            "仅输入 op（含 op=keys 走 GUI 窗口时）可选："
                            "true 时注入前后各截一张图并比较像素"
                            "差异，结果放在返回 JSON 的 diff 字段"
                            "（changed / changed_ratio / region / summary），"
                            "用于确认操作是否真的让界面发生了变化。"
                            "与 wait_for='change' 同时使用时会复用同一张基准图。"
                        ),
                    },
                    "tolerance": {
                        "type": "number",
                        "description": (
                            "输入 op（含 op=keys 走 GUI 窗口时）的 diff / "
                            "op=locate 可选：像素比较的"
                            "颜色容差（每通道 0..255）。diff 默认 8（0 表示"
                            "要求像素完全一致，界面有抗锯齿 / 淡入淡出动画时"
                            "可调大）；locate 模板匹配默认 25（模板与截图"
                            "比例一致时可用较小值）。"
                        ),
                    },
                    "max_elements": {
                        "type": "number",
                        "description": (
                            "仅 elements 可选：控件清单最多返回多少条"
                            "（默认 200，上限 2000）。控件很多时按需缩小以"
                            "保持输出简洁。"
                        ),
                    },
                    "template": {
                        "type": "string",
                        "description": (
                            "仅 locate：模板图片路径（PNG）。在截图（或 path "
                            "指定的已有图片）里搜索该小图（图标 / 按钮局部），"
                            "返回其位置与置信度；与 query 二选一。"
                        ),
                    },
                    "query": {
                        "type": "string",
                        "description": (
                            "仅 locate：要在画面里查找的文字（OCR；画面 = "
                            "窗口截图或 path 指定的已有图片）。"
                            "返回匹配文字的包围盒坐标（支持跨词短语）；"
                            "与 template 二选一。也可用 text 传该查询。"
                            "精确匹配失败时自动做近似匹配：折叠 OCR 易混字符"
                            "（l/I/1、O/0、S/5 等）并忽略下划线 / 破折号 / "
                            "点号 / 空格等分隔符（实测 bash_opt 会被识别成 "
                            "bash—opt），命中时 match_mode='fuzzy' 并提示坐标"
                            "需复核；中文查询在字符折叠仍失败时按整行文本"
                            "相似度（>= 80%）兜底，match_mode='similar'（OCR "
                            "把「钮」认成「田」这类错字），同样提示复核；"
                            "未命中时会附带 recognized_texts（识别到的"
                            "文字样本），便于判断是 OCR 识别偏差还是画面里确实"
                            "没有该文字；crop 可把命中范围限定在指定区域内。"
                            "OCR 语言可用 ocr_lang 指定。"
                        ),
                    },
                    "ocr_lang": {
                        "type": "string",
                        "description": (
                            "仅 locate 的 OCR 文字定位可选：OCR 语言标签"
                            "（BCP-47，如 'zh-Hans-CN' / 'en-US' / 'ja-JP'）。"
                            "缺省按用户配置语言（Windows 系统 OCR 的 "
                            "TryCreateFromUserProfileLanguages）；系统没装该语言"
                            "的 OCR 语言包时返回可读错误。Windows 可用 "
                            "OcrEngine.AvailableRecognizerLanguages 查询本机"
                            "可用语言；其它平台按 tesseract 语言包名传入。"
                        ),
                    },
                    "max_results": {
                        "type": "number",
                        "description": (
                            "仅 locate：最多返回多少条匹配（默认 10，上限 50）。"
                        ),
                    },
                    "min_scale": {
                        "type": "number",
                        "description": (
                            "仅 locate 模板匹配：模板最小缩放比例（默认 1.0）。"
                            "模板与截图像素比例不一致时可设 0.5~2.0 搜索。"
                        ),
                    },
                    "max_scale": {
                        "type": "number",
                        "description": (
                            "仅 locate 模板匹配：模板最大缩放比例（默认 1.0，"
                            "需 >= min_scale）。"
                        ),
                    },
                    "scale_steps": {
                        "type": "number",
                        "description": (
                            "仅 locate 模板匹配：在 min_scale..max_scale 之间"
                            "取多少个缩放档（默认 1，上限 21）。"
                        ),
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["point", "region", "find"],
                        "description": (
                            "仅 pixel 可选：取色模式。"
                            "point（默认，需 x/y）读该像素颜色；"
                            "region（可选 region，省略时统计整窗 / 整图——"
                            "窗口截图或 path 图片）统计区域均值 / 极值 / 主色；"
                            "find（需 color）在整图（或 region）内查找目标颜色，"
                            "按连通块返回多个候选位置（可点击）。"
                        ),
                    },
                    "color": {
                        "type": "string",
                        "description": (
                            "仅 pixel / annotate 使用：颜色。"
                            "支持 '#RRGGBB'、'rgb(r,g,b)'、'r,g,b' 或颜色名"
                            "（如 'red'）。pixel 里作为「期望色 / 查找目标」"
                            "（给 color 时点 / 区域取色会附带是否匹配的结论）；"
                            "annotate 里作为标记颜色（缺省红色）。"
                        ),
                    },
                    "region": {
                        "type": "string",
                        "description": (
                            "仅 pixel 使用：取色 / 查找区域（'x,y,width,height'，"
                            "窗口截图坐标，原点为窗口截图左上角；给了 path 时"
                            "以该图片左上角为原点）；"
                            "省略时：point 模式下用 x/y，find 模式下扫描整图。"
                        ),
                    },
                    "min_pixels": {
                        "type": "number",
                        "description": (
                            "仅 pixel 的 find 模式：连通块的最小像素数"
                            "（默认 1），用于过滤噪点。"
                        ),
                    },
                    "max_regions": {
                        "type": "number",
                        "description": (
                            "仅 pixel 的 find 模式：最多返回多少块候选区域"
                            "（默认 10，上限 100）。"
                        ),
                    },
                    "output": {
                        "type": "string",
                        "description": (
                            "仅 annotate 可选：标注结果输出路径（PNG）。"
                            "省略时覆盖 path 指定的原图；无扩展名自动补 .png。"
                        ),
                    },
                    "boxes": {
                        "type": "array",
                        "items": {"type": ["string", "object", "array"]},
                        "description": (
                            "仅 annotate：矩形标注列表，每项为 "
                            "'x,y,width,height'、[x,y,w,h] 或 "
                            "{x,y,width,height}；坐标以图像左上角为原点。"
                        ),
                    },
                    "points": {
                        "type": "array",
                        "items": {"type": ["string", "object", "array"]},
                        "description": (
                            "仅 annotate：点标注列表，每项为 'x,y'、[x,y] 或 "
                            "{x,y}；绘制为十字 + 中心点。"
                        ),
                    },
                    "labels": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "仅 annotate：与 boxes + points 顺序对应的标签文本"
                            "（5x7 点阵，常用数字 / 字母）；省略时自动编号。"
                        ),
                    },
                    "macro": {
                        "type": "string",
                        "description": (
                            "record / replay 的宏名（保存到 "
                            "'bash_opt_macros/<宏名>.json'）。replay 时按名加载；"
                            "record 时保存/追加到该名字。"
                        ),
                    },
                    "times": {
                        "type": "number",
                        "description": (
                            "仅 replay 可选：整段宏重复执行次数（默认 1，"
                            "上限 20）。"
                        ),
                    },
                    "append": {
                        "type": "boolean",
                        "description": (
                            "仅 record 可选：同名宏已存在时，把新步骤追加到"
                            "末尾（默认 false = 覆盖）。"
                        ),
                    },
                },
                "required": ["op"],
            },
        },
    }


__all__ = ["build_bash_opt_schema"]
