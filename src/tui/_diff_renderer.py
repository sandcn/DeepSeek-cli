"""
差异渲染模块 — 从 src/tui/consumer/diff_renderer.py 迁移至 TUI 根层级。

职责：文件差异的终端渲染，包括行内高亮、语法高亮、上下文折叠。

迁移说明（2026-07-29 TUI 重构）：
  - 从 src/tui/consumer/diff_renderer.py 迁移至此
  - 导入路径更新为使用 .core.style / ._locks / .events.consumers
  - 外部调用方通过 src/tui._diff_renderer 或 src/tui.consumer 的 re-export 访问
"""

from __future__ import annotations

import logging
import os
import re
import difflib
from functools import lru_cache
from typing import Optional, TYPE_CHECKING

from .core.style import Style, StyleSheet
from src.renderer._locks import diff_active, _try_acquire_output_lock
from .events.consumers import publish_output
# 方向1 步骤2（ANSI 单一工具）：消毒复用统一 ``ink.helpers.strip_ansi``
# 主真源（本文件不再定义独立正则；先剥离合法序列 + 兜底移除孤立 ESC）。
from src.tui.ink.helpers import strip_ansi

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ._output_target import IOutputTarget

# 行内差异背景色（256 色，使用 Style）
# ★ 标准 React Ink 组件化（2026-08-05）：ANSI 背景色直拼迁移为 Style 对象
#   （Style 支持 bg——旧注释「因 Style 不支持 bg only」过时）。渲染统一经
#   ``Style.apply``，不再手动拼接 ANSI 序列。旧常量保留为兼容 re-export
#   （既有测试/外部调用面）；生产路径经 ``_bg_del``/``_bg_add`` Style。
_BG_RED = '\033[48;5;124m'    # 256色暗红背景（兼容 re-export；生产用 _bg_del）
_BG_GREEN = '\033[48;5;28m'   # 256色柔和绿背景（兼容 re-export；生产用 _bg_add）
_BG_OFF = '\033[49m'          # 重置为默认背景色（兼容 re-export）

# ★ P3-13（兼容死代码）：_BG_RED/_BG_GREEN/_BG_OFF/_RESET_STR 生产路径零引用
#   （渲染统一经 ``Style.apply``）——保留仅为兼容 re-export（既有测试/外部
#   调用面）。移除计划：在 __all__ 标注 deprecated 后，待确认外部调用方清空
#   后删除（勿在生产代码新增引用）。

#: 行内删除段背景 Style（暗红 bg=124）
_bg_del = Style(bg=124)
#: 行内新增段背景 Style（柔和绿 bg=28）
_bg_add = Style(bg=28)

# 分隔线默认宽度（方向1 P1：diff 摘要/多 hunk 分隔线固定 40 → 提取常量，
# 窄终端调用方传收缩宽度 min(40, max(10, ...))，默认 40 行为不变）
_SEPARATOR_WIDTH = 40

# 语义色常量引用（从 StyleSheet 获取，兜底硬编码确保任何加载顺序下都有默认值）
_DIFF_ADD_STYLE: Style = StyleSheet.get("diff_add") or Style(fg=41)
_DIFF_DEL_STYLE: Style = StyleSheet.get("diff_del") or Style(fg=196)
_DIFF_CTX_STYLE: Style = StyleSheet.get("diff_ctx") or Style(fg=244)

# ── 美化专用样式常量（diff 渲染局部样式，不注册 StyleSheet 避免影响其他模块） ──
_DIFF_FILE_OLD: Style = Style(fg=210, bold=True)    # 旧文件头：亮红（旧文件标识）
_DIFF_FILE_NEW: Style = Style(fg=114, bold=True)    # 新文件头：亮绿（新文件标识）
_DIFF_HUNK_BAR: Style = Style(fg=45, dim=True)      # hunk 头装饰条：柔青
_DIFF_NUM_DEL:  Style = Style(fg=167)               # 删除行号列：柔红（避免 196 过刺眼）
_DIFF_NUM_ADD:  Style = Style(fg=41)                # 新增行号列：绿
_DIFF_MARK_DEL: Style = Style(fg=196, bold=True)    # `-` 标记：亮红加粗
_DIFF_MARK_ADD: Style = Style(fg=41, bold=True)     # `+` 标记：亮绿加粗

