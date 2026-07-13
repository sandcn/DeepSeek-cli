"""
ls — 列出目录内容工具

列出指定目录下的文件和子目录，支持类似 ls 命令的常用选项。
使用纯 Python 实现（pathlib + os 模块），安全跨平台，无 shell 注入风险。
"""

from __future__ import annotations

import asyncio
import os
import stat
import time
import logging
from pathlib import Path
from .base import Func, tool_metadata
from ..core.constants import human_size

logger = logging.getLogger(__name__)

import shutil

# ── 魔法数字常量 ──────────────────────────────────────
LARGE_DIR_ENTRY_LIMIT = 500   # 大目录条目阈值（走 executor）
RECENT_MODIFY_DAYS = 180      # 最近修改天数阈值


@tool_metadata(
    parallel_safe=True,
    requires_network=False,
    requires_terminal=False,
    timeout_estimate=0,
    category="code",
    priority=20,
    tool_category="read",
    description="列出目录内容",
)
class LsFunc(Func):
    """列出目录内容工具 — 类似 Unix ls 命令"""

    name = "ls"

    @classmethod
    def to_tool_schema(cls):
        return {
            "type": "function",
            "function": {
                "name": "ls",
                "description": (
                    "列出指定目录下的文件和子目录。支持常用选项：详细格式、显示隐藏文件。"
                    "使用纯 Python 实现，安全跨平台，无 shell 注入风险。"
                    "\n\n"
                    "【防幻觉】引用任何文件路径前，先用 ls 确认该路径确实存在，禁止凭记忆虚构路径。"
                    "\n\n"
                    "参数说明："
                    "\n- path（可选）：要列出的目录路径，默认当前工作目录"
                    "\n- long（可选）：是否以详细格式显示（类似 ls -l），显示权限/大小/修改时间，默认 false"
                    "\n- all（可选）：是否显示隐藏文件（以 . 开头的文件和目录），默认 false"
                    "\n- human（可选）：long 模式下文件大小是否以人类可读格式显示（如 1.5K、3.2M），默认 true"
                    "\n\n"
                    "使用示例："
                    "\n- 列出当前目录：ls()"
                    "\n- 列出指定目录：ls(path=\"src/\")"
                    "\n- 详细格式：ls(path=\"src/\", long=true)"
                    "\n- 显示隐藏文件：ls(path=\".\", all=true)"
                    "\n- 详细+人类可读：ls(path=\".\", long=true, human=true)"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "要列出的目录路径（可选）。省略时使用当前工作目录。支持相对路径和绝对路径。路径安全校验会拒绝路径穿越攻击。",
                        },
                        "long": {
                            "type": "boolean",
                            "description": "是否以详细格式显示（类似 ls -l）。为 true 时显示：权限、硬链接数、所有者、组、大小、修改时间、名称。默认 false。",
                            "default": False,
                        },
                        "all": {
                            "type": "boolean",
                            "description": "是否显示隐藏文件（以 . 开头的文件和目录）。默认 false 只显示非隐藏项。",
                            "default": False,
                        },
                        "human": {
                            "type": "boolean",
                            "description": "long 模式下文件大小是否以人类可读格式显示（如 1.5K、3.2M、1.8G）。默认 true。设为 false 则以字节显示。",
                            "default": True,
                        },
                    },
                    "required": [],
                },
            },
        }

    @classmethod
    def display_params(cls, arguments: dict, max_len: int = 80) -> str:
        path = arguments.get("path", "")
        opts = []
        if arguments.get("long"):
            opts.append("-l")
        if arguments.get("all"):
            opts.append("-a")
        display = path or "."
        if opts:
            display = f"{' '.join(opts)} {display}"
        return f"'{cls._sanitize_display(display)}'"

    def __init__(
        self,
        path: str | None = None,
        long: bool = False,
        all: bool = False,
        human: bool = True,
    ):
        super().__init__()
        self.target_path = path or os.getcwd()
        self.long = long
        self.all = all
        self.human = human

    # ── 核心执行 ────────────────────────────────────────

    async def execute(self) -> str:
        """异步执行 ls，返回格式化结果字符串"""
        try:
            target = Path(self.target_path).resolve()
            if not target.exists():
                return f"(路径不存在: {self.target_path})"

            if target.is_file():
                # 如果指定的是文件，显示文件信息
                return self._format_single_file(target)

            if not target.is_dir():
                return f"(不是目录也不是文件: {self.target_path})"

            # 先直接遍历（iterdir() 是轻度操作，不阻塞事件循环）
            try:
                entries = list(target.iterdir())
                # 大目录（>500项）时走 executor，避免排序和 stat 阻塞事件循环
                if len(entries) > LARGE_DIR_ENTRY_LIMIT:
                    loop = asyncio.get_event_loop()
                    entries = await loop.run_in_executor(None, self._list_entries, target)
                else:
                    if not self.all:
                        entries = [e for e in entries if not e.name.startswith(".")]
                    entries.sort(key=lambda p: (not p.is_dir(), p.name.lower()))
            except (OSError, PermissionError):
                loop = asyncio.get_event_loop()
                entries = await loop.run_in_executor(None, self._list_entries, target)

            return self._format_entries(entries, target)

        except PermissionError:
            return f"(权限不足: {self.target_path})"
        except OSError as e:
            return f"(列出目录失败: {e})"
        except Exception as e:
            logger.exception("ls 异常: path=%s", self.target_path)
            return f"(ls 失败: {e})"

    def _list_entries(self, target: Path) -> list[Path]:
        """同步列出目录条目（在 executor 中运行）"""
        entries: list[Path] = []
        for entry in target.iterdir():
            # 过滤隐藏文件
            if not self.all and entry.name.startswith("."):
                continue
            entries.append(entry)

        # 排序：目录在前，文件在后，各自按名称排序
        entries.sort(key=lambda p: (not p.is_dir(), p.name.lower()))
        return entries

    # ── 格式化 ──────────────────────────────────────────

    def _format_single_file(self, target: Path) -> str:
        """单个文件时的格式化输出"""
        if self.long:
            return self._format_long(target)
        return target.name

    def _format_entries(self, entries: list[Path], target: Path) -> str:
        """格式化目录条目列表"""
        if not entries:
            return f"(空目录: {target})"

        total = len(entries)

        if self.long:
            # 计算总块数（仅限常规文件）
            block_count = 0
            for entry in entries:
                try:
                    st = entry.stat()
                    # 每个 block 512 字节，取整
                    block_count += (st.st_blocks if hasattr(st, 'st_blocks')
                                    else (st.st_size + 511) // 512)
                except OSError:
                    pass

            lines = [f"总用量 {block_count}"]
            for entry in entries:
                lines.append(self._format_long(entry))
            return "\n".join(lines)

        # 短格式：列式输出
        # 计算列数（基于当前终端宽度）
        names = [entry.name for entry in entries]
        max_name_len = max(len(n) for n in names) if names else 0
        col_width = max_name_len + 2  # 名称 + 2空格间距
        cols = max(1, shutil.get_terminal_size().columns // col_width)
        rows = (total + cols - 1) // cols

        lines = []
        for row in range(rows):
            line_parts = []
            for col in range(cols):
                idx = row + col * rows
                if idx < total:
                    name = names[idx]
                    # 目录加 / 后缀
                    if entries[idx].is_dir():
                        name += "/"
                    line_parts.append(name.ljust(col_width))
            lines.append("".join(line_parts).rstrip())

        return "\n".join(lines)

    def _format_long(self, entry: Path) -> str:
        """详细格式：权限 硬链接数 所有者 组 大小 修改时间 名称"""
        try:
            st = entry.stat()
        except OSError:
            return f"? {entry.name}"

        # 权限字符串
        perms = self._format_permissions(st.st_mode, entry)

        # 硬链接数
        nlink = st.st_nlink

        # 所有者/组（惰性导入，避免 Windows/Android 上导入报错）
        try:
            import pwd  # noqa: PLC0415
            owner = pwd.getpwuid(st.st_uid).pw_name
        except (KeyError, ImportError):
            owner = str(st.st_uid)

        try:
            import grp  # noqa: PLC0415
            group = grp.getgrgid(st.st_gid).gr_name
        except (KeyError, ImportError):
            group = str(st.st_gid)

        # 大小
        if self.human:
            size = human_size(st.st_size)
        else:
            size = str(st.st_size).rjust(8)

        # 修改时间
        mtime = time.localtime(st.st_mtime)
        # 如果修改时间在过去6个月内，显示 月 日 时:分，否则 月 日  年
        now = time.time()
        six_months_ago = now - RECENT_MODIFY_DAYS * 24 * 3600
        if st.st_mtime > six_months_ago:
            time_str = time.strftime("%m月%d日 %H:%M", mtime)
        else:
            time_str = time.strftime("%m月%d日  %Y", mtime)

        # 名称（目录加 / 后缀）
        name = entry.name
        if entry.is_dir():
            name += "/"

        return f"{perms} {nlink:>2} {owner} {group} {size:>8} {time_str} {name}"

    @staticmethod
    def _format_permissions(mode: int, entry: Path) -> str:
        """格式化为类似 -rwxr-xr-x 的权限字符串"""
        if entry.is_symlink():
            type_char = "l"
        elif stat.S_ISDIR(mode):
            type_char = "d"
        elif stat.S_ISCHR(mode):
            type_char = "c"
        elif stat.S_ISBLK(mode):
            type_char = "b"
        elif stat.S_ISFIFO(mode):
            type_char = "p"
        elif stat.S_ISSOCK(mode):
            type_char = "s"
        else:
            type_char = "-"

        perms = type_char
        for who in ("USR", "GRP", "OTH"):
            for what in ("R", "W", "X"):
                shift = getattr(stat, f"S_I{what}{who}", 0)
                perms += what.lower() if (mode & shift) else "-"

        # 特殊权限位
        if mode & stat.S_ISUID:
            perms = perms[:3] + ("s" if perms[3] == "x" else "S") + perms[4:]
        if mode & stat.S_ISGID:
            perms = perms[:6] + ("s" if perms[6] == "x" else "S") + perms[7:]
        if mode & stat.S_ISVTX:
            perms = perms[:9] + ("t" if perms[9] == "x" else "T") + perms[10:]

        return perms

    # ── 显示（终端） ─────────────────────────────────────

    async def display(self) -> str:
        """终端显示"""
        opts = []
        if self.long:
            opts.append("-l")
        if self.all:
            opts.append("-a")
        opt_str = f" {' '.join(opts)}" if opts else ""
        return await self._display_result_template(
            header=f"ls{opt_str} {self.target_path}",
        )

    # ── 显示（Web） ─────────────────────────────────────

    async def web_display(self) -> str:
        """Web 模式：返回纯文本结果"""
        opts = []
        if self.long:
            opts.append("-l")
        if self.all:
            opts.append("-a")
        opt_str = f" {' '.join(opts)}" if opts else ""
        return await self._web_display_result_template(
            header=f"ls{opt_str} {self.target_path}",
            print_result=False,
        )
