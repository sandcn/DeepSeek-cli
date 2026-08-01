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

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ._output_target import IOutputTarget

# 行内差异背景色（256 色，使用 Style）
_BG_RED = '\033[48;5;124m'    # 256色暗红背景（保留，因 Style 不支持 bg only）
_BG_GREEN = '\033[48;5;28m'   # 256色柔和绿背景（保留，因 Style 不支持 bg only）
_BG_OFF = '\033[49m'          # 重置为默认背景色

# 语义色常量引用（从 StyleSheet 获取，兜底硬编码确保任何加载顺序下都有默认值）
_DIFF_ADD_STYLE: Style = StyleSheet.get("diff_add") or Style(fg=41)
_DIFF_DEL_STYLE: Style = StyleSheet.get("diff_del") or Style(fg=196)
_DIFF_CTX_STYLE: Style = StyleSheet.get("diff_ctx") or Style(fg=244)

# 向后兼容常量（从 StyleSheet.get() 获取语义色，兜底硬编码值）
# 保留别名供 _render_* 函数中 f-string 使用（已迁移为 Style.apply）
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


def _get_highlighter(lexer_name):
    """获取或缓存 pygments lexer + formatter，未知 lexer 自动降级到 text。"""
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
    """移除字符串中所有 ANSI 转义序列（移除所有 ESC 字符）。

    这是最安全的兜底消毒策略——移除所有 \\x1b（ESC）字符，
    无论其是否为合法序列头。语法高亮生成的 ANSI 序列在本函数
    之后执行，不受影响。

    Args:
        text: 可能含 ANSI 转义序列的输入字符串。

    Returns:
        不含任何 \\x1b 字符的安全字符串。
    """
    return re.sub('\x1b', '', text)


def _syntax_hl(text, lexer_name):
    """对单行文本做语法高亮（输入先做 ANSI 消毒防注入）。"""
    if not lexer_name or not text.strip():
        return text
    # 先消毒再传 pygments，防止 ANSI 注入
    text = _sanitize_ansi(text)
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
            old_parts.append(f"{_BG_RED}{old_text[i1:i2]}{_BG_OFF}")
            new_parts.append(f"{_BG_GREEN}{new_text[j1:j2]}{_BG_OFF}")
        elif op == 'delete':
            old_parts.append(f"{_BG_RED}{old_text[i1:i2]}{_BG_OFF}")
        elif op == 'insert':
            new_parts.append(f"{_BG_GREEN}{new_text[j1:j2]}{_BG_OFF}")
    return ''.join(old_parts), ''.join(new_parts)


def _parse_diff_hunks(diff_list, line_offset=0):
    """解析 unified diff 行列表，返回结构化记录列表。

    每条记录为 (type, line, old_num, new_num) 元组，
    type 取值: 'hunk' | 'del' | 'add' | 'ctx'
    """
    hunk_re = re.compile(r'^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@')
    old_num = new_num = 0
    parsed = []
    for line in diff_list:
        if line.startswith('---'):
            parsed.append(('old_file', line, 0, 0))
            continue
        if line.startswith('+++'):
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


def _write_diff_line(text: str, output_target=None):
    """写入一行 diff 输出，优先使用 output_target，否则使用 publish_output。"""
    if output_target is not None:
        output_target.write_line(text)
    else:
        publish_output(text, level="raw")


def _render_chunk(item, w, lexer_name, output_target):
    """渲染一个非增删类型的 diff 块（old_file/new_file/hunk/ctx/fold）。

    Args:
        item: folded 列表中的条目 (typ, line, old_num, new_num)
        w: 行号宽度
        lexer_name: 语法高亮名称
        output_target: 可选的输出目标
    """
    typ = item[0]
    dim = _DIFF_CTX_STYLE
    if typ == 'old_file':
        path = item[1][4:] if len(item[1]) > 4 else ""
        _write_diff_line("  " + dim.apply("┌─ " + path), output_target)
        return
    if typ == 'new_file':
        path = item[1][4:] if len(item[1]) > 4 else ""
        _write_diff_line("  " + dim.apply("└─ " + path), output_target)
        return
    if typ == 'hunk':
        hl = StyleSheet.resolve("highlight", Style(fg=45))
        bold_hl = hl.merge(Style(bold=True))
        _write_diff_line("  " + bold_hl.apply(item[1]), output_target)
        return
    if typ == 'fold':
        hidden = item[1]
        _write_diff_line(
            "  " + dim.apply(f"│ {'':>{w}} │┄ {hidden} lines ┄"),
            output_target,
        )
        return
    # ctx: 上下文行（先消毒用户内容再输出，防 ANSI 注入）
    ctx_text = item[1][1:] if item[1].startswith(' ') else item[1]
    ctx_text = _sanitize_ansi(ctx_text)
    hl_text = _syntax_hl(ctx_text, lexer_name) if lexer_name else ctx_text
    _write_diff_line(
        "  " + dim.apply(f"│ {item[2]:>{w}} │") + " " + hl_text,
        output_target,
    )


def _render_diff_summary(diff_list, output_target=None):
    """渲染 diff 变更统计摘要（增删行数）。

    从 diff_list 中统计 +/-/ctx 行数，输出分隔线和统计行。
    排除 ---/+++ 文件头和 @@ 块头行。
    """
    adds = dels = ctx = 0
    for line in diff_list:
        if line.startswith('---') or line.startswith('+++') or line.startswith('@@'):
            continue
        if line.startswith('+'):
            adds += 1
        elif line.startswith('-'):
            dels += 1
        else:
            ctx += 1

    if adds == 0 and dels == 0:
        return

    # 分隔线
    dim = _DIFF_CTX_STYLE
    _write_diff_line("  " + dim.apply("╌" * 40), output_target)

    parts = []
    if adds:
        parts.append(_DIFF_ADD_STYLE.apply(f"🟢 +{adds}"))
    if dels:
        parts.append(_DIFF_DEL_STYLE.apply(f"🔴 -{dels}"))
    if ctx:
        parts.append(_DIFF_CTX_STYLE.apply(f"⚪ {ctx} unchanged"))
    _write_diff_line("  " + "  ".join(parts), output_target)