# 向后兼容常量（从 StyleSheet.get() 获取语义色，兜底硬编码值）
# ★ 标准 React Ink 组件化（2026-08-05）：_RESET_STR 已无生产引用
#   （渲染统一经 Style.apply 自带 reset）；保留定义供外部兼容导入。
_RESET_STR = "\033[0m"


@lru_cache(maxsize=64)
def _resolve_lexer_name(ext: str) -> str:
    """将文件扩展名转为安全的 Pygments lexer 名称，未知扩展默认用 text。

    BUG-T8：缓存有界（64）——扩展名集合有限，maxsize=64 足够且防无限增长。
    """
    if not ext:
        return "text"
    # 已知 Pygments 不支持的别名 → 直接映射到 text
    _UNSUPPORTED = {"txt", "text"}
    if ext.lower() in _UNSUPPORTED:
        return "text"
    return ext


@lru_cache(maxsize=64)
def _get_highlighter(lexer_name):
    """获取或缓存 pygments lexer + formatter，未知 lexer 自动降级到 text。

    方向4：``lru_cache`` 缓存（lexer_name → (lexer, formatter)）——pygments
    lexer/formatter 无状态可安全复用；``_resolve_lexer_name`` 已 lru_cache，
    组合后热点路径（每行高亮）免重建（修复前每次调用重建 lexer+formatter）。
    """
    try:
        from pygments.lexers import get_lexer_by_name
        from pygments.formatters import TerminalFormatter
    except ImportError:
        return None

    candidates = [_resolve_lexer_name(lexer_name), "text"]
    seen = set()
    for name in candidates:
        if name in seen:
            continue
        seen.add(name)
        try:
            lexer = get_lexer_by_name(name, stripnl=False, ensurenl=False)
            formatter = TerminalFormatter()
            return lexer, formatter
        except Exception:
            _logger.debug("lexer %s 加载失败", name, exc_info=True)
            continue
    return None


def _sanitize_ansi(text: str) -> str:
    """移除字符串中所有 ANSI 转义序列（复用统一 ``ink.helpers.strip_ansi`` 主真源）。

    方向1 步骤2：改为基于 ``strip_ansi`` 的窄包装——先剥离合法 ANSI 序列
    （SGR/光标/OSC），再兜底移除残留的孤立 ESC 字符（strip_ansi 不匹配的
    非法/残缺序列）——与历史「移除所有 \\x1b」的安全兜底语义一致：任何
    \\x1b 都不进入终端（防注入）。语法高亮生成的 ANSI 序列在本函数之后
    执行，不受影响。

    Args:
        text: 可能含 ANSI 转义序列的输入字符串。

    Returns:
        不含任何 \\x1b 字符的安全字符串。
    """
    return strip_ansi(text).replace("\x1b", "")


def _syntax_hl(text, lexer_name):
    """对单行文本做语法高亮（输入先做 ANSI 消毒防注入）。

    方向A 步骤3 修复：消毒移动到提前 return 之前——空 lexer（无语法高亮）
    时输入含恶意 ANSI 也必须消毒后返回（修复前空 lexer 提前返回原文，
    ANSI 注入窗口存在于单行 add/del 路径）。
    """
    # 先消毒再返回/传 pygments，防止 ANSI 注入
    text = _sanitize_ansi(text)
    if not lexer_name or not text.strip():
        return text
    pair = _get_highlighter(lexer_name)
    if not pair:
        return text
    try:
        from pygments import highlight as pyg_hl
        return pyg_hl(text, pair[0], pair[1]).rstrip('\n')
    except ImportError:
        return text


