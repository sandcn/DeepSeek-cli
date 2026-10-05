"""Tools 共享常量 — 集中管理排除目录、安全路径、异常类等跨工具常量

消除各工具文件之间的 DRY 违反。

「一切皆插件」：内置常量在本模块以字面量声明，并登记到 ``const_registry``
（``src.plugins.tool_const_entries`` 的独立条目）；消费方经本模块的访问器
（``catchall_encodings`` / ``const`` 等）实时查询，可按
Profile/Patch/Overlay 覆盖或禁用。模块级同名常量保留为向后兼容快照。
"""

from .const_registry import const, declare_constants  # noqa: F401  （const 供本模块访问器使用）

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

# ── 内置声明登记（清单条目按名称覆盖/禁用） ──────────────

declare_constants({
    "DANGEROUS_DEVICE_FILES": DANGEROUS_DEVICE_FILES,
    "SYSTEM_CRITICAL_PATHS": SYSTEM_CRITICAL_PATHS,
    "DOS_DEVICE_NAMES": DOS_DEVICE_NAMES,
    "WIN_DEVICE_PREFIXES": WIN_DEVICE_PREFIXES,
    "DEFAULT_ENCODING": DEFAULT_ENCODING,
    "DEFAULT_ERRORS": DEFAULT_ERRORS,
    "MAX_FILE_SIZE_MB": MAX_FILE_SIZE_MB,
    "CATCHALL_ENCODINGS": CATCHALL_ENCODINGS,
    "MAX_DETECT_BYTES": MAX_DETECT_BYTES,
    "COMMON_ENCODINGS": COMMON_ENCODINGS,
    "FALLBACK_ENCODINGS": FALLBACK_ENCODINGS,
    "BOM_MARKERS": BOM_MARKERS,
    "ENCODING_ALIASES": ENCODING_ALIASES,
    "LARGE_FILE_THRESHOLD": LARGE_FILE_THRESHOLD,
})


# ── 访问器（实时查询注册表当前生效值） ──────────────────


def dangerous_device_files() -> frozenset:
    return frozenset(const("DANGEROUS_DEVICE_FILES", ()))


def system_critical_paths() -> frozenset:
    return frozenset(const("SYSTEM_CRITICAL_PATHS", ()))


def dos_device_names() -> frozenset:
    return frozenset(const("DOS_DEVICE_NAMES", ()))


def win_device_prefixes() -> tuple:
    return tuple(const("WIN_DEVICE_PREFIXES", ()))


def default_encoding() -> str:
    return str(const("DEFAULT_ENCODING", "utf-8"))


def default_errors() -> str:
    return str(const("DEFAULT_ERRORS", "strict"))


def max_file_size_mb() -> int:
    return int(const("MAX_FILE_SIZE_MB", 100))


def large_file_threshold() -> int:
    return int(const("LARGE_FILE_THRESHOLD", 10 * 1024 * 1024))


def catchall_encodings() -> frozenset:
    return frozenset(const("CATCHALL_ENCODINGS", ()))


def common_encodings() -> list:
    return list(const("COMMON_ENCODINGS", ()))


def fallback_encodings() -> list:
    return list(const("FALLBACK_ENCODINGS", ()))


def bom_markers() -> dict:
    return dict(const("BOM_MARKERS", {}) or {})


def encoding_aliases() -> dict:
    return dict(const("ENCODING_ALIASES", {}) or {})


def max_detect_bytes() -> int:
    return int(const("MAX_DETECT_BYTES", 64 * 1024))


# ── 工具显示名映射（UI 显示用：一律取工具注册名的 PascalCase） ──
# 2026-09-18 用户需求：工具卡/子代理面板显示工具**真实注册名**（如
# ``ReadFile engine/rendering/FrameGraph.cpp``），不再用 Claude Code 风格
# 缩写（Read/Write/Edit/Task/Grep/RM 等）——UI 名称与模型调用的工具名一一对应。
# 数据表已上移为表现层数据注册表（``presentation_data`` → ``tool_display_name``）。

from ..core.tool_display import TOOL_DISPLAY_NAME  # noqa: E402,F401  （下沉核心层，re-export 兼容）
