"""共享工具函数模块（renderer 包内部使用）— 包内包集线器。

所有公共符号由 _utils/ 子包提供，此文件仅为向后兼容的集线器模块。
"""

from __future__ import annotations

# ── URL/Email 扫描 ──
from ._utils._url_email_scanner import (
    _scan_next_url,
    _scan_next_email,
    _scan_next_url_or_email,
)

# ── HTML 实体 ──
from ._utils._html_entities import (
    _HTML_ENTITIES,
    decode_html_entities,
)

# ── 自动链接化 ──
from ._utils._linkify import (
    auto_linkify,
    _auto_linkify_emails,
)

# ── 显示宽度与转义标记 ──
from ._utils._display import (
    _ZERO_WIDTH_CHARS,
    cjk_display_width,
    strip_escape_markers,
)

# ── 代码高亮 ──
from ._utils._highlight import (
    _code_style_cache,
    parse_highlight_lines,
    get_code_style,
)

# ── Code fence 检测 ──
from ._utils._fence import (
    _COMMON_LANGUAGES,
    _get_fence_info,
)

# ── 高精度延时 ──
from ._utils._delay import (
    _PLATFORM,
    _IS_LINUX,
    _precise_delay,
)

# ── CSS 颜色映射 ──
from ._utils._css_colors import (
    CSS_COLOR_MAP,
)
