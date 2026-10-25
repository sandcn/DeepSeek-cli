"""
glob — 文件查找工具

在项目中查找文件和目录，支持 glob 模式匹配、类型(file/dir)过滤。

模式按是否含路径分隔符分两类（同一 pattern 内可用空格分隔混用，OR 语义）：

- **名称模式**（不含 ``/``）：匹配条目名，如 ``*.py``（任意层级的 .py）；
- **路径模式**（含 ``/``）：匹配相对搜索根的 POSIX 路径，``**`` 作为独立段
  匹配零或多层目录，如 ``**/*.py``（任意层级）、``src/**/*.ts``（仅 src 下）、
  ``**/*``（全部文件与目录）。

另支持 shell 风格的大括号展开：``{a,b}`` 会先展开为多个备选模式再逐个匹配，
可嵌套（``*.{c,{h,hpp}}``）、可含空项（``*.{py,}``），如
``**/*.{c,h,cpp,hpp,cc,py,mk}`` 等价于 7 个 ``**/*.ext`` 模式的 OR。
未闭合或展开结果超过上限的 ``{`` 按字面量处理。

使用纯 Python 实现（pathlib + os.walk），不依赖外部命令。
安全、跨平台、无 shell 注入风险。
"""

from __future__ import annotations

import asyncio
import os
import fnmatch
import re
import logging
from pathlib import Path
from ._constants import should_exclude_dir as _should_exclude_dir
from .base import Func

logger = logging.getLogger(__name__)

# ── 魔法数字常量 ──────────────────────────────────────
SMALL_DIR_ENTRY_LIMIT = 200   # 小型目录条目阈值（直接同步遍历）

#: globstar 段标记：作为独立段的 ``**`` 匹配零或多层目录。
_GLOBSTAR = "**"

#: 大括号展开上限：单个模式最多展开出的备选模式数，超出后整条按字面量处理
#: （防止病态嵌套模式生成海量正则拖垮遍历）。
_MAX_BRACE_EXPANSIONS = 256
#: 大括号递归展开深度上限（同时防御异常深的嵌套）。
_MAX_BRACE_DEPTH = 32


def _split_patterns(text: str) -> list[str]:
    """按空白拆分多个模式，但花括号内部的空白不拆。

    这样 ``*.{c, h}`` 仍是一个模式（展开后各备选再经 strip 规范化）；
    花括号数量不平衡时退回普通 ``split()``，保持既有「空格分隔」语义。
    """
    if text.count("{") != text.count("}"):
        return text.split()
    tokens: list[str] = []
    buf: list[str] = []
    depth = 0
    for char in text:
        if char == "{":
            depth += 1
            buf.append(char)
        elif char == "}":
            depth = max(0, depth - 1)
            buf.append(char)
        elif char.isspace() and depth == 0:
            if buf:
                tokens.append("".join(buf))
                buf = []
        else:
            buf.append(char)
    if buf:
        tokens.append("".join(buf))
    return tokens


def _split_brace_alternatives(body: str) -> list[str] | None:
    """按顶层逗号拆分花括号内容；没有顶层逗号时返回 ``None``（按字面量处理）。"""
    parts: list[str] = []
    depth = 0
    start = 0
    has_comma = False
    for index, char in enumerate(body):
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            parts.append(body[start:index])
            start = index + 1
            has_comma = True
    parts.append(body[start:])
    if not has_comma:
        return None
    # 备选两侧空白不参与匹配（"*.{py, txt}" 视作 *.py / *.txt）
    return [part.strip() for part in parts]


