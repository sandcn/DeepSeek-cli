"""model_profiles — 模型档案（模型选择器数据源与持久化，纯逻辑）。

「模型选择器」视图（ModelView，``model.fullscreen == "model"``）的**单一
数据真源**：模型列表构建（**配置的模型列表** = 用户档案 + RC 顶层
``models``——不列内置 provider 模型）、档案字段校验、写回持久化、应用到
当前会话。零 UI 依赖——TUI 组件 / 命令 / CLI 共用。

数据结构（RC 顶层 ``model_profiles``，``list[dict]``）：:

    {
        "provider": "deepseek",       # 提供商（deepseek/anthropic/glm/mimo/custom）
        "model": "deepseek-v4-pro",   # 必填（API 请求的 model 字段）
        "api_key": "sk-...",          # 可选（空 = 沿用全局 api_key / 环境变量）
        "base_url": "https://...",    # 接口地址（新增时按提供商自动填好，一般无需改）
        "name": "显示名",             # 可选（空 = 用模型名）
    }

新增流程（用户需求）：**先选提供商** → **填模型名** → **填 API 密钥**；
接口地址按提供商自动给定，用户无需输入（仍可在表单中修改）。
"""

from __future__ import annotations

__all__ = [
    "PROFILE_FIELDS",
    "FIELD_KEYS",
    "PROVIDER_LABELS",
    "field_by_key",
    "empty_profile",
    "normalize_profile",
    "display_name",
    "load_profiles",
    "save_profiles",
    "validate_profile",
    "infer_provider",
    "resolve_provider",
    "default_base_url",
    "effective_base_url",
    "provider_choices",
    "configured_models",
    "active_profile_index",
    "active_profile",
    "set_active_profile",
    "current_model",
    "current_provider",
    "current_base_url",
    "current_api_key",
    "build_model_entries",
    "apply_entry",
    "mask_key",
]

#: 档案字段元数据（表单渲染顺序 + 标签 + 敏感 + 必填 + 提示 + 编辑界面类型）。
#: 顺序即交互引导顺序：提供商 → 模型名 → API 密钥 → 接口地址 → 名称。
PROFILE_FIELDS: tuple = (
    {"key": "provider", "label": "提供商", "sensitive": False, "required": False,
     "kind": "select", "hint": "先选提供商（接口地址将自动填好）"},
    {"key": "model", "label": "模型名", "sensitive": False, "required": True,
     "kind": "input", "hint": "API 请求使用的模型名（如 deepseek-v4-pro）"},
    {"key": "api_key", "label": "API 密钥", "sensitive": True, "required": False,
     "kind": "input", "hint": "API Key（留空则沿用全局配置 / 环境变量）"},
    {"key": "base_url", "label": "接口地址", "sensitive": False, "required": False,
     "kind": "input", "hint": "已按提供商自动填好，一般无需修改"},
    {"key": "name", "label": "名称", "sensitive": False, "required": False,
     "kind": "input", "hint": "显示名（留空则用模型名）"},
)

#: 档案字段键（顺序同 ``PROFILE_FIELDS``）。
FIELD_KEYS: tuple = tuple(f["key"] for f in PROFILE_FIELDS)

#: 提供商显示说明（未列出的用其默认接口地址或占位说明）。
PROVIDER_LABELS: dict = {
    "deepseek": "DeepSeek 官方",
    "anthropic": "Anthropic Claude",
    "glm": "智谱 GLM",
    "mimo": "小米 MiMo",
    "custom": "自定义（需手填接口地址）",
}


def field_by_key(key: str) -> dict | None:
    """按字段键取元数据（未找到返回 None）。"""
    for f in PROFILE_FIELDS:
        if f["key"] == key:
            return f
    return None


def empty_profile() -> dict:
    """空白档案（全部字段为空串）。"""
    return {k: "" for k in FIELD_KEYS}


def normalize_profile(raw) -> dict:
    """任意输入 → 规范档案 dict（未知键丢弃、缺失补空串、值字符串化）。"""
    if not isinstance(raw, dict):
        return empty_profile()
    out: dict = {}
    for k in FIELD_KEYS:
        value = raw.get(k, "")
        out[k] = "" if value is None else str(value)
    return out


def display_name(profile) -> str:
    """档案显示名（``name`` 为空时回退模型名）。"""
    if not isinstance(profile, dict):
        return ""
    return (profile.get("name") or "").strip() or (profile.get("model") or "").strip()


def load_profiles(rc=None) -> list[dict]:
    """从配置读模型档案列表（非法条目跳过；rc 缺省时读当前 RC）。"""
    if rc is None:
        from .loader import get_rc
        rc = get_rc()
    raw = rc.get("model_profiles", []) if isinstance(rc, dict) else []
    if not isinstance(raw, list):
        return []
    return [normalize_profile(item) for item in raw if isinstance(item, dict)]