def _inline_highlight(old_text, new_text):
    """对比两段文本，返回带背景色高亮差异部分的 (old_hl, new_hl)

    注意：对输入文本做 ANSI 转义序列消毒（移除所有 ESC 字符），防止终端注入。
    """
    # 消毒：最安全的兜底——移除所有 ESC 字符
    old_text = _sanitize_ansi(old_text)
    new_text = _sanitize_ansi(new_text)

    sm = difflib.SequenceMatcher(None, old_text, new_text)
    if sm.ratio() < 0.25:
        return old_text, new_text
    old_parts, new_parts = [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == 'equal':
            old_parts.append(old_text[i1:i2])
            new_parts.append(new_text[j1:j2])
        elif op == 'replace':
            old_parts.append(_bg_del.apply(old_text[i1:i2]))
            new_parts.append(_bg_add.apply(new_text[j1:j2]))
        elif op == 'delete':
            old_parts.append(_bg_del.apply(old_text[i1:i2]))
        elif op == 'insert':
            new_parts.append(_bg_add.apply(new_text[j1:j2]))
    return ''.join(old_parts), ''.join(new_parts)


def _parse_diff_hunks(diff_list, line_offset=0):
    """解析 unified diff 行列表，返回结构化记录列表。

    每条记录为 (type, line, old_num, new_num) 元组，
    type 取值: 'hunk' | 'del' | 'add' | 'ctx'

    ★ 方向1 P0-2（文件头误判修复）：文件头精确匹配 ``--- ``/``+++ ``（含空格，
    difflib.unified_diff 输出恒为 ``--- a/...``/``+++ b/...``），并排除 ``----``
    边界——删除行内容以 ``--`` 开头（diff 表示为 ``---foo``，无空格）不再被
    误判为 old_file，落入 del 分支（``-`` 前缀）。
    """
    hunk_re = re.compile(r'^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@')
    old_num = new_num = 0
    parsed = []
    for line in diff_list:
        # 文件头：``--- a/path`` / ``+++ b/path``（difflib 输出恒含空格）；
        # ``----`` 边界兜底（排除 ``---``+非空格 前缀）
        if line.startswith('--- ') and not line.startswith('----'):
            parsed.append(('old_file', line, 0, 0))
            continue
        if line.startswith('+++ ') and not line.startswith('++++'):
            parsed.append(('new_file', line, 0, 0))
            continue
        m = hunk_re.match(line)
        if m:
            old_num = line_offset + int(m.group(1))
            new_num = line_offset + int(m.group(2))
            parsed.append(('hunk', line, old_num, new_num))
            continue
        if line.startswith('-'):
            parsed.append(('del', line, old_num, 0))
            old_num += 1
        elif line.startswith('+'):
            parsed.append(('add', line, 0, new_num))
            new_num += 1
        else:
            parsed.append(('ctx', line, old_num, new_num))
            old_num += 1
            new_num += 1
    return parsed


def _fold_context(parsed, fold_threshold=4):
    """折叠连续上下文行（保留首尾各1行）。

    Args:
        parsed: _parse_diff_hunks 的输出
        fold_threshold: 连续上下文行折叠阈值

    Returns:
        折叠后的记录列表
    """
    folded = []
    ctx_run = []

    def _flush_ctx():
        if len(ctx_run) > fold_threshold:
            folded.append(ctx_run[0])
            hidden = len(ctx_run) - 2
            folded.append(('fold', hidden, 0, 0))
            folded.append(ctx_run[-1])
        else:
            folded.extend(ctx_run)

    for item in parsed:
        if item[0] == 'ctx':
            ctx_run.append(item)
        else:
            _flush_ctx()
            ctx_run = []
            folded.append(item)
    _flush_ctx()
    return folded


def _write_diff_line(text: str, output_target=None, width=None):
    """写入一行 diff 输出，优先使用 output_target，否则使用 publish_output。

    ★ H3（BUG 修复，2026-08-15）：出口截断——``width`` 非 None 且 >0 时，
    经 ``ansi_to_line``（ANSI→AnsiLine）+ ``truncate_line``（CJK 安全截断）
    组合按宽度截断（diff 非每帧热路径，性能可接受），窄终端 diff 长行不再
    wraparound。``width`` None（默认）保持原样（旧调用/纯函数兼容——
    ``render_diff_to_ansi``/WebUI 不传）。截断后仍为合法 ANSI（无断裂 SGR）。
    """
    if width is not None and width > 0:
        from src.renderer.ansi.helpers import ansi_to_line, truncate_line
        line = ansi_to_line(text)
        if line.width > width:
            text = truncate_line(line, width).render()
    if output_target is not None:
        output_target.write_line(text)
    else:
        publish_output(text, level="raw")


def _render_chunk(item, w, lexer_name, output_target, max_width=None):
    """渲染一个非增删类型的 diff 块（old_file/new_file/hunk/ctx/fold）。

    Args:
        item: folded 列表中的条目 (typ, line, old_num, new_num)
        w: 行号宽度
        lexer_name: 语法高亮名称
        output_target: 可选的输出目标
        max_width: 行截断宽度（None=不截断；H3——出口截断经 _write_diff_line）
    """
    typ = item[0]
    dim = _DIFF_CTX_STYLE
    if typ == 'old_file':
        # P3-12：文件头路径消毒（ANSI 注入防护）——path 来自 diff 列表
        # （可能是用户提供的文件名），须与 ctx/add/del 行一致走 _sanitize_ansi。
        path = _sanitize_ansi(item[1][4:] if len(item[1]) > 4 else "")
        # 美化：旧文件头亮红加粗（保持 ``┌─ path`` 连续字面量，测试/WebUI 兼容）
        _write_diff_line("\n  " + _DIFF_FILE_OLD.apply("┌─ " + path), output_target, max_width)
        return
    if typ == 'new_file':
        path = _sanitize_ansi(item[1][4:] if len(item[1]) > 4 else "")
        # 美化：新文件头亮绿加粗
        _write_diff_line("  " + _DIFF_FILE_NEW.apply("└─ " + path), output_target, max_width)
        return
    if typ == 'hunk':
        hl = StyleSheet.resolve("highlight", Style(fg=45))
        bold_hl = hl.merge(Style(bold=True))
        # ★ BUG-56（review 方向）：hunk 头消毒（ANSI 注入防护）——修复前
        #   ``bold_hl.apply(item[1])`` 直接输出；hunk 头来自外部 diff 输入
        #   （``_parse_diff_hunks`` 正则只校验前缀），构造 ``@@ -1 +1 @@
        #   \x1b[31mINJECT`` 可注入 ANSI（与 old_file/new_file/ctx 行已消毒
        #   语义一致）。
        hunk_text = _sanitize_ansi(item[1])
        # 美化：左装饰条 ``▌``（柔青 dim）+ hunk 头加粗亮青
        _write_diff_line("  " + _DIFF_HUNK_BAR.apply("▌ ") + bold_hl.apply(hunk_text), output_target, max_width)
        return
    if typ == 'fold':
        hidden = item[1]
        # 方向3（折叠行对齐）：与 ctx 行结构对称——行号列占位 + 分隔空格 +
        # 折叠提示（修复前 `│` 后紧贴 `┄`，行号列/内容列与 ctx 行不对齐）。
        # 美化：折叠提示柔青（与 hunk 装饰同色系，视觉层级一致）
        _write_diff_line(
            "  " + dim.apply(f"│ {'':>{w}} │") + " " + _DIFF_HUNK_BAR.apply(f"┄ {hidden} lines ┄"),
            output_target,
            max_width,
        )
        return
    # ctx: 上下文行（先消毒用户内容再输出，防 ANSI 注入）
    ctx_text = item[1][1:] if item[1].startswith(' ') else item[1]
    ctx_text = _sanitize_ansi(ctx_text)
    hl_text = _syntax_hl(ctx_text, lexer_name) if lexer_name else ctx_text
    _write_diff_line(
        "  " + dim.apply(f"│ {item[2]:>{w}} │") + " " + hl_text,
        output_target,
        max_width,
    )


def _render_diff_summary(diff_list, output_target=None, width: int = _SEPARATOR_WIDTH, max_width: int | None = None):
    """渲染 diff 变更统计摘要（增删行数）。

    ★ 方向1 P0-2：基于 ``_parse_diff_hunks`` 的 parsed 结构统计——不再用
    ``startswith`` 前缀启发式判定文件头/增删行：删除行内容以 ``--`` 开头
    （diff 表示为 ``---foo``）等场景不再被误判为文件头；old_file/new_file/
    hunk/fold 不计入统计。输出分隔线和统计行。

    方向1 P1（宽度参数化）：分隔线宽度提取 ``_SEPARATOR_WIDTH``（默认 40）；
    调用方（render_diff_to_ansi/show_file_diff）传 ``min(40, max(10, 终端宽度
    或 w*2))``——窄终端分隔线收缩不溢出，默认 width=40 行为不变。

    H3（2026-08-15）：新增 ``max_width``——行截断宽度（None=不截断；与
    ``render_diff`` 同语义，出口截断经 ``_write_diff_line``）。
    """
    parsed = _parse_diff_hunks(diff_list)
    adds = dels = ctx = 0
    for typ, _line, _old_num, _new_num in parsed:
        if typ == 'add':
            adds += 1
        elif typ == 'del':
            dels += 1
        elif typ == 'ctx':
            ctx += 1
        # old_file/new_file/hunk/fold 不计入统计

    if adds == 0 and dels == 0:
        return

    # 分隔线（宽度参数化：取 min(_SEPARATOR_WIDTH, width)，调用方已 clamp ≥10）
    dim = _DIFF_CTX_STYLE
    sep = min(_SEPARATOR_WIDTH, width)
    _write_diff_line("  " + dim.apply("╌" * sep), output_target, max_width)

    parts = []
    if adds:
        parts.append(_DIFF_ADD_STYLE.apply(f"🟢 +{adds}"))
    if dels:
        parts.append(_DIFF_DEL_STYLE.apply(f"🔴 -{dels}"))
    if ctx:
        parts.append(_DIFF_CTX_STYLE.apply(f"⚪ {ctx} unchanged"))
    # 美化：统计行前置 ✦ 图标（柔青），层级与分隔线/折叠提示一致
    _write_diff_line("  " + _DIFF_HUNK_BAR.apply("✦ ") + "  ".join(parts), output_target, max_width)


def render_diff(diff_list, w, line_offset=0, lexer_name='', output_target: Optional["IOutputTarget"] = None, width: int = _SEPARATOR_WIDTH, max_width: int | None = None):
    """
    美化后的差异渲染：
    - 文件头：┌─ a/path（旧，亮红加粗） / └─ b/path（新，亮绿加粗）
    - hunk 头：▌ @@ -L,N +L,N @@ — 装饰条柔青 + 头加粗亮青色
    - 删除行：│ 行号 │（柔红）+ 加粗红 - 前缀，内容含红背景行内高亮
    - 新增行：│ 行号 │（绿）+ 加粗绿 + 前缀，内容含绿背景行内高亮
    - 上下文行：浅灰 │ 行号 │ 内容（语法高亮）
    - 折叠行：┄ N lines ┄（柔青提示）
    - 多 hunk 间 ╌╌╌ 分隔线
    - 成对修改行内高亮差异部分（红/绿背景色）

    方向1 P1（宽度参数化）：多 hunk 分隔线宽度提取 ``_SEPARATOR_WIDTH``
    （默认 40），调用方传收缩宽度（如 ``min(40, max(10, w*2))``）。

    H3（2026-08-15）：新增 ``max_width``——行截断宽度（None=不截断，保持
    旧调用/纯函数行为；``render_diff_to_ansi``/WebUI 不传）。``width``
    （分隔线宽度）与 ``max_width``（截断宽度）职责分离，不复用语义冲突。
    截断经 ``_write_diff_line`` 出口统一执行（含分隔线/hunk/fold/ctx/add/del
    各行；行号列前缀随整行一起截断，前缀短不受影响）。
    """
    def _hl(text):
        # 方向A 步骤3：无条件调用 _syntax_hl——空 lexer 时也消毒（消除单行
        # add/del 内容含恶意 ANSI 时完全跳过消毒的注入窗口；_syntax_hl 内部
        # 已先消毒，空 lexer 返回消毒后字面量）。
        return _syntax_hl(text, lexer_name)

    # 解析 diff
    parsed = _parse_diff_hunks(diff_list, line_offset)
    # 折叠上下文
    folded = _fold_context(parsed)
    # 多 hunk 分隔线宽度（方向1 P1：调用方已 clamp ≥10）
    sep = min(_SEPARATOR_WIDTH, width)

    def _flush_pairs(del_buf, add_buf, max_width):
        # 美化：行号列统一为 ``│ n │`` 表格风格（与 ctx/fold 对齐），
        # 删除行号列柔红、新增行号列绿；`-`/`+` 标记加粗醒目。
        for i in range(max(len(del_buf), len(add_buf))):
            if i < len(del_buf) and i < len(add_buf):
                _, d_line, d_oln, _ = del_buf[i]
                _, a_line, _, a_nln = add_buf[i]
                h_old, h_new = _inline_highlight(d_line[1:], a_line[1:])
                _write_diff_line(
                    "  " + _DIFF_NUM_DEL.apply(f"│ {d_oln:>{w}} │") + " " + _DIFF_MARK_DEL.apply("-") + h_old,
                    output_target,
                    max_width,
                )
                _write_diff_line(
                    "  " + _DIFF_NUM_ADD.apply(f"│ {a_nln:>{w}} │") + " " + _DIFF_MARK_ADD.apply("+") + h_new,
                    output_target,
                    max_width,
                )
            elif i < len(del_buf):
                _, d_line, d_oln, _ = del_buf[i]
                _write_diff_line(
                    "  " + _DIFF_NUM_DEL.apply(f"│ {d_oln:>{w}} │") + " " + _DIFF_MARK_DEL.apply("-") + _hl(d_line[1:]),
                    output_target,
                    max_width,
                )
            else:
                _, a_line, _, a_nln = add_buf[i]
                _write_diff_line(
                    "  " + _DIFF_NUM_ADD.apply(f"│ {a_nln:>{w}} │") + " " + _DIFF_MARK_ADD.apply("+") + _hl(a_line[1:]),
                    output_target,
                    max_width,
                )

    del_buf, add_buf = [], []
    _hunk_count = 0
    for item in folded:
        typ = item[0]
        if typ == 'del':
            if add_buf:
                _flush_pairs(del_buf, add_buf, max_width)
                del_buf, add_buf = [], []
            del_buf.append(item)
        elif typ == 'add':
            add_buf.append(item)
        else:
            if del_buf or add_buf:
                _flush_pairs(del_buf, add_buf, max_width)
                del_buf, add_buf = [], []
            # 多 hunk 间输出分隔线（宽度参数化，方向1 P1）
            if typ == 'hunk':
                _hunk_count += 1
                if _hunk_count > 1:
                    _write_diff_line(
                        "  " + _DIFF_CTX_STYLE.apply("╌" * sep),
                        output_target,
                        max_width,
                    )
            _render_chunk(item, w, lexer_name, output_target, max_width)
    if del_buf or add_buf:
        _flush_pairs(del_buf, add_buf, max_width)


def render_diff_to_ansi(path: str, old_content: str, new_content: str) -> str:
    """将文件差异渲染为带 ANSI 颜色的纯文本字符串。

    纯函数，无锁，不涉及 I/O。返回的字符串可直接在支持 ANSI 的终端显示，
    或由前端做 ANSI→HTML 转换后在 WebUI 渲染。

    与 show_file_diff 的区别：
      - show_file_diff: 通过 output_target 输出（可能有锁），有副作用
      - render_diff_to_ansi: 返回字符串，无锁，无副作用，纯函数
    """
    old_norm = old_content.replace('\r\n', '\n') if old_content else ""
    new_norm = new_content.replace('\r\n', '\n') if new_content else ""
    old_lines = old_norm.splitlines(keepends=False)
    new_lines = new_norm.splitlines(keepends=False)

    diff_list = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f'a/{path}', tofile=f'b/{path}',
        lineterm='', n=3
    ))
    if not diff_list:
        return ""

    w = len(str(max(len(old_lines), len(new_lines), 1)))
    ext = os.path.splitext(path)[1].lstrip('.').lower()
    lexer_name = _resolve_lexer_name(ext)
    # 方向1 P1：分隔线宽度随行号宽度收缩（min(40, max(10, w*2))，窄终端不溢出）
    sep_w = min(_SEPARATOR_WIDTH, max(10, w * 2))

    # 使用简单列表收集器（纯内存操作，无锁）
    collected: list[str] = []

    class _Collector:
        """收集 write_line 调用的简单输出目标（替代 type() 动态类创建）。"""
        _target = collected
        @classmethod
        def write_line(cls, text: str) -> None:
            cls._target.append(text)

    render_diff(diff_list, w, lexer_name=lexer_name, output_target=_Collector, width=sep_w)
    # 追加变更统计摘要
    _render_diff_summary(diff_list, output_target=_Collector, width=sep_w)
    # 移除最后的空行（如有）
    while collected and collected[-1] == '':
        collected.pop()
    return '\n'.join(collected)


