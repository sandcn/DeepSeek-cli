"""配置视图模型 — 配置项列表构建 / 值格式化 / 编辑输入解析（TUI/CLI 共用）。

纯逻辑模块（零 UI 依赖）：为 ``/config`` TUI 全屏界面（ConfigView）与
``chat.py config`` CLI 子命令提供统一的配置展示数据与编辑解析能力。

数据源单一：``CONFIG_KEYS`` 元数据（rc_path/type/default）+ ``DEFAULTS``
中的额外顶层键（provider/base_url/api_key/skills）。
"""

from __future__ import annotations

import json
from typing import Any

from .defaults import CONFIG_KEYS, DEFAULTS
from .loader import get_rc

#: 配置项说明映射（CONFIG_KEYS 键名 → 中文说明；额外键见 _EXTRA_KEYS）
CONFIG_ENTRY_DESCS: dict[str, str] = {
    # ── 核心配置 ──
    "MODEL": "当前模型（环境变量 CHAT_MODEL 可覆盖）",
    "MODELS": "可用模型列表",
    "REASONING_EFFORT": "推理等级（low/medium/high/max）",
    "TEMPERATURE": "大模型温度（0.0~2.0，越高越随机）",
    "THEME": "UI 配色主题（dark/light/high-contrast）",
    # ── 数值配置 ──
    "MAX_CONTEXT_CHARS": "上下文最大字符数",
    "MAX_OUTPUT_CHARS": "单次输出最大字符数",
    "MAX_RETRIES": "API 调用最大重试次数",
    "RETRY_BASE_SEC": "重试基础间隔（秒）",
    "MAX_SESSION_MESSAGES": "会话消息数上限（0=无限制）",
    "KEEP_RECENT_MESSAGES": "压缩时保留的最近消息数",
    "MAX_CONTEXT_TOKENS": "上下文最大 tokens",
    "MODEL_CONTEXT_TOKENS": "模型上下文窗口（tokens，上下文使用率分母）",
    "SUMMARY_TOKEN_BUDGET": "摘要 token 预算",
    "AUTO_FORCE_COMPRESS_THRESHOLD": "自动强制压缩阈值",
    # ── 布尔配置 ──
    "ENABLE_NOTIFICATIONS": "启用系统通知",
    "NOTIFY_ON_CHAT_COMPLETION": "聊天完成时通知",
    # ── 复合配置 ──
    "TOKEN_PRICES": "token 价格表（input/output/input_cache_hit，$/M）",
    "MULTIMODAL_MODELS": "多模态模型列表",
    # ── HTTP 性能配置（嵌套路径） ──
    "HTTP_CONNECT_TIMEOUT": "HTTP 连接超时（秒）",
    "HTTP_READ_TIMEOUT": "HTTP 读取超时（秒）",
    "HTTP_WRITE_TIMEOUT": "HTTP 写入超时（秒）",
    "HTTP_MAX_CONNECTIONS": "HTTP 连接池最大连接数",
    "HTTP_MAX_CONNECTIONS_PER_HOST": "HTTP 单主机最大连接数",
    "HTTP_KEEP_ALIVE_TIMEOUT": "HTTP 保持连接超时（秒）",
    "HTTP_ENABLE_POOL": "启用 HTTP 连接池",
    "HTTP_ENABLE_HTTP2": "启用 HTTP/2",
}

#: 额外顶层键（不在 CONFIG_KEYS 元数据中，但属于用户可配置项）：
#: (rc_key, type, desc, display_path)
_EXTRA_KEYS: tuple = (
    ("provider", str, "服务提供商（deepseek/custom/anthropic/glm/mimo）", "provider"),
    ("base_url", str, "API 基础地址（留空使用 provider 默认）", "base_url"),
    ("api_key", str, "API Key（留空使用环境变量 CHAT_API_KEY）", "api_key"),
    ("skills", dict, "技能子系统配置（enabled/auto_load 等）", "skills"),
)

#: 枚举选择型配置项的候选选项（写回键 → [(值, 说明), ...]）。
#: 键不在本表中、但类型为 bool 的配置项自动获得 true/false 候选；
#: MODEL 动态取当前可用模型列表；其余键走文本/JSON 输入界面。
CONFIG_ENTRY_OPTIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "PROVIDER": (
        ("deepseek", "DeepSeek 官方（v4-pro / v4-flash）"),
        ("custom", "自定义服务（需配置 base_url）"),
        ("anthropic", "Anthropic（Claude 系列）"),
        ("glm", "智谱 GLM（open.bigmodel.cn）"),
        ("mimo", "小米 MiMo（token-plan-cn）"),
    ),
    "THEME": (
        ("dark", "暗色主题"),
        ("light", "亮色主题"),
        ("high-contrast", "高对比主题"),
    ),
    "REASONING_EFFORT": (
        ("low", "低——最快响应，思考最少"),
        ("medium", "中——平衡速度与深度"),
        ("high", "高——更深入思考"),
        ("max", "最大——最充分思考"),
    ),
}