def save_profiles(profiles) -> None:
    """写回模型档案列表到配置（持久化 + 清配置缓存）。"""
    from .loader import update_config
    update_config("MODEL_PROFILES", [normalize_profile(p) for p in (profiles or [])])


def validate_profile(profile, profiles, index=None) -> str:
    """校验档案；返回错误消息（空串 = 通过）。

    仅 **模型名** 必填（显示名留空时用模型名）；重名判定按显示名（排除自身）。

    Args:
        profile: 待校验档案（规范 dict）。
        profiles: 现有档案列表（重名判定）。
        index: 编辑时的档案下标（重名判定排除自身；None = 新增）。
    """
    model = (profile.get("model") or "").strip()
    if not model:
        return "模型名不能为空"
    name = display_name(profile)
    for i, p in enumerate(profiles or []):
        if index is not None and i == index:
            continue
        if display_name(p) == name:
            return f"名称已存在: {name}"
    return ""


def infer_provider(model: str) -> str:
    """按模型名推断提供商（未命中返回空串）。"""
    model = (model or "").strip()
    if not model:
        return ""
    try:
        from .defaults import PROVIDERS
    except Exception:
        return ""
    for pname, pcfg in PROVIDERS.items():
        if model in (pcfg.get("models") or []):
            return pname
    if model.startswith("ollama"):
        return "custom"
    return ""


def resolve_provider(profile, current_provider: str = "") -> str:
    """档案最终提供商：显式配置 > 按模型名推断 > 当前提供商。"""
    explicit = (profile.get("provider") or "").strip()
    if explicit:
        return explicit
    inferred = infer_provider(profile.get("model") or "")
    return inferred or (current_provider or "")


def default_base_url(provider: str) -> str:
    """提供商默认接口地址（未配置返回空串）。"""
    try:
        from .defaults import PROVIDERS
        return str(PROVIDERS.get(provider, {}).get("base_url", "") or "")
    except Exception:
        return ""


def effective_base_url(profile, provider: str = "") -> str:
    """档案有效接口地址（空 = 提供商默认地址）。"""
    url = (profile.get("base_url") or "").strip()
    if url:
        return url
    provider = provider or resolve_provider(profile)
    return default_base_url(provider)


def provider_choices() -> list[tuple[str, str]]:
    """提供商候选 ``[(provider, 说明)]``（表单「提供商」字段的选择界面）。"""
    try:
        from .defaults import PROVIDERS
    except Exception:
        return []
    out: list[tuple[str, str]] = []
    for name, cfg in PROVIDERS.items():
        label = PROVIDER_LABELS.get(name)
        if not label:
            label = str(cfg.get("base_url", "") or "") or "（需手填接口地址）"
        out.append((str(name), label))
    return out


def configured_models(rc=None) -> list[str]:
    """配置的模型列表（**模型档案** ``model_profiles`` 的模型名，去重保序）。

    **模型列表的唯一来源**（RC 顶层 ``models`` 字段已移除）：
    ``/models`` 界面、``/model``（选择器 / 序号 / 名称）、``/model <Tab>``
    补全与 **Ctrl+N** 均只从此列表选择——内置 provider 模型
    （``defaults.PROVIDERS``）仅作元数据（provider 推断 / 默认地址）参与，
    不进入选择列表；列表为空时调用方给出「请先 /models 新增」提示。
    """
    if rc is None:
        from .loader import get_rc
        rc = get_rc()
    rc = rc if isinstance(rc, dict) else {}
    out: list[str] = []
    seen: set = set()
    for prof in load_profiles(rc):
        m = (prof.get("model") or "").strip()
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


# ═══════════════════════════════════════════════════════════
# 当前生效档案（LLM 访问参数的唯一来源）
# ═══════════════════════════════════════════════════════════

def active_profile_index(rc=None) -> int:
    """当前生效档案下标。

    无档案 → ``-1``；``active_model_profile`` 缺失/非法/越界时回退**首个
    档案**（保证有档案即可用，避免历史配置缺键导致不可用）。
    """
    if rc is None:
        from .loader import get_rc
        rc = get_rc()
    rc = rc if isinstance(rc, dict) else {}
    profiles = load_profiles(rc)
    if not profiles:
        return -1
    try:
        idx = int(rc.get("active_model_profile", -1))
    except (TypeError, ValueError):
        idx = -1
    return idx if 0 <= idx < len(profiles) else 0


