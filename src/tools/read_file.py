from __future__ import annotations

import asyncio
import logging
import os
import sys
import time as _time
from functools import lru_cache
import aiofiles
import aiofiles.os
from rich.syntax import Syntax
from .base import Func, tool_metadata
from .file_ops import validate_path_security
from .encoding import async_detect_encoding
from ._constants import LARGE_FILE_THRESHOLD, CATCHALL_ENCODINGS as _CATCHALL_ENCODINGS
from ..core.constants import CYAN, DIM, RESET, RED
from ..ui.colors import console
from ..ui._lock import locked_print, _try_acquire_output_lock

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
    return ext


@tool_metadata(
    parallel_safe=True,
    requires_network=False,
    requires_terminal=False,
    timeout_estimate=0,
    category="io",
    priority=10,
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
                    "读取文件内容。支持 path + start_line/end_line 配合使用："
                    "①仅 path → 读取整个文件；②path+start_line → 从 start_line 读至末尾；"
                    "③path+end_line → 从第1行读至 end_line；"
                    "④同时指定 start_line+end_line → 读取闭区间 [start_line, end_line]。"
                    "修改文件前必须先调用此工具确认当前内容。"
                    "读取多个文件时应并发调用多个read_file。\n\n"
                    "【使用规则】\n"
                    "- 首次读取：第一次读取某个文件时，必须读取完整内容（不设 start_line/end_line 限制），"
                    "确保全面理解后再操作。后续读取同一文件时可设行号范围进行分段读取。\n\n"
                    "【边界信息】\n"
                    "- 大文件(>10MB)会在UI上显示警告标记，仍正常读取\n"
                    "- 二进制文件/编码错误自动降级为replace模式，不会崩溃\n"
                    "- start_line<1自动调整为1，start_line>end_line自动交换\n"
                    "- 路径安全校验：拒绝路径穿越攻击（如../../etc/passwd）\n"
                    "- 文件不存在时返回明确错误信息「文件不存在: xxx」\n"
                    "- 自动检测编码（UTF-8 / GBK / Latin-1 等），GBK 中文文件亦可正常读取"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "文件路径，支持相对路径（如 \"src/main.py\"）和绝对路径（如 \"/home/user/project/main.py\"）。示例：\"src/main.py\""
                        },
                        "start_line": {
                            "type": "integer",
                            "description": "起始行号（行号从1开始，包含该行）。如果小于1自动调整为1。不指定时从文件开头读取。"
                        },
                        "end_line": {
                            "type": "integer",
                            "description": "结束行号（包含该行）。如果 start_line>end_line 则自动交换两者。不指定时读到文件末尾。"
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
                locked_print(f"警告：{name} 必须 >= 1，已自动调整为 1")
                return 1
            return n
        except (ValueError, TypeError):
            locked_print(f"警告：{name} 应为整数，收到 {value}，已忽略该参数")
            return None

    @classmethod
    def from_args(cls, args):
        path = args.get("path") or args.get("paths")
        start_line = args.get("start_line")
        end_line = args.get("end_line")

        # 兼容旧的 paths 数组格式
        if isinstance(path, list):
            path = path[0] if path else ""

        # 验证行号参数
        start_line = cls._validate_line_number(start_line, "start_line")
        end_line = cls._validate_line_number(end_line, "end_line")

        # 如果两者都提供且 start_line > end_line，交换并警告
        if start_line is not None and end_line is not None and start_line > end_line:
            locked_print(f"警告：start_line ({start_line}) 大于 end_line ({end_line})，已自动交换")
            start_line, end_line = end_line, start_line

        return cls(path, start_line, end_line)

    @classmethod
    def display_params(cls, arguments: dict, max_len: int = 80) -> str:
        path = arguments.get("path") or arguments.get("paths", "")
        if isinstance(path, list):
            path = path[0] if path else ""
        if not path:
            return ""
        display = f"'{cls._sanitize_display(path)}'"
        extras = []
        if arguments.get("start_line") is not None:
            extras.append(f"offset:{arguments['start_line']}")
        if arguments.get("end_line") is not None:
            extras.append(f"limit:{arguments['end_line']}")
        if extras:
            display += " " + " ".join(extras)
        return display

    def __init__(self, path, start_line=None, end_line=None):
        super().__init__()
        validate_path_security(path)
        self.path = path
        self.encoding = "utf-8"
        self.errors = "strict"
        self.start_line = start_line
        self.end_line = end_line

        self._file_result = None

    async def _determine_encoding(self, file_path):
        """读取文件全部原始字节并检测编码，返回 (encoding, raw_bytes)

        合并编码检测和文件读取为一次 IO（仅读取一次文件全部字节），
        并将已读取的 raw_bytes 传给编码检测器，避免重复 IO 且使用整
        体数据进行检测，大幅提高 GBK 等中文编码的检测准确率。
        """
        async with aiofiles.open(file_path, 'rb') as f:
            raw_bytes = await f.read()
        encoding = await async_detect_encoding(file_path, raw_bytes=raw_bytes)
        return encoding, raw_bytes

    async def _read_content(self, file_path: str, encoding: str, errors: str) -> dict:
        """读取文件全部内容，返回 {'content', 'original_line_numbers', 'error', 'success'} 结构。

        注意：行号范围切片逻辑已统一至 _slice_lines() 中，
        该方法已无调用方，保留仅作向后兼容（子类覆盖）。
        """
        async with aiofiles.open(file_path, 'r', encoding=encoding, errors=errors) as f:
            content = await f.read()
        return {
            _CONTENT_KEY: content,
            _LINE_NUMBERS_KEY: None,
            _ERROR_KEY: None,
            _SUCCESS_KEY: True,
        }

    def _try_decode(self, raw_bytes: bytes, candidates: list[str]) -> tuple[str, str]:
        """尝试用候选编码列表解码字节，返回 (encoding, content)。

        依次尝试每个编码，优先选零替代字符、strict 模式成功的编码。
        如果所有编码都失败，回退到第一个编码的 replace 模式。

        注意：latin-1 / iso-8859-* / cp125x 等「通吃编码」能解码任意字
        节且无 \ufffd，所以遇到它们时不立即返回——须遍历全部候选再择优。
        通吃编码评分大幅降低，防止它们因零替代字符的固有属性覆盖真实编码。
        """
        from .encoding import FALLBACK_ENCODINGS

        fallback = candidates + [e for e in FALLBACK_ENCODINGS if e not in candidates]
        best_enc = fallback[0]
        best_content = ""
        best_score = -1

        for enc in fallback:
            try:
                decoded = raw_bytes.decode(enc, errors='strict')
                repl_count = decoded.count('\ufffd')
                if repl_count == 0 and enc.lower() not in _CATCHALL_ENCODINGS:
                    return enc, decoded  # 完美解码且非通吃编码，立即返回
                if enc.lower() in _CATCHALL_ENCODINGS:
                    score = 60  # 通吃编码降分——解码任意字节是无意义的"成功"
                elif repl_count == 0:
                    score = 100  # 非通吃编码完美解码（理论上不会走到这里）
                else:
                    score = 100 - repl_count * 2  # 非通吃编码有少量损坏字节
            except UnicodeDecodeError:
                try:
                    decoded = raw_bytes.decode(enc, errors='replace')
                    repl_count = decoded.count('\ufffd')
                    # replace 模式评分基础 70——高于通吃编码的 60，确保非通吃编码
                    # 即使 strict 失败且有少量损坏字节，仍优于通吃编码（如 latin-1
                    # 虽然 0 替代字符但内容完全错误）。仅当损坏较多时才让通吃编码胜出。
                    score = 70 - repl_count
                except Exception:
                    continue

            if score > best_score:
                best_score = score
                best_enc = enc
                best_content = decoded

        if best_content:
            return best_enc, best_content
        # 终极降级：用第一个编码的 replace 模式
        return candidates[0], raw_bytes.decode(candidates[0], errors='replace')

    def _slice_lines(self, content: str) -> dict:
        """对已解码的完整 content 按行号范围切片，返回 _file_result 字典。

        将 \r\n/\r 归一化为 \n（匹配 Python 文本模式通用换行行为），
        然后在内存中按行切片，消除第2次文件 IO。
        """
        # 归一化行尾：\r\n → \n,  lone \r → \n（匹配文本模式通用换行）
        normalized = content.replace('\r\n', '\n').replace('\r', '\n')

        # 分割为带行尾符的行列表（匹配 async for line in f: 的逐行行为）
        if not normalized:
            all_lines = []
        elif normalized.endswith('\n'):
            all_lines = [l + '\n' for l in normalized[:-1].split('\n')]
        else:
            parts = normalized.split('\n')
            all_lines = [l + '\n' for l in parts[:-1]] + [parts[-1]]

        total = len(all_lines)
        start = max(1, self.start_line) if self.start_line is not None else 1
        end = self.end_line  # 用户传入，可能为 None

        if total == 0 or start > total:
            # 行号越界：返回空内容，actual_end 匹配原始 _read_content 行为
            selected = ''
            actual_end = end  # 可为 None（原始行为：end 是用户传入值）
        else:
            if end is None or end > total:
                actual_end = total
            else:
                actual_end = end
            selected = ''.join(all_lines[start - 1:actual_end])

        return {
            _CONTENT_KEY: selected,
            _LINE_NUMBERS_KEY: (start, actual_end),
            _ERROR_KEY: None,
            _SUCCESS_KEY: True,
        }

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

        try:
            if self.start_line is not None or self.end_line is not None:
                # ===== 行号范围场景：全量读字节做编码检测，内存切片 =====
                # 先读全文件字节确保编码检测准确（对于大文件中后段才出现
                # 中文的场景，仅读头部 64KB 可能检测不到正确编码）
                actual_encoding, raw_bytes = await self._determine_encoding(file_path)
                # 多编码回退：_try_decode 用全量 raw_bytes 择优，返回完整解码内容
                decode_candidates = [actual_encoding]
                final_encoding, content = await asyncio.to_thread(
                    self._try_decode, raw_bytes, decode_candidates,
                )
                if final_encoding != actual_encoding:
                    logger = logging.getLogger(__name__)
                    logger.info(
                        "编码回退(行范围): %s → %s (文件 %s)",
                        actual_encoding, final_encoding, file_path,
                    )
                # 内存行切片：_try_decode 已返回完整解码内容，切片消除第2次文件 IO
                self._file_result = self._slice_lines(content)
            else:
                # ===== 无行号范围：原逻辑（全量读取 + 多编码回退） =====
                actual_encoding, raw_bytes = await self._determine_encoding(file_path)
                # 多编码回退解码：检测到的编码排首位，备选编码兜底
                decode_candidates = [actual_encoding]
                final_encoding, content = await asyncio.to_thread(
                    self._try_decode, raw_bytes, decode_candidates
                )
                if final_encoding != actual_encoding:
                    logger = logging.getLogger(__name__)
                    logger.info(
                        "编码回退: %s → %s (文件 %s)",
                        actual_encoding, final_encoding, file_path,
                    )
                self._file_result = {
                    _CONTENT_KEY: content,
                    _LINE_NUMBERS_KEY: None,
                    _ERROR_KEY: None,
                    _SUCCESS_KEY: True,
                }
        except UnicodeDecodeError:
            _logger = logging.getLogger(__name__)
            _logger.error("意外到达死代码路径 (UnicodeDecodeError) - file=%s", file_path)
            # 作为防御性兜底，用 replace 模式解码
            try:
                content = raw_bytes.decode(actual_encoding, errors='replace')
                if self.start_line is not None or self.end_line is not None:
                    start = self.start_line if self.start_line is not None else 1
                    end = self.end_line if self.end_line is not None else None
                    lines = content.splitlines(keepends=True)
                    line_count = len(lines)
                    if end is None or end > line_count:
                        end = line_count
                    if start > line_count:
                        selected_lines = []
                    else:
                        selected_lines = lines[start-1:end]
                    content = ''.join(selected_lines)
                    original_line_numbers = (start, end)
                else:
                    original_line_numbers = None
                self._file_result = {
                    _CONTENT_KEY: content,
                    _LINE_NUMBERS_KEY: original_line_numbers,
                    _ERROR_KEY: None,
                    _SUCCESS_KEY: True,
                }
            except Exception:
                self._file_result = {
                    _CONTENT_KEY: None, _LINE_NUMBERS_KEY: None,
                    _ERROR_KEY: "(编码错误: 请尝试指定正确的encoding参数)",
                    _SUCCESS_KEY: False
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
        if content is not None:
            cleaned = content.replace('\r', '')
            if cleaned:
                return f"文件: {self.path}\n{cleaned}"
        return f"(文件为空: {self.path})"

    # ── 公共 UI 辅助方法（消除 display/web_display 重复）──

    async def _build_file_info_line(self, file_path: str, result: dict) -> str:
        """构建文件信息行（大小、修改时间、范围等），返回 ANSI 格式化字符串。"""
        exists = await aiofiles.os.path.exists(file_path)
        if not exists:
            return f"\n{CYAN}{file_path}{RESET}"

        stat = await aiofiles.os.stat(file_path)
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

    @staticmethod
    def _get_chat_ui():
        """获取活跃 ChatUI 实例，不可用时返回 None。"""
        try:
            from ..chat_ui import get_active_chat_ui  # noqa: PLC0415
            return get_active_chat_ui()
        except Exception:
            return None

    @staticmethod
    def _render_syntax_to_chatui(syntax: Syntax, chat_ui) -> None:
        """将 Syntax 渲染为 ANSI 字符串，通过 ChatUI write_line 逐行上屏。

        工具线程中调用，chat_ui.write_line() 是线程安全的（入队 → render 线程渲染）。
        使用 StringIO Console 捕获 Rich 的 ANSI 输出，不直接写终端。
        """
        import io
        from rich.console import Console as RichConsole

        buf = io.StringIO()
        ansi_console = RichConsole(file=buf, force_terminal=True)
        ansi_console.print(syntax)
        output = buf.getvalue()
        if output:
            for line in output.rstrip("\n").split("\n"):
                chat_ui.write_line(line)

    def _render_syntax_to_output(
        self, file_path: str, result: dict, lock_name: str,
        chat_ui=None,
    ) -> None:
        """渲染语法高亮到终端（Rich Syntax），供 display/web_display 复用。

        ChatUI 激活时 → 渲染为 ANSI 字符串，路由到 ChatUI render 线程串行输出
        （尊重 DECSTBM 分屏布局，不破坏底部栏显示）。
        ChatUI 不可用时 → console.print(syntax) 直写终端（持 output_lock）。

        Args:
            chat_ui: 调用方传入已获取的 ChatUI 实例，避免重复查询。
                     为 None 时内部自行查询。
        """
        syntax = self._build_syntax(result, file_path)
        if syntax is None:
            return

        # ChatUI 激活 → 路由到 ChatUI render 线程串行输出
        if chat_ui is None:
            chat_ui = self._get_chat_ui()
        if chat_ui is not None:
            self._render_syntax_to_chatui(syntax, chat_ui)
            return

        # ChatUI 不可用 → console.print() 直写终端（持 output_lock）
        with _try_acquire_output_lock(name=lock_name):
            console.print(syntax)

    async def display(self):
        """异步显示文件内容并返回给大模型"""
        start_time = _time.time()
        output = await self.execute()
        elapsed = _time.time() - start_time

        if not self._file_result[_SUCCESS_KEY]:
            msg = f"  {RED}x {self._file_result[_ERROR_KEY]}{RESET}"
            chat_ui = self._get_chat_ui()
            if chat_ui is not None:
                chat_ui.write_line(msg)
            else:
                locked_print(msg)
            return output

        file_path = self.path
        result = self._file_result

        info_line = await self._build_file_info_line(file_path, result)
        chat_ui = self._get_chat_ui()
        if chat_ui is not None:
            chat_ui.write_line(info_line)
        else:
            locked_print(info_line)

        self._render_syntax_to_output(file_path, result, "read_file.display.syntax", chat_ui=chat_ui)

        return output

    async def web_display(self) -> str:
        """Web 模式：返回带文件路径的纯文本内容（无 ANSI 控制码），
        同时将文件路径和内容（含语法高亮）打印到终端。"""
        start_time = _time.time()
        output = await self.execute()
        elapsed = _time.time() - start_time

        if not self._file_result[_SUCCESS_KEY]:
            msg = f"  {RED}x {self._file_result[_ERROR_KEY]}{RESET}\n"
            chat_ui = self._get_chat_ui()
            if chat_ui is not None:
                chat_ui.write_line(msg.rstrip("\n"))
                return output
            with _try_acquire_output_lock(name="read_file.web_display.error"):
                sys.__stdout__.write(msg)
                sys.__stdout__.flush()
            return output

        file_path = self.path
        result = self._file_result

        info_line = await self._build_file_info_line(file_path, result)
        chat_ui = self._get_chat_ui()
        info_written = False
        if chat_ui is not None:
            chat_ui.write_line(info_line)
            info_written = True
        if not info_written:
            with _try_acquire_output_lock(name="read_file.web_display.file_info"):
                sys.__stdout__.write(info_line + "\n")
                sys.__stdout__.flush()

        self._render_syntax_to_output(file_path, result, "read_file.web_display.syntax", chat_ui=chat_ui)

        # 为前端构建带行号信息的返回文本
        if result[_CONTENT_KEY] is not None:
            cleaned = result[_CONTENT_KEY].replace('\r', '')
            if cleaned:
                # 提取行号范围信息，嵌入到文件路径行中供前端解析
                if result.get(_LINE_NUMBERS_KEY) is not None:
                    start, end = result[_LINE_NUMBERS_KEY]
                    if end is not None:
                        range_str = f" (L{start}-{end})"
                    else:
                        range_str = f" (L{start}+)"
                else:
                    # 读取整个文件时，计算内容总行数
                    line_count = cleaned.count('\n') + 1
                    range_str = f" (L1-{line_count})"
                output = f"文件: {self.path}{range_str}\n{cleaned}"

        return output