#: 显示文本截断长度（防超宽行破坏行级 diff 宽度不变量）
_DEFAULT_TRUNCATE = 48


def _rc_get(rc: dict, path: tuple, default: Any) -> Any:
    """按 rc_path 从 rc 字典取值；缺失/空 dict 用 default（对齐
    ``config._resolve_rc_key`` 语义——HTTP_* 等嵌套路径未配置时回退默认值）。"""
    value: Any = rc
    for part in path:
        if not isinstance(value, dict):
            return default
        value = value.get(part)
    if value is None:
        return default
    if isinstance(value, dict) and not value:
        return default
    return value


def _truncate(text: str, max_len: int) -> str:
    """按**显示宽度**截断文本（CJK 等宽字符按 2 列计；超长加省略号）。

    ★ P3（review 2026-08-20）：修复前按 ``len()`` 字符数截断——CJK 字符
    显示宽度 2，截断后实际显示可能超预算；现按宽字符感知截断（不依赖
    tui 层宽度工具，view_model 保持纯逻辑分层）。省略号 ``…`` 占 1 列，
    截断预算为 ``max_len - 1``（保证总显示宽度 ≤ max_len）。max_len<=0
    不截断防御。
    """
    if max_len <= 0:
        return text
    budget = max_len - 1
    width = 0
    for i, ch in enumerate(text):
        w = 2 if ord(ch) > 0x2E7F else 1
        if width + w > budget:
            return text[:i] + "\u2026"
        width += w
    return text


def _display_width(text: str) -> int:
    """显示宽度（CJK 等宽字符按 2 列计；与 _truncate 同一宽度口径）。"""
    return sum(2 if ord(ch) > 0x2E7F else 1 for ch in text)


def format_config_value(
    value: Any, typ: type,
    sensitive: bool = False, max_len: int = _DEFAULT_TRUNCATE,
) -> str:
    """配置值 → 显示文本（bool→true/false；list/dict→JSON 摘要；敏感脱敏）。"""
    if sensitive:
        s = str(value or "")
        if len(s) > 8:
            return f"{s[:3]}...{s[-4:]}"
        return "****" if s else "(空)"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        try:
            text = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(value)
        return _truncate(text, max_len)
    if value is None:
        return "(空)"
    return _truncate(str(value), max_len)


def parse_config_value(typ: type, text: str) -> tuple[Any, str]:
    """按类型解析编辑输入文本 → (值, 错误消息)。

    成功时错误消息为空串；失败时值为 None + 中文错误提示。
    bool 接受 true/false/1/0/yes/no/on/off；list/dict 接受 JSON。
    """
    raw = (text or "").strip()
    if typ is bool:
        t = raw.lower()
        if t in ("true", "1", "yes", "on"):
            return True, ""
        if t in ("false", "0", "no", "off"):
            return False, ""
        return None, "布尔值请输入 true/false"
    if typ is int:
        try:
            return int(raw), ""
        except (ValueError, TypeError):
            return None, "请输入整数"
    if typ is float:
        try:
            value = float(raw)
        except (ValueError, TypeError):
            return None, "请输入数字"
        # ★ P2（review 2026-08-20）：拒绝 NaN/Inf——修复前 ``float("nan")``
        #   无错误返回，ConfigView 输入可写入 nan/inf（与 _cmd_temperature
        #   的 NaN/Inf 防御不一致，同配置两条编辑路径行为不同）。
        import math as _math
        if not _math.isfinite(value):
            return None, "请输入有限数字"
        return value, ""
    if typ in (list, dict):
        raw_t = raw or ("[]" if typ is list else "{}")
        try:
            value = json.loads(raw_t)
        except (ValueError, TypeError):
            return None, "请输入合法 JSON"
        if not isinstance(value, typ):
            return None, f"JSON 类型应为 {typ.__name__}"
        return value, ""
    # str 及其余类型：直接接受文本
    return text, ""


def resolve_config_key(user_input: str) -> str | None:
    """用户输入的键名 → 写回键（``update_config`` 接受的大写键名或直接键名）。

    支持三种形态：
      - 大写键名：``MODEL`` / ``HTTP_CONNECT_TIMEOUT``；
      - rc_path 键：``model`` / ``connect_timeout`` / ``performance.http_client.connect_timeout``；
      - 额外顶层键：``provider`` / ``base_url`` / ``api_key`` / ``skills``。
    未匹配返回 None。
    """
    text = (user_input or "").strip()
    if not text:
        return None
    upper = text.upper()
    if upper in CONFIG_KEYS:
        return upper
    norm = text.lower()
    for name, meta in CONFIG_KEYS.items():
        path = ".".join(meta["rc_path"]).lower()
        if path == norm or path.endswith("." + norm):
            return name
    for key, _typ, _desc, _path in _EXTRA_KEYS:
        if key == norm or _path == norm:
            return key
    return None


def get_entry_display_path(rc_key: str) -> str:
    """写回键 → 显示路径（CONFIG_KEYS 用 rc_path 连接；额外键用自身名）。"""
    if rc_key in CONFIG_KEYS:
        path = CONFIG_KEYS[rc_key]["rc_path"]
        return ".".join(path) if path else rc_key
    return rc_key


