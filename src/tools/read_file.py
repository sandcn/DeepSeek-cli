from __future__ import annotations

import asyncio
import io
import logging
import os
import time as _time
from functools import lru_cache
import aiofiles
import aiofiles.os
from rich.syntax import Syntax
from rich.console import Console as RichConsole
from .base import Func, tool_metadata
from .file_ops import validate_path_security, check_file_size
from .encoding import async_detect_encoding, pick_best_decoding, FALLBACK_ENCODINGS
from ._constants import LARGE_FILE_THRESHOLD, MAX_FILE_SIZE_MB
from ..core.constants import CYAN, DIM, RESET, RED

_UNSUPPORTED_EXTENSIONS = frozenset({"txt", "text"})

# ── 结果字典键常量 ─────────────────────────────────
_LINE_NUMBERS_KEY = "original_line_numbers"
_CONTENT_KEY = "content"
_ERROR_KEY = "error"
_SUCCESS_KEY = "success"


@lru_cache(maxsize=256)
def _resolve_lexer_name(ext: str) -> str:
    """将文件扩展名转为安全的 Pygments lexer 名称，未知扩展默认用 text。"""
    if not ext or ext.lower() in _UNSUPPORTED_EXTENSIONS:
        return "text"
    try:
        from pygments.lexers import get_lexer_by_name
        get_lexer_by_name(ext)
        return ext
    except Exception:
        return "text"