def _expand_braces(pattern: str, *, _depth: int = 0) -> list[str]:
    """展开 shell 风格大括号 ``{a,b}``，返回全部备选模式。

    - 支持嵌套（``a{b,c}d`` → ``abd`` / ``acd``）与空项（``a{,b}`` → ``a`` / ``ab``）；
    - 花括号内没有顶层逗号、未闭合或超出展开上限时按字面量保留；
    - 展开结果保持出现顺序，便于稳定输出。
    """
    if _depth >= _MAX_BRACE_DEPTH:
        return [pattern]

    start = pattern.find("{")
    if start < 0:
        return [pattern]

    level = 0
    close = -1
    for index in range(start, len(pattern)):
        if pattern[index] == "{":
            level += 1
        elif pattern[index] == "}":
            level -= 1
            if level == 0:
                close = index
                break
    if close < 0:
        return [pattern]

    head = pattern[:start]
    body = pattern[start + 1:close]
    tail = pattern[close + 1:]

    alternatives = _split_brace_alternatives(body)
    if alternatives is None:
        # 无顶层逗号：花括号按字面量保留，但内部仍可嵌套展开（{a{b,c}} → {ab} / {ac}）
        bodies = ["{" + item + "}" for item in _expand_braces(body, _depth=_depth + 1)]
    else:
        # 各备选内部仍可能嵌套花括号（{b,{c,d}}），逐个继续展开
        bodies = []
        for alternative in alternatives:
            bodies.extend(_expand_braces(alternative, _depth=_depth + 1))

    tails = _expand_braces(tail, _depth=_depth + 1)

    results: list[str] = []
    for middle in bodies:
        for suffix in tails:
            results.append(head + middle + suffix)
            if len(results) > _MAX_BRACE_EXPANSIONS:
                return [pattern]
    return results


def _translate_segment(segment: str) -> str:
    """把单个路径段（不含 ``/``）的 fnmatch 通配符翻译为正则片段。

    段内语义与 :func:`fnmatch.translate` 一致（``*`` / ``?`` / ``[seq]`` /
    ``[!seq]``），但 ``*`` 与 ``?`` 不跨越路径分隔符——路径模式按段拼接，
    段内不允许吃掉 ``/``（否则 ``a/*/b`` 会误匹配 ``a/x/y/b``）。
    """
    res: list[str] = []
    i, n = 0, len(segment)
    while i < n:
        c = segment[i]
        i += 1
        if c == "*":
            res.append("[^/]*")
        elif c == "?":
            res.append("[^/]")
        elif c == "[":
            j = i
            if j < n and segment[j] == "!":
                j += 1
            if j < n and segment[j] == "]":
                j += 1
            while j < n and segment[j] != "]":
                j += 1
            if j >= n:
                # 未闭合的 '[' 按字面量处理（同 fnmatch）
                res.append(r"\[")
            else:
                stuff = segment[i:j]
                i = j + 1
                if stuff.startswith("!"):
                    stuff = "^" + stuff[1:]
                elif stuff.startswith("^"):
                    stuff = "\\" + stuff
                res.append(f"[{stuff}]")
        else:
            res.append(re.escape(c))
    return "".join(res)


def _translate_path_pattern(pattern: str) -> re.Pattern:
    """编译含 ``/`` 的路径模式（支持 ``**`` 跨目录匹配）。

    - ``**`` 作为独立段：结尾时匹配其下任意深度（``src/**``），中间或开头时
      整段可省略（``**/*.py`` 同时匹配 ``a.py`` 与 ``x/y/a.py``）；
    - 其余段按 :func:`_translate_segment` 翻译，段内通配符不跨 ``/``；
    - 匹配对象是相对搜索根的 POSIX 路径（如 ``src/a/b.py``）。
    """
    segments = [seg for seg in pattern.split("/") if seg not in ("", ".")]
    parts: list[str] = []
    for index, seg in enumerate(segments):
        is_last = index == len(segments) - 1
        if seg == _GLOBSTAR:
            if is_last:
                # 结尾 globstar：匹配前缀之下的任意深度；单独 ** 匹配任意非空路径
                parts.append(".*" if parts else ".+")
            else:
                parts.append("(?:.*/)?")
            continue
        parts.append(_translate_segment(seg))
        if not is_last:
            parts.append("/")
    if not parts:
        # 规范化后无有效段（如 pattern 仅为 "/"）：永不匹配
        return re.compile(r"(?!)")
    return re.compile("(?s:" + "".join(parts) + r")\Z")