def _entry_options(rc: dict, name: str, meta: dict) -> list[tuple[str, str]] | None:
    """配置项编辑候选选项（None=无选择界面，走文本/JSON 输入）。

    优先级：
      1. ``CONFIG_ENTRY_OPTIONS`` 静态枚举（provider/theme/reasoning_effort）；
      2. bool 类型自动生成 true/false；
      3. MODEL 动态取当前可用模型列表（MODELS 配置值 + PROVIDERS 聚合回退）；
      4. 其余返回 None（文本/JSON 输入界面）。
    """
    static = CONFIG_ENTRY_OPTIONS.get(name) or CONFIG_ENTRY_OPTIONS.get(name.upper())
    if static:
        return list(static)
    typ = meta.get("type")
    if typ is bool:
        return [("true", "开启"), ("false", "关闭")]
    if name == "MODEL":
        models: list = []
        try:
            models_path = CONFIG_KEYS["MODELS"]["rc_path"]
            models = _rc_get(rc, models_path, [])
        except Exception:
            models = []
        if not models:
            # 聚合全部 PROVIDERS 的模型（去重）
            from .defaults import PROVIDERS as _PROVIDERS
            _seen: set[str] = set()
            for _p in _PROVIDERS.values():
                for _m in _p.get("models", []):
                    if _m not in _seen:
                        _seen.add(_m)
                        models.append(_m)
        return [(str(m), "") for m in models]
    return None


def build_config_entries(rc: dict | None = None) -> list[dict]:
    """构建配置项列表（ConfigView 界面 / CLI 显示共用）。

    每条目字段：
      key        写回键（``update_config`` 接受）
      path       显示路径（chatrc.json 中的实际路径）
      type       Python 类型
      value      当前值
      value_text 格式化显示文本
      default_text 默认值文本
      desc       中文说明
      sensitive  是否敏感（api_key 脱敏）
      options    编辑候选选项 ``[(值, 说明), ...]``（仅 edit_kind=="select"）
      edit_kind  编辑界面类型："select"=候选选择界面（枚举/布尔/模型）；
                 "json"=子 JSON 结构化编辑界面（list/dict 有子结构的配置项）；
                 "input"=文本/数值输入界面（其余）
    """
    rc = rc if rc is not None else get_rc()
    entries: list[dict] = []
    for name, meta in CONFIG_KEYS.items():
        path = meta["rc_path"]
        value = _rc_get(rc, path, meta["default"])
        options = _entry_options(rc, name, meta)
        entries.append({
            "key": name,
            "path": ".".join(path) if path else name,
            "type": meta["type"],
            "value": value,
            "value_text": format_config_value(value, meta["type"]),
            "default_text": format_config_value(meta["default"], meta["type"]),
            "desc": CONFIG_ENTRY_DESCS.get(name, ""),
            "sensitive": False,
            "options": options,
            "edit_kind": _edit_kind_of(options, meta["type"]),
        })
    for key, typ, desc, path in _EXTRA_KEYS:
        value = rc.get(key, DEFAULTS.get(key))
        meta = {"type": typ, "default": DEFAULTS.get(key)}
        options = _entry_options(rc, key, meta)
        entries.append({
            "key": key,
            "path": path,
            "type": typ,
            "value": value,
            "value_text": format_config_value(
                value, typ, sensitive=(key == "api_key"),
            ),
            "default_text": format_config_value(DEFAULTS.get(key), typ),
            "desc": desc,
            "sensitive": key == "api_key",
            "options": options,
            "edit_kind": _edit_kind_of(options, typ),
        })
    return entries


def _edit_kind_of(options, typ: type) -> str:
    """编辑界面类型判定：有候选 → select；list/dict 有子 JSON → json；其余 input。"""
    if options:
        return "select"
    if typ in (list, dict):
        return "json"
    return "input"


def format_config_text(entries: list[dict] | None = None, rc_file=None) -> str:
    """配置项列表 → 多行文本（CLI 显示 / TUI 无界面回退显示共用）。

    每行：``键名 = 值   说明``；敏感项值脱敏显示。
    """
    from .defaults import RC_FILE as _DEFAULT_RC
    entries = entries if entries is not None else build_config_entries()
    file_path = rc_file if rc_file is not None else _DEFAULT_RC
    lines: list[str] = [f"配置中心（{file_path}）"]
    if not entries:
        lines.append("  (无配置项)")
        return "\n".join(lines)
    key_w = max(len(e["path"]) for e in entries)
    key_w = min(key_w, 40)
    for e in entries:
        marker = "*" if e.get("sensitive") else " "
        key = e["path"].ljust(key_w)
        lines.append(
            f"  {marker} {key} = {e['value_text']}"
            + (f"    {e['desc']}" if e.get("desc") else "")
        )
    lines.append("")
    lines.append("  提示: 编辑配置文件或使用 chat.py config set <键> <值>")
    return "\n".join(lines)