@tool_metadata(
    parallel_safe=True,
    requires_network=False,
    requires_terminal=False,
    timeout_estimate=0,
    category="io",
    priority=10,
    tool_category="read",
    description="读取文件内容",
)
class ReadFileFunc(Func):
    name = "read_file"

    @classmethod
    def to_tool_schema(cls):
        return {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": (
                    "读取文件内容，返回文件文本。支持整文件读取或按行号范围读取"
                    "（start_line/end_line，含两端，行号从 1 开始）。"
                    "show_line_numbers 开启时在返回内容中为每行附加行号（默认关闭）。"
                    "首次读取某文件必须读完整内容（不设行号限制）以全面理解；"
                    "读取多个文件时并发调用多个 read_file。"
                    "返回：文件内容；错误以「(」开头（如「(文件不存在: xxx)」）；"
                    "编码自动检测（UTF-8/GBK/Latin-1），二进制/编码错误自动降级不崩溃；危险/系统关键路径被拒绝。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "文件路径，支持相对路径（如 src/main.py）或绝对路径。"
                        },
                        "start_line": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "起始行号（从 1 开始，含该行）。省略时从文件开头读取。"
                        },
                        "end_line": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "结束行号（含该行）。省略时读到文件末尾；start_line>end_line 时自动交换。"
                        },
                        "show_line_numbers": {
                            "type": "boolean",
                            "default": False,
                            "description": "是否在返回内容中为每行附加行号（行号从 1 开始；配合行号范围读取时从 start_line 起连续编号）。默认关闭。"
                        }
                    },
                    "required": ["path"]
                }
            }
        }

    @staticmethod
    def _validate_line_number(value, name: str) -> int | None:
        """验证并规范化行号参数，返回 int | None"""
        if value is None:
            return None
        try:
            n = int(value)
            if n < 1:
                Func._publish_tool_notice(f"警告：{name} 必须 >= 1，已自动调整为 1")
                return 1
            return n
        except (ValueError, TypeError):
            Func._publish_tool_notice(f"警告：{name} 应为整数，收到 {value}，已忽略该参数")
            return None

    @staticmethod
    def _coerce_bool(value) -> bool:
        """将 show_line_numbers 等布尔参数从 bool/字符串/数字归一化为 bool。"""
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        if isinstance(value, (int, float)):
            return value != 0
        return str(value).strip().lower() in ("1", "true", "yes", "y", "on")

    @staticmethod
    def _clamp_line(value) -> int | None:
        """将行号参数安全 clamp 为 >=1 的整数，非法/None 返回 None。

        仅做行号基础防护；start>end 的交换在 __init__ 中统一处理，
        保证直接构造与 from_args 行为一致。
        """
        if value is None:
            return None
        try:
            return max(1, int(value))
        except (ValueError, TypeError):
            return None

    @classmethod
    def from_args(cls, args):
        path = args.get("path") or args.get("paths")
        if path is None:
            raise ValueError("缺少必需参数: path")
        start_line = args.get("start_line")
        end_line = args.get("end_line")

        # 兼容旧的 paths 数组格式
        if isinstance(path, list):
            path = path[0] if path else ""
        if not path:
            raise ValueError("缺少有效路径: path")

        # 验证行号参数
        start_line = cls._validate_line_number(start_line, "start_line")
        end_line = cls._validate_line_number(end_line, "end_line")

        # 如果两者都提供且 start_line > end_line，交换并警告（▎通知 块）
        if start_line is not None and end_line is not None and start_line > end_line:
            Func._publish_tool_notice(
                f"警告：start_line ({start_line}) 大于 end_line ({end_line})，已自动交换"
            )
            start_line, end_line = end_line, start_line

        # 解析 show_line_numbers（布尔：兼容 bool/字符串/数字）
        show_line_numbers = cls._coerce_bool(args.get("show_line_numbers", False))

        return cls(path, start_line, end_line, show_line_numbers)

    @classmethod
    def display_params(cls, arguments: dict, max_len: int = 80) -> str:
        path = arguments.get("path") or arguments.get("paths", "")
        if isinstance(path, list):
            path = path[0] if path else ""
        if not path:
            return ""
        display = f"'{cls._sanitize_display(path)}'"
        extras = []
        start_line = arguments.get("start_line")
        end_line = arguments.get("end_line")
        if start_line is not None and end_line is not None:
            extras.append(f"L{start_line}-{end_line}")
        elif start_line is not None:
            extras.append(f"L{start_line}+")
        elif end_line is not None:
            extras.append(f"L1-{end_line}")
        if arguments.get("show_line_numbers"):
            extras.append("line-num")
        if extras:
            display += " " + " ".join(extras)
        return display

    def __init__(self, path, start_line=None, end_line=None, show_line_numbers=False):
        super().__init__()
        if not path:
            raise ValueError("缺少有效路径: path")
        validate_path_security(path)
        self.path = path
        self.encoding = "utf-8"
        self.errors = "strict"
        self.start_line = self._clamp_line(start_line)
        self.end_line = self._clamp_line(end_line)
        # 与 from_args 一致：start_line > end_line 时交换
        if self.start_line is not None and self.end_line is not None and self.start_line > self.end_line:
            self.start_line, self.end_line = self.end_line, self.start_line
        self.show_line_numbers = self._coerce_bool(show_line_numbers)

        self._file_result = None

    async def _determine_encoding(self, file_path):
        """读取文件全部字节并检测编码，返回 (encoding, raw_bytes)。

        合并编码检测和文件读取为一次 IO，并将已读取的 raw_bytes
        传给编码检测器，避免重复 IO 且使用整体数据进行检测，大幅
        提高 GBK 等中文编码的检测准确率。
        """
        async with aiofiles.open(file_path, 'rb') as f:
            raw_bytes = await f.read()
        encoding = await async_detect_encoding(file_path, raw_bytes=raw_bytes)
        return encoding, raw_bytes

    def _slice_lines(self, content: str) -> dict:
        """对已解码的完整 content 按行号范围切片，返回 _file_result 字典。

        使用 str.find('\\n') 索引定位替代全量 split+join，仅遍历到
        end_line 即停止。对「读文件末尾几行」场景（如 L1000-L1020）
        大幅减少计算——不再构建百万级字符串列表。
        """
        start = max(1, self.start_line) if self.start_line is not None else 1
        end = self.end_line  # 用户传入，可能为 None
        if end is not None and end < 1:
            end = 1

        if not content:
            return {
                _CONTENT_KEY: '',
                _LINE_NUMBERS_KEY: None,
                _ERROR_KEY: None,
                _SUCCESS_KEY: True,
            }

        # 归一化行尾：\r\n → \n,  lone \r → \n（仅在含 \r 时执行）
        if '\r' in content:
            content = content.replace('\r\n', '\n').replace('\r', '\n')
        normalized = content

        # 计算总行数：\n 的个数 + (末字符非 \n 时最后一行)
        total = normalized.count('\n')
        if not normalized.endswith('\n'):
            total += 1

        if start > total:
            return {
                _CONTENT_KEY: '',
                _LINE_NUMBERS_KEY: (start, total),
                _ERROR_KEY: f"(行号越界: 文件共 {total} 行，起始行 {start})",
                _SUCCESS_KEY: False,
            }

        if end is None or end > total:
            actual_end = total
        else:
            actual_end = end

        # 用 find 定位 start 行的起始位置（跳过 start-1 个换行符）
        pos = 0
        for _ in range(start - 1):
            nl = normalized.find('\n', pos)
            if nl == -1:
                break
            pos = nl + 1

        # 从 start 位置定位 end 行的结束位置（扫描 actual_end-start+1 个换行符）
        end_pos = pos
        lines_to_scan = actual_end - start + 1
        for _ in range(lines_to_scan):
            nl = normalized.find('\n', end_pos)
            if nl == -1:
                end_pos = len(normalized)
                break
            end_pos = nl + 1

        selected = normalized[pos:end_pos]

        return {
            _CONTENT_KEY: selected,
            _LINE_NUMBERS_KEY: (start, actual_end),
            _ERROR_KEY: None,
            _SUCCESS_KEY: True,
        }

    def _format_with_line_numbers(self, content: str) -> str:
        """为内容逐行附加行号前缀（返回给大模型的文本）。

        行号基准：行号范围读取时从实际起始行号（_LINE_NUMBERS_KEY[0]）起，
        整文件读取时从 1 起；末尾换行符不额外产生空行行号。
        """
        start = 1
        if self._file_result is not None and self._file_result.get(_LINE_NUMBERS_KEY) is not None:
            start = self._file_result[_LINE_NUMBERS_KEY][0]
        lines = content.split('\n')
        if lines and lines[-1] == '':
            lines = lines[:-1]
        if not lines:
            return content
        width = len(str(start + len(lines) - 1))
        return '\n'.join(
            f"{str(start + idx).rjust(width)}  {line}"
            for idx, line in enumerate(lines)
        )

    async def execute(self):
        """异步读取文件并返回内容（无UI输出）"""
        file_path = self.path

        # 检查文件是否存在
        if not await aiofiles.os.path.exists(file_path):
            self._file_result = {
                _CONTENT_KEY: None, _LINE_NUMBERS_KEY: None,
                _ERROR_KEY: f"(文件不存在: {file_path})",
                _SUCCESS_KEY: False
            }
            return self._file_result[_ERROR_KEY]

        # 文件大小上限（与 write_file 一致）：超过 MAX_FILE_SIZE_MB 拒绝，
        # 避免超大文件被全量读入引发内存尖峰（LLM 上下文也无法容纳）。
        try:
            await asyncio.to_thread(check_file_size, file_path, MAX_FILE_SIZE_MB)
        except ValueError as e:
            self._file_result = {
                _CONTENT_KEY: None, _LINE_NUMBERS_KEY: None,
                _ERROR_KEY: f"({e})",
                _SUCCESS_KEY: False,
            }
            return self._file_result[_ERROR_KEY]

        # 大文件感知（不阻断读取）——超出 LARGE_FILE_THRESHOLD 时通过通知提示
        try:
            _size = (await aiofiles.os.stat(file_path)).st_size
            if _size > LARGE_FILE_THRESHOLD:
                Func._publish_tool_notice(
                    f"提示：{file_path} 大小 {_size // (1024 * 1024)}MB，读取内容可能较大"
                )
        except OSError:
            pass

        try:
            # 统一编码策略：读全文件字节 → async_detect_encoding 检测 →
            # pick_best_decoding 候选解码。行号范围与整文件读取共用同一解码链路，
            # 避免同一文件因读取方式不同而得到不同编码结果（GBK 等中文编码一致）。
            actual_encoding, raw_bytes = await self._determine_encoding(file_path)
            decode_candidates = [actual_encoding]
            full_candidates = decode_candidates + [e for e in FALLBACK_ENCODINGS if e not in decode_candidates]
            final_encoding, content = await asyncio.to_thread(
                pick_best_decoding, raw_bytes, full_candidates,
            )
            if final_encoding != actual_encoding:
                logger = logging.getLogger(__name__)
                logger.info(
                    "编码回退: %s → %s (文件 %s)",
                    actual_encoding, final_encoding, file_path,
                )
            if self.start_line is not None or self.end_line is not None:
                self._file_result = self._slice_lines(content)
            else:
                self._file_result = {
                    _CONTENT_KEY: content,
                    _LINE_NUMBERS_KEY: None,
                    _ERROR_KEY: None,
                    _SUCCESS_KEY: True,
                }
        except Exception as e:
            self._file_result = {
                _CONTENT_KEY: None, _LINE_NUMBERS_KEY: None,
                _ERROR_KEY: f"(读取失败: {e})",
                _SUCCESS_KEY: False
            }

        if not self._file_result[_SUCCESS_KEY]:
            return self._file_result[_ERROR_KEY]

        content = self._file_result[_CONTENT_KEY]
        cleaned = content.replace('\r\n', '\n').replace('\r', '\n')
        if cleaned:
            if self.show_line_numbers:
                body = self._format_with_line_numbers(cleaned)
                return f"文件: {self.path}\n{body}"
            return f"文件: {self.path}\n{cleaned}"
        return f"(文件为空: {self.path})"

    # ── 公共 UI 辅助方法 ──

    async def _build_file_info_line(self, file_path: str, result: dict) -> str:
        """构建文件信息行（大小、修改时间、范围等），返回 ANSI 格式化字符串。"""
        exists = await aiofiles.os.path.exists(file_path)
        if not exists:
            return f"\n{CYAN}{file_path}{RESET}"

        try:
            stat = await aiofiles.os.stat(file_path)
        except OSError:
            return f"\n{CYAN}{file_path}{RESET}"
        size = stat.st_size
        mtime = _time.strftime('%Y-%m-%d %H:%M:%S', _time.localtime(stat.st_mtime))
        size_warning = ""
        if size > LARGE_FILE_THRESHOLD:
            size_warning = f" {DIM}(大文件){RESET}"

        range_info = ""
        if result.get(_LINE_NUMBERS_KEY) is not None:
            start, end = result[_LINE_NUMBERS_KEY]
            if end is not None:
                range_info = f" {DIM}L{start}-{end}{RESET}"
            else:
                range_info = f" {DIM}L{start}+{RESET}"

        return f"\n{CYAN}{file_path}{RESET} {DIM}{size}B {mtime}{size_warning}{range_info}{RESET}"

    def _build_syntax(self, result: dict, file_path: str) -> Syntax | None:
        """从 result 构建 Syntax 对象（含 lexer 解析和 fallback），返回 Syntax 或 None。"""
        if not result[_CONTENT_KEY]:
            return None
        try:
            ext = os.path.splitext(file_path)[1].lstrip('.')
            lexer = _resolve_lexer_name(ext)
            start_line = 1
            if result.get(_LINE_NUMBERS_KEY) is not None:
                start_line = result[_LINE_NUMBERS_KEY][0]
            return Syntax(
                result[_CONTENT_KEY], lexer,
                line_numbers=True,
                start_line=start_line,
                highlight_lines=set(),
                theme="monokai",
                background_color="default",
            )
        except Exception:
            fallback_start = 1
            if result.get(_LINE_NUMBERS_KEY) is not None:
                fallback_start = result[_LINE_NUMBERS_KEY][0]
            return Syntax(
                result[_CONTENT_KEY], "python3",
                line_numbers=True,
                start_line=fallback_start,
                theme="monokai",
                background_color="default",
            )

    def _render_syntax_to_output(self, file_path: str, result: dict) -> None:
        """将语法高亮渲染为 ANSI 字符串，通过 EventBus 上屏。"""
        syntax = self._build_syntax(result, file_path)
        if syntax is None:
            return

        buf = io.StringIO()
        ansi_console = RichConsole(file=buf, force_terminal=True)
        ansi_console.print(syntax)
        output = buf.getvalue()
        if output:
            Func._publish_tool_text(output)

    async def display(self):
        """异步显示文件内容并返回给大模型"""
        output = await self.execute()

        if not self._file_result[_SUCCESS_KEY]:
            Func._publish_tool_text(f"  {RED}x {self._file_result[_ERROR_KEY]}{RESET}")
            return output

        file_path = self.path
        result = self._file_result

        info_line = await self._build_file_info_line(file_path, result)
        Func._publish_tool_text(info_line)

        self._render_syntax_to_output(file_path, result)

        return output