def render_diff(diff_list, w, line_offset=0, lexer_name='', output_target: Optional["IOutputTarget"] = None):
    """
    美化后的差异渲染：
    - 文件头：┌─ a/path（old） / └─ b/path（new）
    - hunk 头：@@ -L,N +L,N @@ — 加粗亮青色
    - 删除行：RED▐ 左颜色条 + 暗红行号 + BRIGHT_RED - 前缀
    - 新增行：GREEN▐ 左颜色条 + 暗绿行号 + BRIGHT_GREEN + 前缀
    - 上下文行：浅灰 │ 行号 │ 内容（语法高亮）
    - 折叠行：┄ N lines ┄
    - 多 hunk 间 ╌╌╌ 分隔线
    - 成对修改行内高亮差异部分（红/绿背景色）
    """
    def _hl(text):
        return _syntax_hl(text, lexer_name) if lexer_name else text

    # 解析 diff
    parsed = _parse_diff_hunks(diff_list, line_offset)
    # 折叠上下文
    folded = _fold_context(parsed)

    def _flush_pairs(del_buf, add_buf):
        diff_del = _DIFF_DEL_STYLE
        diff_add = _DIFF_ADD_STYLE
        dimmer = _DIFF_CTX_STYLE
        for i in range(max(len(del_buf), len(add_buf))):
            if i < len(del_buf) and i < len(add_buf):
                _, d_line, d_oln, _ = del_buf[i]
                _, a_line, _, a_nln = add_buf[i]
                h_old, h_new = _inline_highlight(d_line[1:], a_line[1:])
                _write_diff_line(
                    "  " + diff_del.apply(f"▐ {d_oln:>{w}} │") + " " + Style(fg=196, bold=True).apply("-") + h_old,
                    output_target,
                )
                _write_diff_line(
                    "  " + diff_add.apply(f"▐ {a_nln:>{w}} │") + " " + Style(fg=41, bold=True).apply("+") + h_new,
                    output_target,
                )
            elif i < len(del_buf):
                _, d_line, d_oln, _ = del_buf[i]
                _write_diff_line(
                    "  " + diff_del.apply(f"▐ {d_oln:>{w}} │") + " " + Style(fg=196, bold=True).apply("-") + _hl(d_line[1:]),
                    output_target,
                )
            else:
                _, a_line, _, a_nln = add_buf[i]
                _write_diff_line(
                    "  " + diff_add.apply(f"▐ {a_nln:>{w}} │") + " " + Style(fg=41, bold=True).apply("+") + _hl(a_line[1:]),
                    output_target,
                )

    del_buf, add_buf = [], []
    _hunk_count = 0
    for item in folded:
        typ = item[0]
        if typ == 'del':
            if add_buf:
                _flush_pairs(del_buf, add_buf)
                del_buf, add_buf = [], []
            del_buf.append(item)
        elif typ == 'add':
            add_buf.append(item)
        else:
            if del_buf or add_buf:
                _flush_pairs(del_buf, add_buf)
                del_buf, add_buf = [], []
            # 多 hunk 间输出分隔线
            if typ == 'hunk':
                _hunk_count += 1
                if _hunk_count > 1:
                    _write_diff_line(
                        "  " + _DIFF_CTX_STYLE.apply("╌" * 40),
                        output_target,
                    )
            _render_chunk(item, w, lexer_name, output_target)
    if del_buf or add_buf:
        _flush_pairs(del_buf, add_buf)


def render_diff_to_ansi(path: str, old_content: str, new_content: str) -> str:
    """将文件差异渲染为带 ANSI 颜色的纯文本字符串。

    纯函数，无锁，不涉及 I/O。返回的字符串可直接在支持 ANSI 的终端显示，
    或由前端做 ANSI→HTML 转换后在 WebUI 渲染。

    与 show_file_diff 的区别：
      - show_file_diff: 通过 output_target 输出（可能有锁），有副作用
      - render_diff_to_ansi: 返回字符串，无锁，无副作用，纯函数
    """
    old_norm = old_content.replace('\r\n', '\n') if old_content else ""
    new_norm = new_content.replace('\r\n', '\n')
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

    # 使用简单列表收集器（纯内存操作，无锁）
    collected: list[str] = []

    class _Collector:
        """收集 write_line 调用的简单输出目标（替代 type() 动态类创建）。"""
        _target = collected
        @classmethod
        def write_line(cls, text: str) -> None:
            cls._target.append(text)

    render_diff(diff_list, w, lexer_name=lexer_name, output_target=_Collector)
    # 追加变更统计摘要
    _render_diff_summary(diff_list, output_target=_Collector)
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
    old_norm = old_content.replace('\r\n', '\n')
    new_norm = new_content.replace('\r\n', '\n')

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
    # 锁外预热 Pygments lexer，避免在锁内首次加载阻塞其他线程
    _get_highlighter(lexer_name)
    diff_was_active = diff_active.is_set()
    if not diff_was_active:
        diff_active.set()
    try:
        with _try_acquire_output_lock(name=f"show_file_diff:{os.path.basename(path)}"):
            render_diff(diff_list, w, lexer_name=lexer_name, output_target=output_target)
            _render_diff_summary(diff_list, output_target=output_target)
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
]
