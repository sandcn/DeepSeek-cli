"""help_view — HelpView 帮助速查视图（模态全屏视图，2026-10-07）。

**F1** 或 **Ctrl+/** 打开/关闭：App 在 ``model.fullscreen == "help"`` 时经
全屏视图注册表 **整屏只渲染本组件**（消息区/顶部标题栏/状态栏/输入区全部
不显示），关闭后恢复完整聊天界面。

布局（单栏滚动）：
  - 头部行：标题 + 统计（命令数/快捷键数）+ 操作提示 + ``─`` 填充至满宽；
  - 内容区：分组命令列表（分组标题行 + 命令行，名称列对齐、描述分色、
    别名弱化）+ 空行 + 快捷键速查（两列按显示宽度对齐）；视口按终端高度
    自适应，滚动窗口只渲染可见行；
  - 光标行整行背景高亮（vim cursorline 语义）。

键盘：
  - ↑↓/jk 移动 · PgUp/PgDn 翻页 · Home/End 或 g/G 首末；
  - Esc / Ctrl+H / F1 / Ctrl+/ 关闭（其余按键被模态吞掉，不落入输入缓冲）。

数据源：核心命令插件注册表（``<command_core>._iter_help_commands``——分组/
描述/别名）与 ``SHORTCUT_ROWS``（快捷键单一真源）——与 ``/help`` 文本输出
同源，避免两处漂移。依赖约束：仅依赖 app 同层（_modal_view）与 ink 框架
（Layer 0/1）；核心命令数据经函数内延迟 import（避免模块级循环依赖）。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui.ink import TEXT, Column, StyledRun, h, use_input, use_memo, use_state
from src.tui.ink.helpers import truncate_runs
from src.tui._screen import wcswidth_simple

from ._modal_view import empty_modal_frame, is_modal_close_key, use_modal_scope

__all__ = ["HelpView", "_help_rows"]

# ── 样式（静态色——浏览界面，不呼吸，diff 零输出） ──
_S_TITLE = Style(fg=45, bold=True)     # 视图标题（亮青加粗）
_S_HINT = Style(fg=242)                # 提示/统计（暗灰）
_S_SEP = Style(fg=238)                 # 分隔线（深灰）
_S_GROUP = Style(fg=110, bold=True)    # 分组标题（浅蓝加粗）
_S_CMD = Style(fg=45)                  # 命令名（亮青）
_S_DESC = Style(fg=252)                # 描述/说明（亮白）
_S_ALIAS = Style(fg=242)               # 别名（暗灰）
_S_KEY = Style(fg=214)                 # 快捷键键名（橙黄）
_S_CUR_BG = Style(bg=237)              # 光标行背景

#: 命令名称列最小/最大宽度（与 /help 文本输出同口径）。
_CMD_COL_MIN = 8
_CMD_COL_MAX = 20
#: 头部预留行数（视图内容可见行 = 终端高 - 头部 1 行）。
_VIEWPORT_RESERVED = 1


def _viewport_rows() -> int:
    """内容区可见行数（终端高度自适应；无高度上下文回退 16）。"""
    try:
        from src.tui._screen import TerminalWidthCache

        h_ = TerminalWidthCache.get_default().get_height()
        return max(4, int(h_) - _VIEWPORT_RESERVED)
    except Exception:
        return 16


def _group_header(label: str, width: int) -> list:
    """分组标题行：``── 标题 `` + ``─`` 填充至满宽。"""
    text = f"\u2500\u2500 {label} "
    pad = max(0, width - _disp_len(text)) if width > 0 else 0
    runs = [StyledRun(text, _S_GROUP)]
    if pad:
        runs.append(StyledRun("\u2500" * pad, _S_SEP))
    return runs


def _disp_len(text: str) -> int:
    """文本显示宽度（东亚宽/全角按 2 列）。"""
    try:
        return wcswidth_simple(text)
    except Exception:
        return len(text)


def _help_rows(width: int) -> list:
    """构建帮助速查全量内容行（``list[list[StyledRun]]``）。

    Args:
        width: 行宽预算（>0 时每行截断至该宽度，保持行级 diff 宽度不变量）。

    Returns:
        内容行列表——空行以单个空格 run 表示（保证占一行）。
    """
    from src.core.internal.commands._command_core import (
        GROUP_LABELS,
        GROUP_ORDER,
        SHORTCUT_ROWS,
        _iter_help_commands,
    )

    entries = _iter_help_commands()
    groups: dict = {}
    for group, name, desc, aliases in entries:
        groups.setdefault(group, []).append((name, desc, aliases))

    rows: list = [[StyledRun(" ", None)]]
    if groups:
        order = [g for g in GROUP_ORDER if g in groups]
        order += [g for g in sorted(groups) if g not in GROUP_ORDER]
        cmd_w = max(len("/" + n) for items in groups.values() for n, _, _ in items)
        cmd_w = max(_CMD_COL_MIN, min(cmd_w, _CMD_COL_MAX))
        for group in order:
            rows.append(_group_header(GROUP_LABELS.get(group, group), width))
            for name, desc, aliases in sorted(groups[group], key=lambda t: t[0]):
                cmd = "/" + name
                pad = " " * (cmd_w - len(cmd) + 2)
                runs = [
                    StyledRun("  " + cmd, _S_CMD),
                    StyledRun(pad, None),
                    StyledRun(desc, _S_DESC),
                ]
                if aliases:
                    runs.append(StyledRun(
                        "  " + " ".join("/" + a for a in aliases), _S_ALIAS,
                    ))
                rows.append(runs)
        pad_exit = " " * (cmd_w - len("exit") + 2)
        rows.append([
            StyledRun("  exit", _S_CMD),
            StyledRun(pad_exit, None),
            StyledRun("退出", _S_DESC),
        ])
    else:
        rows.append([StyledRun("  (暂无可用命令)", _S_HINT)])

    rows.append([StyledRun(" ", None)])
    rows.append(_group_header("快捷键", width))
    key_w = max((len(k) for row in SHORTCUT_ROWS for k, _ in row), default=0)
    left_w = 0
    for row in SHORTCUT_ROWS:
        key, desc = row[0]
        left_w = max(left_w, key_w + 3 + _disp_len(desc))
    for row in SHORTCUT_ROWS:
        runs: list = []
        for idx, (key, desc) in enumerate(row):
            pad = " " * (key_w - len(key) + 3)
            if idx == 0:
                gap = left_w - (key_w + 3 + _disp_len(desc)) + 2
                runs.append(StyledRun("  ", None))
                runs.append(StyledRun(key, _S_KEY))
                runs.append(StyledRun(pad, None))
                runs.append(StyledRun(desc, _S_DESC))
                runs.append(StyledRun(" " * max(2, gap), None))
            else:
                runs.append(StyledRun(key, _S_KEY))
                runs.append(StyledRun(pad, None))
                runs.append(StyledRun(desc, _S_DESC))
        rows.append(runs)

    if width > 0:
        rows = [truncate_runs(r, width) if r else r for r in rows]
    return rows


def _is_help_close_key(event) -> bool:
    """帮助视图关闭键：Esc / Ctrl+H / F1 / Ctrl+/。"""
    if is_modal_close_key(event):
        return True
    kind = getattr(event, "kind", "")
    if kind == "f1":
        return True
    return kind == "ctrl_key" and getattr(event, "char", "") == "\x1f"


def HelpView(props) -> object:
    """帮助速查视图组件（模态全屏视图；App 按 FULLSCREEN_VIEWS 整屏渲染）。

    Props:
        model: AppModel 实例（读 ``model.fullscreen``）。
        width: 终端宽度（行宽预算）。
    """
    model = props["model"]
    width = props.get("width", 0) or 0
    visible = getattr(model, "fullscreen", "") == "help"

    rows = use_memo(lambda: _help_rows(width), (width,))
    total = len(rows)
    vh = _viewport_rows()
    cursor, set_cursor = use_state(0)
    scroll, set_scroll = use_state(0)

    # 渲染期局部归一化（不写 state——渲染保持纯函数；真实状态由事件处理器钳制）
    cur = max(0, min(int(cursor or 0), total - 1)) if total else 0
    vh = max(1, vh)
    max_scroll = max(0, total - vh)
    off = max(0, min(int(scroll or 0), max_scroll))
    if cur < off:
        off = cur
    elif cur >= off + vh:
        off = cur - vh + 1

    def _move_to(new_cursor: int) -> None:
        new_cursor = max(0, min(new_cursor, total - 1)) if total else 0
        set_cursor(new_cursor)
        # 保持光标可见（同步滚动窗口）
        if new_cursor < off:
            set_scroll(new_cursor)
        elif new_cursor >= off + vh:
            set_scroll(new_cursor - vh + 1)

    def _handle(event) -> bool:
        if not visible:
            return False
        if _is_help_close_key(event):
            model.fullscreen = ""
            return True
        kind = getattr(event, "kind", "")
        ch = getattr(event, "char", "") or ""
        if kind == "arrow_up" or (kind == "char" and ch == "k"):
            _move_to(cur - 1)
            return True
        if kind == "arrow_down" or (kind == "char" and ch == "j"):
            _move_to(cur + 1)
            return True
        if kind == "page_up":
            _move_to(cur - vh)
            return True
        if kind == "page_down":
            _move_to(cur + vh)
            return True
        if kind == "home" or (kind == "char" and ch == "g"):
            _move_to(0)
            return True
        if kind == "end" or (kind == "char" and ch == "G"):
            _move_to(total - 1)
            return True
        return False

    use_input(_handle, visible)
    # 模态全屏视图声明：visible 期间未消费按键被 input router 吞掉（字符/Enter
    # 不落入输入缓冲）；关闭后（visible=False）hook 不激活零影响。
    use_modal_scope(visible)

    if not visible:
        return empty_modal_frame()

    # ── 头部（标题 + 统计 + 提示 + ─ 填充至满宽） ──
    cmd_count = sum(1 for runs in rows if runs and runs[0].style is _S_CMD)
    shortcut_count = _shortcut_count()
    header_runs = [
        StyledRun("\u258d帮助速查", _S_TITLE),
        StyledRun(f" \u00b7 {cmd_count} \u547d\u4ee4 \u00b7 {shortcut_count} \u5feb\u6377\u952e", _S_HINT),
        StyledRun("  \u2191\u2193/jk \u9009\u62e9 \u00b7 PgUp/PgDn \u7ffb\u9875 \u00b7 g/G \u9996\u672b \u00b7 Esc/F1 \u5173\u95ed", _S_HINT),
    ]
    if width > 0:
        header_runs = truncate_runs(header_runs, width)
        used = sum(getattr(r, "width", 1) for r in header_runs)
        pad = width - used
        if pad > 1:
            header_runs.append(StyledRun(" " + "\u2500" * (pad - 1), _S_SEP))

    children: list = [
        h(TEXT, {"styled": header_runs, "height": 1, "key": "hv-header"}),
    ]
    window = rows[off:off + vh]
    for i, runs in enumerate(window):
        abs_idx = off + i
        line_runs = runs if runs else [StyledRun(" ", None)]
        if abs_idx == cur:
            line_runs = [
                StyledRun(r.text, (r.style or Style()).merge(_S_CUR_BG))
                for r in line_runs
            ]
        children.append(h(TEXT, {
            "styled": line_runs, "height": 1, "key": f"hv-{abs_idx}",
        }))
    return h(Column, None, children)


def _shortcut_count() -> int:
    """快捷键条目数（头部统计用；数据源与 /help 同源）。"""
    from src.core.internal.commands._command_core import SHORTCUT_ROWS

    return sum(len(row) for row in SHORTCUT_ROWS)