class GlobFunc(Func):
    """文件查找工具 — 在项目中查找文件和目录"""

    name = "glob"

    @classmethod
    def to_tool_schema(cls):
        return {
            "type": "function",
            "function": {
                "name": "glob",
                "description": (
                    "按文件名/路径模式（fnmatch 通配符，空格分隔多个模式）查找文件和目录，"
                    "支持 ** 跨目录（如 **/*.py 匹配任意层级，**/* 匹配全部条目）"
                    "与 {a,b} 大括号展开（如 **/*.{c,h,cpp,hpp,cc,py,mk}）；"
                    "返回相对路径列表；自动跳过 .git/node_modules 等目录，"
                    "无匹配时返回提示文本。type 限定 file/dir。找代码位置优先用它，"
                    "不得用 bash find/ls 替代（✗ bash find src -name '*.py' ↦ ✓ glob）。"
                    "例：glob({\"pattern\": \"**/*.py\", \"path\": \"src\", "
                    "\"type\": \"file\"})"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": (
                                "文件名或相对路径模式（fnmatch，空格分隔多个，"
                                "支持 ** 跨目录与 {a,b} 展开，如 **/*.{c,h,py}）"
                            ),
                        },
                        "path": {
                            "type": "string",
                            "description": "搜索根路径（默认当前目录，如 src）",
                        },
                        "type": {
                            "type": "string",
                            "description": "过滤类型：file / dir（默认全部）",
                            "enum": ["file", "dir"],
                        },
                    },
                    "required": ["pattern"],
                },
            },
        }

    @classmethod
    def display_params(cls, arguments: dict, max_len: int = 80) -> str:
        pattern = arguments.get("pattern", "")
        path = arguments.get("path", "")
        type_filter = arguments.get("type", "")
        extras = []
        if path:
            extras.append(f"in:{path}")
        if type_filter:
            extras.append(f"type:{type_filter}")
        display = cls._sanitize_display(pattern)
        if extras:
            display += f" ({', '.join(extras)})"
        return f"'{display}'"

    def __init__(
        self,
        pattern: str,
        path: str | None = None,
        type: str | None = None,
    ):
        super().__init__()
        self.pattern = pattern
        # 展开 ~（记忆目录位于 ~/.chat/memory/）
        self.root_path = os.path.expanduser(path) if path else os.getcwd()
        # 避免遮蔽 Python builtin type()，内部使用 filter_type
        self.filter_type = type  # "file", "dir", or None
        # 按是否含路径分隔符把模式分为名称模式 / 路径模式（见模块 docstring），
        # 先做大括号展开（**/*.{c,h} → **/*.c / **/*.h），并预编译为 regex，
        # 消除每次匹配时 fnmatch.translate + compile 开销。
        self._name_patterns_re: list[re.Pattern] = []
        self._path_patterns_re: list[re.Pattern] = []
        name_seen: set[str] = set()
        path_seen: set[str] = set()
        for raw in _split_patterns(self.pattern):
            for expanded in _expand_braces(raw):
                normalized = self._normalize_pattern(expanded)
                if not normalized:
                    continue
                if "/" in normalized:
                    if normalized in path_seen:
                        continue
                    path_seen.add(normalized)
                    self._path_patterns_re.append(_translate_path_pattern(normalized))
                else:
                    if normalized in name_seen:
                        continue
                    name_seen.add(normalized)
                    self._name_patterns_re.append(
                        re.compile(fnmatch.translate(normalized))
                    )

    @staticmethod
    def _normalize_pattern(pattern: str) -> str:
        """规范化单个模式：统一分隔符、去掉 ``./`` 前缀、首尾 ``/``。"""
        value = pattern.strip()
        if os.sep == "\\":
            # Windows：模型/用户可能写反斜杠分隔的路径模式
            value = value.replace("\\", "/")
        while value.startswith("./"):
            value = value[2:]
        # 绝对路径按「相对搜索根」处理（工具本身已限定在 path 之下查找）
        value = value.lstrip("/")
        # 尾随 / 仅表示目录层级，对匹配结果无影响
        return value.rstrip("/")

    def _matches_name(self, name: str) -> bool:
        """名称模式是否命中条目名（路径模式不参与根目录自身匹配）。"""
        return any(compiled.match(name) for compiled in self._name_patterns_re)

    def _matches(self, rel_posix: str, name: str) -> bool:
        """条目是否命中任一模式：路径模式匹配相对路径，名称模式匹配条目名。"""
        if any(compiled.match(rel_posix) for compiled in self._path_patterns_re):
            return True
        return self._matches_name(name)

    # ── 核心执行 ────────────────────────────────────────

    async def execute(self) -> str:
        """异步执行查找，返回格式化结果字符串"""
        try:
            root = Path(self.root_path).resolve()
            if not root.exists():
                return f"(路径不存在: {self.root_path})"
            if not root.is_dir():
                return f"(路径不是目录: {self.root_path})"

            results = await self._find_files_async(root)
            return self._format_results(results, root)
        except asyncio.CancelledError:
            return "(查找已被取消)"
        except Exception as e:
            logger.exception("查找异常: pattern=%s, path=%s", self.pattern, self.root_path)
            return f"(查找失败: {e})"

    async def _find_files_async(self, root: Path) -> list[Path]:
        """先尝试直接遍历（小型目录），大目录再用 executor"""
        # 先检查目录规模：一级条目很少时目录树通常不大，同步遍历即可
        try:
            entries = list(root.iterdir())
            # 小型目录（<200项）直接同步遍历
            if len(entries) < SMALL_DIR_ENTRY_LIMIT:
                return self._sync_find_files(root)
        except OSError:
            logger.debug("目录遍历跳过（权限或系统错误）: %s", root, exc_info=True)

        # 大目录走 executor，避免阻塞事件循环
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_find_files, root)

    def _sync_find_files(self, root: Path) -> list[Path]:
        """同步遍历目录树，按名称/路径模式匹配文件与目录。

        - 名称模式（不含 ``/``）匹配条目名；路径模式匹配相对搜索根的 POSIX
          路径，``**`` 可跨任意层目录；
        - 目录在**条目**层面判定（父目录遍历到即匹配）；
        - 遍历整棵目录树（排除目录除外），不做深度限制。
        """
        results: list[Path] = []

        if not self._name_patterns_re and not self._path_patterns_re:
            return results

        want_file = self.filter_type in (None, "file")
        want_dir = self.filter_type in (None, "dir")

        # 根目录自身参与名称匹配（既有语义：名称模式命中根目录名时返回根）；
        # 路径模式不匹配根自身——其相对路径为空，``**/*`` 只匹配其下条目。
        if want_dir and self._matches_name(root.name):
            results.append(root)

        for current, dirs, files in os.walk(root, topdown=True):
            try:
                current_path = Path(current)

                # ── 排除非源码目录 ──
                # 排除项自身不返回结果，也不再深入其子树
                dirs[:] = [d for d in dirs if not _should_exclude_dir(d)]

                rel_dir = current_path.relative_to(root)

                # ── 匹配目录名 / 相对路径 ──
                if want_dir:
                    for dirname in dirs:
                        rel_posix = (rel_dir / dirname).as_posix()
                        if self._matches(rel_posix, dirname):
                            results.append(current_path / dirname)

                # ── 匹配文件名 / 相对路径 ──
                if want_file:
                    for filename in files:
                        rel_posix = (rel_dir / filename).as_posix()
                        if self._matches(rel_posix, filename):
                            results.append(current_path / filename)

            except OSError as _perr:
                # 单文件/目录权限错误不中断整个遍历，记录后继续
                logger.debug("遍历目录时跳过不可访问条目: %s, error=%s", current, _perr)
            except Exception:
                logger.exception("遍历目录时发生意外错误: %s", current)

        return results

    # ── 结果格式化 ──────────────────────────────────────

    def _format_results(self, results: list[Path], root: Path) -> str:
        """将匹配结果格式化为结构化文本"""
        if not results:
            return f"查找「{self.pattern}」未找到结果"

        total = len(results)

        # 生成相对路径（用于显示） + 保留绝对路径（用于类型判断）
        try:
            display_items = []
            for p in results:
                try:
                    rel = p.relative_to(root)
                except ValueError:
                    rel = p
                display_items.append((p, rel))
        except Exception:
            display_items = [(p, p) for p in results]

        # 按（相对路径的目录部分，文件名）排序
        display_items.sort(key=lambda x: str(x[1]))

        # 构建输出
        type_label = {
            "file": "文件",
            "dir": "目录",
            None: "文件+目录",
        }.get(self.filter_type, "文件+目录")

        header = f"查找「{self.pattern}」in {root} 找到 {total} 个{type_label}:"

        parts = [header]

        for abs_path, rel_path in display_items:
            icon = "📄 " if abs_path.is_file() else "📁 "
            # 根目录自身显示为目录名而非 "."
            display_name = root.name if rel_path == Path(".") else rel_path
            parts.append(f"  {icon}{display_name}")

        return "\n".join(parts)

    # ── 显示（终端） ─────────────────────────────────────

    async def display(self) -> str:
        """终端显示：打印查找摘要和结果"""
        path_info = f" in:{self.root_path}" if self.root_path != os.getcwd() else ""
        type_info = f" type:{self.filter_type}" if self.filter_type else ""
        return await self._display_result_template(
            header=f"🔍 查找: {self.pattern}",
            extra_info=f"模式: {self.pattern}{path_info}{type_info}",
        )