def show_file_diff(path, old_content, new_content, output_target: Optional["IOutputTarget"] = None):
    """显示文件差异对比

    ★ diff_active 互斥机制：
      本函数直接管理 diff_active（设置/清除），而非通过 _DiffGuard/
      _diff_lock 路径。设计前提：
      - 调用时通常已处于 _DiffGuard 上下文内（由 _show_diff_preview
        或 capture_and_print_async 调用），此时 diff_active 已置位，
        本函数的设置/清除为无操作（检测 was_active 跳过）。
      - 当被直接调用（不在 _DiffGuard 内）时，通过设置 diff_active
        阻止 _refresh_loop 在此期间渲染帧，避免 diff 输出与面板刷新交叠。
      - 超时兜底：_render_frame_unlocked 在 diff_active 超过 30s 且
        _diff_count==0 时强制清除（认为异常/取消导致残留）。
    """
    old_norm = old_content.replace('\r\n', '\n') if old_content else ""
    new_norm = new_content.replace('\r\n', '\n') if new_content else ""

    old_lines = old_norm.splitlines(keepends=False)
    new_lines = new_norm.splitlines(keepends=False)

    diff_list = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f'a/{path}', tofile=f'b/{path}',
        lineterm='', n=3
    ))
    if not diff_list:
        msg = "  " + _DIFF_CTX_STYLE.apply("(内容相同，无变化)")
        if output_target is not None:
            output_target.write_line(msg)
        else:
            publish_output(msg, level="raw")
        return
    w = len(str(max(len(old_lines), len(new_lines), 1)))
    ext = os.path.splitext(path)[1].lstrip('.').lower()
    lexer_name = _resolve_lexer_name(ext)
    # 方向1 P1：分隔线宽度随行号宽度收缩（min(40, max(10, w*2))，窄终端不溢出）
    sep_w = min(_SEPARATOR_WIDTH, max(10, w * 2))
    # ★ H3（2026-08-15）：出口按终端宽度截断——窄终端 diff 长行不再
    #   wraparound。TerminalWidthCache TTL 缓存（无终端时内部回退 80，行为
    #   确定）；``render_diff_to_ansi``（纯函数/WebUI）不传，保持行为不变。
    from src.tui._screen import TerminalWidthCache
    term_w = TerminalWidthCache.get_default().get_width()
    # 锁外预热 Pygments lexer，避免在锁内首次加载阻塞其他线程
    _get_highlighter(lexer_name)
    diff_was_active = diff_active.is_set()
    if not diff_was_active:
        diff_active.set()
    try:
        with _try_acquire_output_lock(name=f"show_file_diff:{os.path.basename(path)}"):
            render_diff(diff_list, w, lexer_name=lexer_name, output_target=output_target, width=sep_w, max_width=term_w)
            _render_diff_summary(diff_list, output_target=output_target, width=sep_w, max_width=term_w)
    finally:
        if not diff_was_active:
            diff_active.clear()


__all__ = [
    "render_diff_to_ansi",
    "show_file_diff",
    "render_diff",
    "_resolve_lexer_name",
    "_get_highlighter",
    "_sanitize_ansi",
    "_syntax_hl",
    "_inline_highlight",
    "_parse_diff_hunks",
    "_fold_context",
    "_write_diff_line",
    "_render_chunk",
    "_render_diff_summary",
    "_BG_RED",
    "_BG_GREEN",
    "_BG_OFF",
    # ★ P3-13：_BG_RED/_BG_GREEN/_BG_OFF 为 deprecated 兼容 re-export
    #   （生产路径零引用，移除计划见模块内注释）；_RESET_STR 未在 __all__
    #   导出（仅保留定义供外部兼容导入，同移除计划）。
]
