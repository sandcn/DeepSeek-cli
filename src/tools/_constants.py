"""Tools 共享常量 — 集中管理排除目录、安全路径、异常类等跨工具常量

消除 search.py / find.py / file_ops.py 等文件之间的 DRY 违反。
"""

import fnmatch
import re

# ── 默认排除的非源码目录（用于 search / find 等搜索工具） ──
# 注意：修改此集合会同时影响 search, find 及所有引用它的工具

EXCLUDED_DIRS: set[str] = {
    "node_modules", "__pycache__", ".git", ".hg", ".svn",
    "venv", ".venv", "env", ".env",
    ".idea", ".vscode", ".vscode-server",
    "dist", "build", "target",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "*.egg-info",
    ".tox", ".nox", ".bundle",
    ".next", ".nuxt", ".output",
    "__snapshots__", "__fixtures__",
    ".chat",
    ".coverage", "htmlcov",
}

# ── 预编译的通配符目录排除模式（供 should_exclude_dir 使用） ──
_EXCLUDED_DIR_PATTERNS: tuple[str, ...] = tuple(
    d for d in EXCLUDED_DIRS if any(c in d for c in "*?[]")
)
_EXCLUDED_DIR_RES: tuple[re.Pattern, ...] = tuple(
    re.compile(fnmatch.translate(p)) for p in _EXCLUDED_DIR_PATTERNS
)


def should_exclude_dir(dirname: str) -> bool:
    """判断目录名是否应被排除

    分两阶段匹配：
    1. set 精确查找（不含通配符的模式，O(1) 性能）
    2. 预编译 regex 模式匹配（含通配符的模式，如 *.egg-info）
    """
    if dirname in EXCLUDED_DIRS:
        return True
    for compiled_re in _EXCLUDED_DIR_RES:
        if compiled_re.match(dirname):
            return True
    return False


# ── 默认排除的编译产物/二进制文件扩展名（用于 search 搜索工具） ──
# 新增排除模式时在此集合添加即可，自动传播到三路引擎（rg/grep/Python）
# 注意：与 EXCLUDED_DIRS 职责分离——此集合仅管理文件模式，EXCLUDED_DIRS 仅管理目录

EXCLUDED_FILE_PATTERNS: set[str] = {
    "*.o", "*.d", "*.exe", "*.dll", "*.so", "*.a",
}

# ── 用于 rg 的 --glob !<pattern> 排除模式 ──
RG_EXCLUDE_GLOBS: tuple[str, ...] = tuple(EXCLUDED_DIRS | EXCLUDED_FILE_PATTERNS)

# ── 用于 grep 的 --exclude-dir 排除（仅纯目录名） ──
GREP_EXCLUDE_DIRS: tuple[str, ...] = tuple(
    d for d in EXCLUDED_DIRS if "*" not in d
)

# ── 用于 grep 的 --exclude 排除（文件通配符模式） ──
GREP_EXCLUDE_FILES: tuple[str, ...] = tuple(
    {d for d in EXCLUDED_DIRS if "*" in d} | EXCLUDED_FILE_PATTERNS
)

# ── 路径安全常量（用于 file_ops / file_base / cp / mv / rm 等） ──

DANGEROUS_DEVICE_FILES: frozenset[str] = frozenset({
    "/dev/null", "/dev/zero", "/dev/random", "/dev/urandom",
    "/dev/stdin", "/dev/stdout", "/dev/stderr",
    "/dev/fd/0", "/dev/fd/1", "/dev/fd/2",
})

SYSTEM_CRITICAL_PATHS: frozenset[str] = frozenset({
    "/etc/passwd", "/etc/shadow", "/etc/sudoers",
    "/bin", "/sbin", "/usr/bin", "/usr/sbin",
})

DOS_DEVICE_NAMES: frozenset[str] = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"COM{n}" for n in range(1, 10)}
    | {f"LPT{n}" for n in range(1, 10)}
)

WIN_DEVICE_PREFIXES: tuple[str, ...] = ("\\\\.\\", "\\\\?\\")

# ── 文件操作常量 ──

DEFAULT_ENCODING = "utf-8"
DEFAULT_ERRORS = "strict"
MAX_FILE_SIZE_MB = 100

# ── 通吃编码 ──
# 通吃编码（catch-all encodings）：能解码任意字节序列（不抛异常、无 \ufffd），
# 编码检测时必须避开它们的「假阳性」——它们解码任何字节都「成功」，但结果完全错误。
# 注意：chardet 返回大写（如 'ISO-8859-9'），集合中统一用小写，
#       比较时做 .lower() 忽略大小写。
CATCHALL_ENCODINGS: frozenset[str] = frozenset({
    "latin-1", "iso-8859-1", "iso-8859-2", "iso-8859-3", "iso-8859-4",
    "iso-8859-5", "iso-8859-6", "iso-8859-7", "iso-8859-8", "iso-8859-9",
    "iso-8859-10", "iso-8859-11", "iso-8859-13", "iso-8859-14",
    "iso-8859-15", "iso-8859-16",
    "cp1250", "cp1251", "cp1252", "cp1253", "cp1254",
    "cp1255", "cp1256", "cp1257", "cp1258",
    "mac_roman", "cp437", "cp850",
    # 其他常见的单字节通吃编码
    "tis-620", "tis620",
    "koi8-r", "koi8-u",
    "mac_cyrillic",
    "cp866", "cp874",
})

# ── 编码检测常量 ──

# 最大用于编码检测的字节数
MAX_DETECT_BYTES = 64 * 1024

# 常见编码尝试顺序
COMMON_ENCODINGS: list[str] = ["utf-8", "gbk", "latin-1", "utf-8-sig"]

# 解码质量验证回退列表
FALLBACK_ENCODINGS: list[str] = ["gbk", "utf-8", "latin-1"]

# BOM 标记
BOM_MARKERS: dict[bytes, str] = {
    b'\xef\xbb\xbf': 'utf-8-sig',
    b'\x00\x00\xfe\xff': 'utf-32-be',
    b'\xff\xfe\x00\x00': 'utf-32-le',
    b'\xff\xfe': 'utf-16-le',
    b'\xfe\xff': 'utf-16-be',
}

ENCODING_ALIASES: dict[str, str] = {
    'gb2312': 'gbk',
    'gb18030': 'gbk',
    'ascii': 'utf-8',
}

# ── read_file 常量 ──

LARGE_FILE_THRESHOLD = 10 * 1024 * 1024  # 10MB

# ── web_search 常量 ──

WEB_USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Linux; Android 12; SM-G998B) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.6099.43 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.6167.143 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 12; SM-S908B) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.6099.43 Mobile Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# ── 工具显示名映射（UI显示用，对齐 Claude Code 完整名） ──

TOOL_DISPLAY_NAME: dict[str, str] = {
    "read_file": "Read",
    "write_file": "Write",
    "update_file": "Edit",
    "str_replace_editor": "Edit",
    "file_editor": "Edit",
    "dispatch_agent": "Task",
    "find": "Grep",
    "grep": "Grep",
    "glob": "Glob",
    "search": "Grep",
    "bash": "Bash",
    "execute_command": "Bash",
    "cp": "CP",
    "mv": "MV",
    "rm": "RM",
    "mkdir": "Mkdir",
    "user_select": "UserSelect",
    "web_search": "WebSearch",
    "web_fetch": "WebFetch",
    "ls": "LS",
}
