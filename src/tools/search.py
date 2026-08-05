"""
search — 代码搜索工具（三路引擎）

搜索策略（优先顺序）：
  1. ripgrep (rg) — 最快，有则优先
  2. grep — 次选，系统自带
  3. 纯 Python — 零外部依赖兜底

支持：
- 正则搜索
- 按文件类型/路径过滤
- 自动排除非源码目录
- 结构化返回结果（文件:行号:内容）
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
import re

from ._constants import (
    EXCLUDED_DIRS,
    GREP_EXCLUDE_DIRS,
    GREP_EXCLUDE_FILES,
    RG_EXCLUDE_GLOBS,
    should_exclude_dir,
)
from .base import Func, tool_metadata

logger = logging.getLogger(__name__)

# ── 魔法数字常量 ──────────────────────────────────────
_BLOCK_SIZE = 1024                   # 块大小（字节）
BINARY_CHECK_SIZE = 512              # 二进制检测读取字节数
SMALL_FILE_LIMIT = 512 * _BLOCK_SIZE        # 小文件阈值（512KB）
LARGE_FILE_LIMIT = 10 * _BLOCK_SIZE * _BLOCK_SIZE  # 大文件阈值（10MB）
BUFFER_SIZE = 65536                  # 文件读取缓冲区大小

# ── 命令行工具检测 ────────────────────────────────────

_COMMAND_CACHE: dict[str, bool] = {}

async def _check_command_available(cmd: str) -> bool:
    """检测系统是否安装命令行工具（结果缓存，避免重复探测）"""
    if cmd in _COMMAND_CACHE:
        return _COMMAND_CACHE[cmd]
    try:
        proc = await asyncio.create_subprocess_exec(
            cmd, "--version",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        rc = await proc.wait()
        _COMMAND_CACHE[cmd] = rc == 0
    except FileNotFoundError:
        _COMMAND_CACHE[cmd] = False
    return _COMMAND_CACHE[cmd]

async def _check_rg() -> bool:
    """检测系统是否安装 ripgrep"""
    return await _check_command_available("rg")

async def _check_grep() -> bool:
    """检测系统是否安装 grep"""
    return await _check_command_available("grep")

# ── 纯 Python 辅助 ────────────────────────────────────

def _is_binary(data: bytes) -> bool:
    """检测前 512 字节是否包含 null 字节来判断是否为二进制文件"""
    return b"\0" in data[:8192]

def _matches_any(text: str) -> bool:
    """text 是否匹配 GREP_EXCLUDE_FILES 中的任意一个模式（使用预编译 regex）"""
    for compiled_re in _GREP_EXCLUDE_RES:
        if compiled_re.match(text):
            return True
    return False

# ── 预编译的排除文件 regex（来自 GREP_EXCLUDE_FILES），
# 消除 _matches_any 中每次 fnmatch 内部的 translate+compile 开销
_GREP_EXCLUDE_RES: list[re.Pattern] = [
    re.compile(fnmatch.translate(p)) for p in GREP_EXCLUDE_FILES
]

# ── 工具类 ────────────────────────────────────────────

@tool_metadata(
    parallel_safe=True,
    requires_network=False,
    requires_terminal=False,
    timeout_estimate=0,
    category="code",
    priority=20,
    tool_category="read",
    description="在项目源码中搜索正则模式",
)
class SearchFunc(Func):
    """代码搜索工具 — 在项目源码中搜索正则模式"""

    name = "search"

    @classmethod
    def to_tool_schema(cls):
        return {
            "type": "function",
            "function": {
                "name": "search",
                "description": (
                    "在项目源码中搜索文件内容，支持正则表达式。自动排除非源码目录。"
                    "\n\n"
                    "【防幻觉】引用任何配置项/环境变量前，先用 search 搜索项目内定义确认其存在，禁止凭记忆虚构。"
                    "\n\n"
                    "参数说明："
                    "\n- query（必填）：搜索模式（始终按正则表达式处理）"
                    "\n- path（可选）：搜索路径范围，默认当前项目根目录。可指定子目录如 src/ 或单个文件如 src/main.py"
                    "\n- include（可选）：文件类型过滤，如 *.py 只搜索 Python 文件、*.py *.js 搜索多种类型。默认搜索所有文件"
                    "\n\n"
                    "【搜索策略（按场景选择）】"
                    "\n- **精确搜索（已知符号名）**：直接搜符号名原样。若结果=0，补搜命名风格变体（snake_case|camelCase|PascalCase）。"
                    "\n  query=\"calculate_total\"   →  精确匹配函数名"
                    "\n  query=\"calculate_total|CalculateTotal\"   →  覆盖命名风格变体"
                    "\n- **模糊/跨语言搜索（未知命名、中英混编）**：中文+英文+同近意词。"
                    "\n  query=\"用户|user|account|member\"   →  跨语言全覆盖"
                    "\n  query=\"config|配置|setting\"   →  中英文混合搜索"
                    "\n  query=\"timeout|超时|超时时间\"   →  中英同义覆盖"
                    "\n- **调用链搜索**：加 `(` 过滤 import/注释噪音。"
                    "\n  query=\"handle_message(\"   →  纯调用方（无 import/注释）"
                    "\n  query=\"ClassName.method(\"   →  精确类方法调用"
                    "\n- **多符号联合搜索**：用 `|` 一次搜多个符号。"
                    "\n  query=\"from src\\.|import src\\.\"   →  搜所有 import 语句"
                    "\n  query=\"def |class \"   →  搜所有定义"
                    "\n  query=\"TODO|FIXME|HACK\"   →  搜所有待办标记"
                    "\n- **行首/行尾/单词边界**：用正则锚点精确定位。"
                    "\n  query=\"^class \"   →  行首类定义"
                    "\n  query=\"^    def \"   →  缩进的方法定义"
                    "\n  query=\"\\bassert\\b\"   →  单词边界精确匹配"
                    "\n- **多关键词 AND**：正则无原生 AND，用两次 search 组合。"
                    "\n\n"
                    "【边界信息】"
                    "\n- 自动排除非源码目录：node_modules, __pycache__, .git, venv, dist, build 等"
                    "\n- query 始终作为正则处理，支持完整正则语法（如 .* | \\b ^ $ \\d \\w 等）"
                    "\n- 搜索结果按文件路径排序输出"
                    "\n- 搜索无结果时返回明确提示"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "搜索模式（始终按正则表达式处理）。支持中英文混合搜索。支持 .* | \\b ^ $ \\d \\w 等完整正则语法。"
                                "\n\n"
                                "常用模式示例："
                                "\n- 精确符号：search query=\"func_name(\""
                                "\n- 多命名变体：search query=\"snake_case|camelCase|PascalCase\""
                                "\n- 中英混合：search query=\"用户|user|account\""
                                "\n- 调用链：search query=\"ClassName.method(\""
                                "\n- 多符号 OR：search query=\"def |class \""
                                "\n- 行首锚定：search query=\"^import \""
                                "\n- 单词边界：search query=\"\\bcount\\b\""
                            ),
                        },
                        "path": {
                            "type": "string",
                            "description": (
                                "搜索路径范围（可选）。缩小范围可提高搜索精度。"
                                "\n- 省略时：搜索当前工作目录下的所有文件"
                                "\n- 指定子目录：如 'src/' 仅搜索 src 目录"
                                "\n- 指定文件：如 'src/main.py' 仅搜索单个文件"
                                "\n- 指定多级子目录：如 'src/tools/'"
                                "\n- 记忆搜索：如 '.chat/memory/'"
                            ),
                        },
                        "include": {
                            "type": "string",
                            "description": (
                                "文件类型过滤，空格分隔多个模式（可选）。"
                                "\n- '*.py'：仅搜索 Python 文件"
                                "\n- '*.py *.js *.ts'：搜索多类型文件"
                                "\n- '*.md'：仅搜索 Markdown 文件"
                                "\n- '*.json *.yaml *.toml'：搜索配置文件"
                                "\n- 省略时：搜索所有文件类型"
                            ),
                        },
                    },
                    "required": ["query"],
                },
            },
        }

    @classmethod
    def display_params(cls, arguments: dict, max_len: int = 80) -> str:
        query = arguments.get("query", "")
        path = arguments.get("path", "")
        include = arguments.get("include", "")
        display = cls._sanitize_display(query)
        extras = []
        if path:
            extras.append(f"in:{path}")
        if include:
            extras.append(f"type:{include}")
        if extras:
            display += f" ({', '.join(extras)})"
        if len(display) > max_len:
            display = display[: max_len - 3] + "..."
        return f"'{display}'"

    def __init__(
        self,
        query: str,
        path: str | None = None,
        include: str | None = None,
    ):
        super().__init__()
        self.query = query
        self.path = path or "."
        self.include = include

        # 引擎检测结果缓存（惰性初始化，首次 execute 时填充）
        self._has_rg: bool | None = None
        self._has_grep: bool | None = None

        # 编译正则（纯 Python 兜底用）
        self._pattern: re.Pattern | None = None
        self._regex_error: str | None = None

        # 防止灾难性回溯：检测复杂正则模式，过度复杂时降级为纯字面量搜索
        if (
            self.query.count("+") > 3
            or self.query.count("*") > 3
            or ".*" in self.query
        ):
            search_pattern = re.escape(self.query)
        else:
            search_pattern = self.query

        try:
            self._pattern = re.compile(search_pattern)
        except re.error as e:
            self._pattern = None
            self._regex_error = str(e)

        # 预编译 include 过滤模式
        if self.include:
            self._include_res = [
                re.compile(fnmatch.translate(p.strip()))
                for p in self.include.split()
                if p.strip()
            ]
        else:
            self._include_res = []

    # ── 核心执行（三路引擎选择）─────────────────────────

    async def execute(self) -> str:
        """执行搜索 — 优先 rg → 其次 grep → 纯 Python 兜底"""
        try:
            # 仅在首次调用时检测引擎（后续复用实例缓存）
            if self._has_rg is None:
                results = await asyncio.gather(_check_rg(), _check_grep(), return_exceptions=True)
                self._has_rg = results[0] if not isinstance(results[0], Exception) else False
                self._has_grep = results[1] if not isinstance(results[1], Exception) else False
                has_rg = self._has_rg
                has_grep = self._has_grep
            else:
                has_rg = self._has_rg
                has_grep = self._has_grep

            # 第 1 优先：ripgrep
            if has_rg:
                return await self._search_with_rg()
            # 第 2 优先：grep
            if has_grep:
                return await self._search_with_grep()
            # 兜底：纯 Python
            return self._format_results(self._search_py())
        except asyncio.CancelledError:
            return "(搜索已被取消)"
        except Exception as e:
            logger.exception("搜索异常: %s", self.query[:200])
            return f"(搜索失败: {e})"

    # ═══════════════════════════════════════════════════
    # 引擎 1：ripgrep
    # ═══════════════════════════════════════════════════

    async def _search_with_rg(self) -> str:
        """使用 ripgrep (rg) 搜索"""
        cmd = ["rg", "--line-number", "--no-heading", "--color", "never", "--with-filename"]

        for d in RG_EXCLUDE_GLOBS:
            cmd.extend(["--glob", f"!{d}"])

        if self.include:
            for pat in self.include.split():
                pat = pat.strip()
                if pat:
                    cmd.extend(["--glob", pat])

        cmd.append("--regexp")

        cmd.append(self.query)
        cmd.append(self.path)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        output = stdout.decode("utf-8", errors="replace")
        err_text = stderr.decode("utf-8", errors="replace").strip()

        if proc.returncode not in (0, 1):
            return f"(rg 搜索失败, code={proc.returncode}: {err_text or '未知错误'})"

        if not output.strip():
            return f"搜索「{self.query}」未找到结果"

        return self._format_lines(output.strip().splitlines())

    # ═══════════════════════════════════════════════════
    # 引擎 2：grep
    # ═══════════════════════════════════════════════════

    async def _search_with_grep(self) -> str:
        """使用 grep 搜索（rg 不可用时的回退方案）

        使用 create_subprocess_exec + 参数列表替代 create_subprocess_shell，
        消除 shell 解析层，从根本上防止命令注入。
        """
        cmd = ["grep", "-r", "-n", "-a", "-H", "-E"]

        for d in GREP_EXCLUDE_DIRS:
            cmd.extend(["--exclude-dir", d])

        if GREP_EXCLUDE_FILES:
            for d in GREP_EXCLUDE_FILES:
                cmd.extend(["--exclude", d])

        if self.include:
            for pat in self.include.split():
                pat = pat.strip()
                if pat:
                    cmd.extend(["--include", pat])

        cmd.append(self.query)
        cmd.append(self.path)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        output = stdout.decode("utf-8", errors="replace")
        err_text = stderr.decode("utf-8", errors="replace").strip()

        if proc.returncode not in (0, 1):
            return f"(grep 搜索失败, code={proc.returncode}: {err_text or '未知错误'})"

        if not output.strip():
            return f"搜索「{self.query}」未找到结果"

        return self._format_lines(output.strip().splitlines())

    # ═══════════════════════════════════════════════════
    # 引擎 3：纯 Python（兜底）
    # ═══════════════════════════════════════════════════

    def _should_exclude_by_pattern(self, filename: str) -> bool:
        """判断文件名是否应被排除（如 *.egg-info）"""
        return _matches_any(filename)

    def _matches_include(self, filename: str) -> bool:
        """检查文件名是否匹配 include 过滤条件（使用预编译 regex）"""
        if not self._include_res:
            return True
        for compiled_re in self._include_res:
            if compiled_re.match(filename):
                return True
        return False

    def _collect_files(self) -> list[str]:
        """递归收集需要搜索的文件路径列表"""
        root_path = os.path.abspath(self.path)

        if os.path.isfile(root_path):
            if self._should_exclude_by_pattern(os.path.basename(root_path)):
                return []
            if not self._matches_include(os.path.basename(root_path)):
                return []
            return [root_path]

        if not os.path.isdir(root_path):
            return []

        files: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root_path):
            for i in range(len(dirnames) - 1, -1, -1):
                if should_exclude_dir(dirnames[i]):
                    del dirnames[i]

            for fname in filenames:
                if self._should_exclude_by_pattern(fname):
                    continue
                if not self._matches_include(fname):
                    continue
                files.append(os.path.join(dirpath, fname))

        files.sort()
        return files

    def _search_py(self) -> list[tuple[str, int, str]]:
        """纯 Python 搜索：遍历文件逐行匹配

        返回: [(filepath, line_number, content), ...]
        """
        if self._pattern is None:
            return []

        files = self._collect_files()
        results: list[tuple[str, int, str]] = []

        for filepath in files:
            try:
                self._search_file_py(filepath, results)
            except Exception:
                logger.debug("搜索文件时跳过 %s", filepath, exc_info=True)

        return results

    def _search_file_py(
        self,
        filepath: str,
        results: list[tuple[str, int, str]],
    ) -> None:
        """在单个文件中搜索匹配行（纯 Python）"""
        # 二进制检测
        try:
            with open(filepath, "rb") as f:
                head = f.read(BINARY_CHECK_SIZE)
        except OSError:
            return

        if _is_binary(head):
            return

        pattern = self._pattern  # 局部变量引用，减少属性查找开销

        # 逐行搜索
        try:
            with open(filepath, "rb", buffering=BUFFER_SIZE) as f:
                f.seek(0, os.SEEK_END)
                fsize = f.tell()
                f.seek(0)

                if fsize < LARGE_FILE_LIMIT:  # < 10MB，一次读入
                    raw = f.read()
                    text = raw.decode("utf-8", errors="replace")
                    for line_num, line in enumerate(text.splitlines(keepends=False), 1):
                        if pattern.search(line):  # 内联 _line_matches，消除函数调用开销
                            results.append((filepath, line_num, line))
                else:
                    # 大文件逐行
                    line_num = 0
                    for raw_line in f:
                        line_num += 1
                        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                        if pattern.search(line):  # 内联 _line_matches，消除函数调用开销
                            results.append((filepath, line_num, line))
        except OSError:
            pass

    # ── 结果格式化（rg/grep/Python 三路共用）───────────

    @staticmethod
    def _parse_line(line: str) -> tuple[str, int, str] | None:
        """解析单行搜索结果，返回 (filepath, line_number, content) 或 None"""
        match = re.match(r"^(.+?):(\d+):(.*)", line)
        if match:
            return match.group(1), int(match.group(2)), match.group(3)
        return None

    def _format_lines(self, lines: list[str]) -> str:
        """将 rg/grep 原始行格式化为结构化输出"""
        parsed: list[tuple[str, int, str]] = []
        skipped_binary = 0

        for line in lines:
            if not line.strip():
                continue
            if "binary file matches" in line.lower() or "matches (found)" in line.lower():
                skipped_binary += 1
                continue
            result = self._parse_line(line)
            if result:
                parsed.append(result)

        return self._format_results(parsed, skipped_binary)

    def _format_results(
        self,
        results: list[tuple[str, int, str]],
        skipped_binary: int = 0,
    ) -> str:
        """将匹配结果格式化为结构化输出"""
        total = len(results)

        if total == 0:
            if self._pattern is None:
                return f"(正则表达式错误: {self._regex_error})"
            return f"搜索「{self.query}」未找到结果"

        results.sort(key=lambda x: (x[0], x[1]))

        parts = [f"搜索「{self.query}」共找到 {total} 处匹配:"]

        current_file = None
        for filepath, lineno, content in results:
            if filepath != current_file:
                current_file = filepath
                parts.append("")
                parts.append(f"  {filepath}:")
            display_content = content.strip()
            if len(display_content) > 200:
                display_content = display_content[:200] + "..."
            parts.append(f"    L{lineno}:  {display_content}")

        if skipped_binary:
            parts.append(f"\n  (跳过了 {skipped_binary} 个二进制文件匹配)")

        return "\n".join(parts)

    # ── 显示 ──

    async def _resolve_engine(self) -> str:
        """解析并缓存搜索引擎名称（"rg" / "grep" / "Python"）。"""
        if self._has_rg is None:
            results = await asyncio.gather(
                _check_rg(), _check_grep(), return_exceptions=True
            )
            self._has_rg = results[0] if not isinstance(results[0], Exception) else False
            self._has_grep = results[1] if not isinstance(results[1], Exception) else False
        return "rg" if self._has_rg else ("grep" if self._has_grep else "Python")

    async def display(self) -> str:
        """终端显示：打印搜索摘要和结果"""
        engine = await self._resolve_engine()
        path_info = f" in:{self.path}" if self.path != "." else ""
        include_info = f" type:{self.include}" if self.include else ""
        return await self._display_result_template(
            header=f"🔍 搜索: {self.query}",
            extra_info=f"引擎: {engine}{path_info}{include_info}",
        )

    async def web_display(self) -> str:
        """Web 模式：返回纯文本结果"""
        engine = await self._resolve_engine()
        path_info = f" in:{self.path}" if self.path != "." else ""
        return await self._web_display_result_template(
            header=f"🔍 搜索: {self.query} ({engine}{path_info})",
        )
