"""
find — 文件查找工具

在项目中查找文件和目录，支持 glob 模式匹配、类型(file/dir)过滤、深度控制。
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
from .base import Func, tool_metadata

logger = logging.getLogger(__name__)

# ── 魔法数字常量 ──────────────────────────────────────
SMALL_DIR_ENTRY_LIMIT = 200   # 小型目录条目阈值（直接同步遍历）
SMALL_DIR_DEPTH_LIMIT = 3     # 小型目录深度阈值


@tool_metadata(
    parallel_safe=True,
    requires_network=False,
    requires_terminal=False,
    timeout_estimate=0,
    category="code",
    priority=20,
    tool_category="read",
    description="在项目中查找文件和目录",
)
class FindFunc(Func):
    """文件查找工具 — 在项目中查找文件和目录"""

    name = "find"

    @classmethod
    def to_tool_schema(cls):
        return {
            "type": "function",
            "function": {
                "name": "find",
                "description": (
                    "在项目中按文件名模式（fnmatch 通配符）查找文件和目录，返回匹配路径列表。"
                    "引用任何文件路径前先用 find 确认其存在。"
                    "pattern 支持 * ? [...] 通配符，多模式空格分隔（OR）；"
                    "path 限定根目录，type 过滤 file/dir，depth 控制深度（0 无限制）。"
                    "自动排除 node_modules/.git/__pycache__ 等。无结果返回明确提示。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": (
                                "文件/目录名匹配模式，支持 shell 通配符（fnmatch 语法，不支持 ** 递归）。"
                                "多个模式用空格分隔（OR 匹配，满足其一即返回）。"
                                "\n\n"
                                "常用通配符："
                                "\n- * 任意字符：*.py（所有 Python 文件）、test_*（所有 test_ 开头）"
                                "\n- ? 单字符：config?.py（如 config1.py config2.py）"
                                "\n- [...] 字符集：[Tt]est.py（Test.py 或 test.py）"
                                "\n- [!...] 排除：[!.]*（非隐藏文件）"
                                "\n\n"
                                "示例："
                                "\n- 所有 Python：*.py"
                                "\n- 测试文件：test_*.py"
                                "\n- JSON 或 YAML：*.json *.yaml"
                                "\n- 按主文件名：main.py app.py"
                                "\n- 隐藏文件：.*"
                                "\n- 多数字后缀：test_*_v[0-9].py"
                                "\n\n"
                                "注意：该模式仅匹配文件名/目录名（basename），不匹配路径。"
                                "要限制搜索目录请使用 path 参数。"
                            ),
                        },
                        "path": {
                            "type": "string",
                            "description": (
                                "搜索根路径（可选）。缩小搜索范围可提高速度。"
                                "\n- 省略时：使用进程当前工作目录"
                                "\n- 指定子目录：如 'src/' 或 '/home/user/project/src'，只在该目录下递归搜索"
                                "\n- 多层嵌套：如 'src/tools/parsers/'"
                                "\n- 记忆目录：如 '.chat/memory/'"
                            ),
                        },
                        "type": {
                            "type": "string",
                            "description": (
                                "过滤类型（可选）。"
                                "\n- 'file'：只返回文件"
                                "\n- 'dir'：只返回目录"
                                "\n- 省略时：文件和目录都返回"
                                "\n\n"
                                "最佳实践："
                                "\n- 想找模块结构：type=\"dir\" + depth=1"
                                "\n- 想找特定文件：type=\"file\" + pattern=\"*.py\""
                            ),
                            "enum": ["file", "dir"],
                        },
                        "depth": {
                            "type": "integer",
                            "minimum": 0,
                            "description": "最大搜索深度（目录层级，0 无限制）。1 仅当前层，2 含子目录。",
                            "default": 0,
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
        depth: int = 0,
    ):
        super().__init__()
        self.pattern = pattern
        self.root_path = path or os.getcwd()
        # 避免遮蔽 Python builtin type()，内部使用 filter_type
        self.filter_type = type  # "file", "dir", or None
        self.depth = max(0, depth)
        # 预编译 fnmatch 模式为 regex，消除每次匹配时 fnmatch.translate + compile 开销
        self._patterns_re: list[re.Pattern] = [
            re.compile(fnmatch.translate(p))
            for p in self.pattern.split() if p.strip()
        ]

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
            # 小型目录（<200项）且深度不大时直接同步遍历
            if len(entries) < SMALL_DIR_ENTRY_LIMIT and (self.depth == 0 or self.depth <= SMALL_DIR_DEPTH_LIMIT):
                return self._sync_find_files(root)
        except (OSError, PermissionError):
            logger.debug("目录遍历跳过（权限或系统错误）: %s", root, exc_info=True)

        # 大目录或深度大的情况走 executor，避免阻塞事件循环
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_find_files, root)

    def _sync_find_files(self, root: Path) -> list[Path]:
        """同步遍历目录树，匹配符合条件的文件/目录"""
        results: list[Path] = []

        # 使用 __init__ 中预编译的 regex 模式列表（OR 逻辑）
        patterns_re = self._patterns_re
        if not patterns_re:
            return results

        # 根目录自身也参与匹配（避免 depth=1 时漏掉根目录本身）
        if self.filter_type in (None, "dir"):
            root_name = root.name
            for compiled_re in patterns_re:
                if compiled_re.match(root_name):
                    results.append(root)
                    break

        # 根目录层级深度（用于 depth 控制）
        root_depth = len(root.parts)

        for current, dirs, files in os.walk(root, topdown=True):
            try:
                current_path = Path(current)

                # ── 深度控制 ──
                current_depth = len(current_path.parts) - root_depth
                # depth=0 表示无限制，>0 时 current_depth=0 为根目录本身
                if self.depth > 0 and current_depth > self.depth:
                    # 修剪子树，不再深入
                    dirs.clear()
                    # depth 超过时跳过当前目录的条目（但根目录 depth=0 已处理）
                    continue

                # ── 排除非源码目录 ──
                dirs[:] = [d for d in dirs if not _should_exclude_dir(d)]

                # ── 根目录层级处理 ──
                # 根目录自身的目录名匹配已在 walk 之前提前处理
                # （os.walk 不将根目录自身作为条目出现在 dirs/files 中，
                #  因此须在 walk 前单独匹配根目录名）
                # 因此这里跳过目录名匹配，但根目录层级的文件仍需匹配
                if current_depth == 0:
                    # 匹配根目录层级的文件名
                    if self.filter_type in (None, "file"):
                        for fname in files:
                            for compiled_re in patterns_re:
                                if compiled_re.match(fname):
                                    results.append(current_path / fname)
                                    break
                    continue

                # ── 匹配目录名 ──
                if self.filter_type in (None, "dir"):
                    dir_name = current_path.name
                    for compiled_re in patterns_re:
                        if compiled_re.match(dir_name):
                            results.append(current_path)
                            break

                # ── 匹配文件名 ──
                if self.filter_type in (None, "file"):
                    for fname in files:
                        for compiled_re in patterns_re:
                            if compiled_re.match(fname):
                                results.append(current_path / fname)
                                break

            except (OSError, PermissionError) as _perr:
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
        depth_info = f" depth:{self.depth}" if self.depth > 0 else ""
        return await self._display_result_template(
            header=f"🔍 查找: {self.pattern}",
            extra_info=f"模式: {self.pattern}{path_info}{type_info}{depth_info}",
        )

    # ── 显示（Web） ─────────────────────────────────────

    async def web_display(self) -> str:
        """Web 模式：返回纯文本结果"""
        path_info = f" in:{self.root_path}" if self.root_path != os.getcwd() else ""
        type_info = f" type:{self.filter_type}" if self.filter_type else ""
        return await self._web_display_result_template(
            header=f"🔍 查找: {self.pattern}{path_info}{type_info}",
        )