def active_profile(rc=None) -> dict | None:
    """当前生效档案（无档案 → ``None``）。"""
    if rc is None:
        from .loader import get_rc
        rc = get_rc()
    rc = rc if isinstance(rc, dict) else {}
    idx = active_profile_index(rc)
    profiles = load_profiles(rc)
    if idx < 0 or idx >= len(profiles):
        return None
    return profiles[idx]


def set_active_profile(index) -> bool:
    """写入当前生效档案下标（持久化到 RC；非法下标返回 False）。"""
    try:
        from .loader import update_config

        update_config("ACTIVE_MODEL_PROFILE", int(index))
        return True
    except Exception:
        return False


def current_model(rc=None) -> str:
    """当前生效的模型名（= 当前档案 ``model``；无档案 → 空串）。"""
    prof = active_profile(rc)
    return str((prof or {}).get("model") or "").strip()


def current_provider(rc=None) -> str:
    """当前生效的提供商（档案 ``provider`` 优先，其次按模型名推断）。"""
    prof = active_profile(rc)
    if not prof:
        return ""
    return resolve_provider(prof, "")


def current_base_url(rc=None) -> str:
    """当前生效的接口地址（档案 ``base_url`` 为空时用提供商默认地址）。"""
    prof = active_profile(rc)
    if not prof:
        return ""
    return effective_base_url(prof, current_provider(rc))


def current_api_key(rc=None) -> str:
    """当前生效的 API 密钥（当前档案 ``api_key``；无档案 → 空串）。"""
    prof = active_profile(rc)
    return str((prof or {}).get("api_key") or "").strip()


def build_model_entries(rc=None) -> list[dict]:
    """构建模型列表（**仅模型档案** —— 模型列表与 LLM 访问的唯一来源）。

    与 :func:`configured_models` 同源：RC 顶层 ``models`` 字段已移除，
    内置 provider 模型（``defaults.PROVIDERS``）只作元数据用于 provider
    推断 / 默认接口地址，**不进入列表**。

    每条字段：
        kind           "profile"（模型档案）
        index          档案下标
        name           显示名（``name`` 为空回退模型名）
        model          实际模型名
        base_url       原始接口地址（可能空 = 用提供商默认）
        effective_base_url  有效接口地址（显示用）
        api_key        密钥（渲染层脱敏）
        provider       提供商
        current        是否当前生效（= :func:`active_profile_index` 指向）
        editable       恒 True
        hint           提供商显示说明
    """
    if rc is None:
        from .loader import get_rc
        rc = get_rc()
    rc = rc if isinstance(rc, dict) else {}
    active_idx = active_profile_index(rc)

    entries: list = []
    for i, prof in enumerate(load_profiles(rc)):
        model = prof.get("model", "")
        provider = resolve_provider(prof, "")
        entries.append({
            "kind": "profile",
            "index": i,
            "name": display_name(prof),
            "model": model,
            "base_url": (prof.get("base_url") or "").strip(),
            "effective_base_url": effective_base_url(prof, provider),
            "api_key": prof.get("api_key", ""),
            "provider": provider,
            "current": i == active_idx,
            "editable": True,
            "hint": PROVIDER_LABELS.get(provider, provider),
        })
    return entries


def apply_entry(entry) -> tuple[bool, str]:
    """把模型条目设为**当前生效档案**（写 ``active_model_profile``）。

    LLM 访问参数（api_key / base_url / model / provider）全部来自模型档案，
    因此这里只切换「当前档案」指针——不再写入任何 RC 旧键。

    Args:
        entry: 模型条目（``build_model_entries`` 产出；按 ``index`` 定位，
            缺 ``index`` 时按 ``model`` 匹配首个同模型档案）。

    Returns:
        ``(成功, 消息)``。
    """
    if not isinstance(entry, dict):
        return False, "无效模型条目"
    model = str(entry.get("model") or "").strip()
    if not model:
        return False, "模型名为空"
    profiles = load_profiles()
    index = entry.get("index")
    if index is None:
        index = -1
        for i, p in enumerate(profiles):
            if (p.get("model") or "").strip() == model:
                index = i
                break
    try:
        index = int(index)
    except (TypeError, ValueError):
        index = -1
    if not (0 <= index < len(profiles)):
        return False, f"模型档案不存在: {model}"
    if not set_active_profile(index):
        return False, "写入配置失败"
    label = display_name(profiles[index]) or model
    return True, f"已切换到 {label}"


def mask_key(key: str) -> str:
    """API 密钥脱敏显示（保留首尾少量字符）。"""
    s = str(key or "")
    if not s:
        return ""
    if len(s) > 8:
        return f"{s[:3]}...{s[-4:]}"
    return "*" * len(s)
