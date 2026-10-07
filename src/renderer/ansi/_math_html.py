"""_math_html — KaTeX HTML / 链接 / 盒子类命令的终端映射（零 Rich）。

KaTeX 的 ``\\href`` / ``\\url`` / ``\\includegraphics`` / ``\\htmlId`` /
``\\htmlClass`` / ``\\htmlStyle`` / ``\\htmlData`` / ``\\class`` / ``\\style`` /
``\\cssId`` / ``\\mmlToken`` / ``\\bbox`` / ``\\enclose`` / ``\\phase`` 等命令
在终端中无法呈现 HTML 语义，按「保留可见内容、不残留命令文本」的原则映射：

  - ``\\href{url}{text}`` → ``text``（Run.link 设为 url，支持 OSC 8）；
  - ``\\url{u}`` → ``u``；
  - ``\\includegraphics[alt=..]{src}`` → ``[图片: alt]``（无 alt 取文件名）；
  - ``\\html*{..}{x}`` / ``\\class`` / ``\\style`` / ``\\cssId`` → ``x``；
  - ``\\bbox`` / ``\\enclose`` / ``\\phase`` / ``\\mmlToken`` → 内容（附必要记号）；
  - ``\\raisebox`` / ``\\reflectbox`` / ``\\scalebox`` / ``\\rotatebox`` /
    ``\\resizebox`` / ``\\mathreflectbox`` → 内容（终端无精确变换）。

扩展方式：新增命令只改 ``_register_html_commands``。
"""

from __future__ import annotations

from .helpers import AnsiLine

from ._math_style import _M_SYM, _M_NOTICE, _M_FN
from ._math_box import _Box, _txt, _empty_box, _plain_of
from ._math_cmds import COMMAND_HANDLERS

#: 只丢弃首个参数、渲染第二个内容的命令（``\htmlId{id}{x}`` 等）
_TWO_ARG_WRAPPERS: frozenset = frozenset({
    "htmlId", "htmlClass", "htmlStyle", "htmlData",
    "class", "style", "cssId", "mmlToken",
})

#: 渲染**首个**参数、丢弃第二个的命令（``\texttip{text}{tip}``）
_CONTENT_FIRST_WRAPPERS: frozenset = frozenset({"texttip", "mathtip"})

#: 读取可选参数后渲染内容的命令（``\bbox[..]{x}``）
_OPTIONAL_ARG_WRAPPERS: frozenset = frozenset({"bbox"})

#: 变换类命令（参数个数 → 渲染最后一个参数）
_TRANSFORM_WRAPPERS: dict[str, int] = {
    "reflectbox": 0, "mathreflectbox": 0,
    "scalebox": 1, "rotatebox": 1, "resizebox": 2,
}


def _set_link(box: _Box, url: str) -> _Box:
    """给 ``box`` 内所有 run 设置超链接（返回新对象，不改缓存行）。"""
    if not url:
        return box
    lines: list[AnsiLine] = []
    for ln in box.lines:
        nl = AnsiLine()
        for run in ln.runs:
            nl.append(run.text, run.style, url)
        lines.append(nl)
    return _Box(lines, kind=box.kind, baseline=box.baseline)


def _alt_text(opts: str, src: str) -> str:
    r"""从 ``\includegraphics`` 选项 / 路径提取替代文本。"""
    low = (opts or "").lower()
    idx = low.find("alt=")
    if idx >= 0:
        rest = opts[idx + 4:].strip()
        if rest[:1] in "\"'":
            quote = rest[0]
            end = rest.find(quote, 1)
            if end > 0:
                return rest[1:end]
        end = rest.find(",")
        return (rest[:end] if end > 0 else rest).strip()
    name = (src or "").strip()
    if not name:
        return ""
    name = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    return name


class _MathHtmlMixin:
    """HTML / 链接 / 盒子类命令（由 ``_LatexRenderer`` 继承）。"""

    # ── 链接 ────────────────────────────────────────────

    def _cmd_href(self, cmd: str = "href") -> _Box:
        """``\\href{url}{text}`` → text（携带 OSC 8 链接）；``\\url{u}`` → u。"""
        if cmd == "url":
            url = self._read_group_raw().strip()
            return _set_link(_txt(url, _M_NOTICE), url)
        url = self._read_group_raw().strip()
        body = self._render_sub(self._read_group_raw())
        return _set_link(body, url)

    # ── 图片 ────────────────────────────────────────────

    def _cmd_includegraphics(self, cmd: str = "includegraphics") -> _Box:
        """``\\includegraphics[opts]{src}`` → ``[图片: alt]``。"""
        opts = self._read_optional_raw()
        src = self._read_group_raw().strip()
        alt = _alt_text(opts, src)
        label = "[图片: " + (alt or "?") + "]"
        return _txt(label, _M_NOTICE)

    # ── 参数包装（丢弃元信息、渲染内容） ─────────────────

    def _cmd_wrapper_two(self, cmd: str) -> _Box:
        """``\\htmlId{id}{x}`` / ``\\mmlToken{t}{x}`` 等 → 渲染 ``x``。"""
        self._read_group_raw()
        return self._render_sub(self._read_group_raw())

    def _cmd_wrapper_optional(self, cmd: str) -> _Box:
        """``\\bbox[opts]{x}`` → 渲染 ``x``。"""
        self._read_optional_raw()
        return self._render_sub(self._read_group_raw())

    def _cmd_wrapper_content_first(self, cmd: str) -> _Box:
        """``\\texttip{text}{tip}`` / ``\\mathtip{math}{tip}`` → 渲染首个参数。"""
        content = self._render_sub(self._read_group_raw())
        self._read_group_raw()
        return content

    def _cmd_transform(self, cmd: str) -> _Box:
        """``\\scalebox{n}{x}`` / ``\\rotatebox{deg}{x}`` 等 → 渲染内容。"""
        for _ in range(_TRANSFORM_WRAPPERS.get(cmd, 0)):
            self._read_group_raw()
        return self._render_sub(self._read_group_raw())

    def _cmd_raisebox(self, cmd: str = "raisebox") -> _Box:
        """``\\raisebox{1em}[h][d]{x}`` → 渲染内容 ``x``。"""
        self._read_group_raw()
        self._read_optional_raw()
        self._read_optional_raw()
        return self._render_sub(self._read_group_raw())

    # ── 其它语义映射 ─────────────────────────────────────

    def _cmd_enclosearg(self, cmd: str) -> _Box:
        """``\\enclose{notation}{x}`` → 内容（终端尽量体现记号）。"""
        note = self._read_group_raw().strip().lower()
        body = self._render_sub(self._read_group_raw())
        if "circle" in note:
            return _encircle(body)
        if "box" in note:
            return _boxed_inline(body)
        return body

    def _cmd_phase(self, cmd: str = "phase") -> _Box:
        """``\\phase{x}`` → ``∡x``（相位角记号）。"""
        body = self._render_sub(self._read_group_raw())
        line = AnsiLine.of("∡", _M_SYM)
        for run in body.lines[0].runs:
            line.append_run(run)
        return _Box([line])

    def _cmd_textcircled(self, cmd: str = "textcircled") -> _Box:
        """``\\textcircled{a}`` → 圈字符（组合圈 U+20DD）。"""
        raw = self._read_group_raw()
        text = _plain_of(self._render_sub(raw)).strip()
        if not text:
            return _empty_box()
        return _txt("".join(ch + "\u20dd" for ch in text), _M_SYM)

    def _cmd_operatornamewithlimits(self, cmd: str = "operatornamewithlimits") -> _Box:
        """``\\operatornamewithlimits{f}`` ≡ ``\\operatorname*{f}``（上下限）。"""
        raw = self._read_group_raw()
        return _txt(raw, _M_FN, kind="limit")


def _encircle(box: _Box) -> _Box:
    lines: list[AnsiLine] = []
    for ln in box.lines:
        nl = AnsiLine()
        for run in ln.runs:
            nl.append(run.text + "\u20dd" if run.text.strip() else run.text,
                      run.style)
        lines.append(nl)
    return _Box(lines, kind=box.kind, baseline=box.baseline)


def _boxed_inline(box: _Box) -> _Box:
    lines: list[AnsiLine] = []
    for ln in box.lines:
        nl = AnsiLine.of("▕", _M_NOTICE)
        for run in ln.runs:
            nl.append_run(run)
        nl.append("▏", _M_NOTICE)
        lines.append(nl)
    return _Box(lines, kind=box.kind, baseline=box.baseline)


def _register_html_commands() -> dict[str, str]:
    table: dict[str, str] = {}
    table["href"] = "_cmd_href"
    table["url"] = "_cmd_href"
    table["includegraphics"] = "_cmd_includegraphics"
    for cmd in _TWO_ARG_WRAPPERS:
        table[cmd] = "_cmd_wrapper_two"
    for cmd in _CONTENT_FIRST_WRAPPERS:
        table[cmd] = "_cmd_wrapper_content_first"
    for cmd in _OPTIONAL_ARG_WRAPPERS:
        table[cmd] = "_cmd_wrapper_optional"
    for cmd in _TRANSFORM_WRAPPERS:
        table[cmd] = "_cmd_transform"
    table["raisebox"] = "_cmd_raisebox"
    table["enclose"] = "_cmd_enclosearg"
    table["phase"] = "_cmd_phase"
    table["textcircled"] = "_cmd_textcircled"
    table["operatornamewithlimits"] = "_cmd_operatornamewithlimits"
    return table


COMMAND_HANDLERS.update(_register_html_commands())


__all__ = ["_MathHtmlMixin"]
