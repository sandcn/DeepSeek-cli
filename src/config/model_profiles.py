"""model_profiles — 模型档案（模型选择器数据源与持久化，纯逻辑）。

「模型选择器」视图（ModelView，``model.fullscreen == "model"``）的**单一
数据真源**：模型列表构建（**配置的模型列表** = 用户档案 + RC 顶层
``models``——不列内置 provider 模型）、档案字段校验、写回持久化、应用到
当前会话。零 UI 依赖——TUI 组件 / 命令 / CLI 共用。

数据结构（RC 顶层 ``model_profiles``，``list[dict]``）：:

    {
        "provider": "deepseek",       # 提供商（deepseek/anthropic/glm/mimo/custom）
        "model": "deepseek-v4-pro",   # 必填（API 请求的 model 字段）
        "api_key": "sk-...",          # 可选（空 = 不带认证密钥，仅适用于无需鉴权的服务）
        "base_url": "https://...",    # 接口地址（新增时按提供商自动填好，一般无需改）
        "name": "显示名",             # 可选（空 = 用模型名）
        "temperature": "0.7",         # 可选（空 = 沿用全局 temperature）
        "reasoning_effort": "high",   # 可选（空 = 沿用全局 reasoning_effort）
        "multimodal": "false",        # 是否支持多模态读图（"true"/"false"）
    }

**按模型生成参数**（2026-10-17 用户需求「/models 支持设置对应的温度跟思考
级别」）：每个档案可独立设置 ``temperature`` / ``reasoning_effort``，
:func:`effective_temperature` / :func:`effective_reasoning_effort` 供 API
适配器读取当前生效档案的值（未设置时回退全局默认）。全局 ``/temperature``
与 ``/reasoning`` 命令已删除——生成参数统一在 ``/models`` 界面按模型配置。

**多模态读图开关**（2026-10-19 用户需求）：每个档案可显式设置
``multimodal``（``"true"`` / ``"false"``，默认 ``"false"``）——它同时决定：

1. 是否为该模型加载 ``read_image`` 工具（false 时工具不出现在请求的
   tool schemas 中）；
2. ``read_image`` 执行时是否放行（false 时返回明确的「请在 /models 开启
   多模态读图」错误）。

模型能力不再由模型名模式推断（原 ``is_multimodal_model`` 判定已从
``read_image`` 移除）——用户显式配置是唯一判据。

新增流程（用户需求）：**先选提供商** → **填模型名** → **填 API 密钥**；
接口地址按提供商自动给定，用户无需输入（仍可在表单中修改）。

**模型名候选**（2026-10-13 用户需求「/models 界面可以选择对应提供商的模型
名」）：选定提供商后，模型名可直接从 :func:`provider_model_choices`（=
``defaults.PROVIDERS`` 的 ``models``，附「默认」标记与计费单价）中选择，
同时保留手动输入自定义模型名的能力。
"""

from __future__ import annotations

import math

__all__ = [
    "PROFILE_FIELDS",
    "FIELD_KEYS",
    "PROVIDER_LABELS",
    "REASONING_EFFORT_LEVELS",
    "TEMPERATURE_RANGE",
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
    "effective_temperature",
    "effective_reasoning_effort",
    "profile_supports_multimodal",
    "multimodal_choices",
    "model_profile_for_model",
    "model_supports_multimodal",
    "current_multimodal",
    "reasoning_effort_choices",
    "provider_choices",
    "provider_models",
    "provider_model_choices",
    "default_model_for",
    "configured_models",
    "active_profile_index",
    "active_profile",
    "set_active_profile",
    "current_model",
    "current_provider",
    "current_base_url",
    "current_api_key",
    "current_temperature",
    "current_reasoning_effort",
    "build_model_entries",
    "apply_entry",
    "mask_key",
]

from .defaults import REASONING_EFFORT_LEVELS, TEMPERATURE_RANGE
from ._values import normalize_multimodal as _normalize_multimodal

#: 档案字段元数据（表单渲染顺序 + 标签 + 敏感 + 必填 + 提示 + 编辑界面类型）。
#: 顺序即交互引导顺序：提供商 → 模型名 → API 密钥 → 接口地址 → 名称 →
#: 温度 → 思考级别。
PROFILE_FIELDS: tuple = (
    {"key": "provider", "label": "提供商", "sensitive": False, "required": False,
     "kind": "select", "hint": "先选提供商（接口地址将自动填好）"},
    {"key": "model", "label": "模型名", "sensitive": False, "required": True,
     "kind": "input", "hint": "API 请求使用的模型名（Enter 进入后可按 ↑/↓ 选择该提供商内置模型）"},
    {"key": "api_key", "label": "API 密钥", "sensitive": True, "required": False,
     "kind": "input", "hint": "API Key（空 = 不带认证密钥，仅适用于无需鉴权的服务）"},
    {"key": "base_url", "label": "接口地址", "sensitive": False, "required": False,
     "kind": "input", "hint": "已按提供商自动填好，一般无需修改"},
    {"key": "name", "label": "名称", "sensitive": False, "required": False,
     "kind": "input", "hint": "显示名（留空则用模型名）"},
    {"key": "temperature", "label": "温度", "sensitive": False, "required": False,
     "kind": "input", "hint": f"该模型温度 {TEMPERATURE_RANGE[0]:.1f}~{TEMPERATURE_RANGE[1]:.1f}（如 0.7）；留空沿用全局默认"},
    {"key": "reasoning_effort", "label": "思考级别", "sensitive": False, "required": False,
     "kind": "select", "hint": "该模型思考级别 low/medium/high/max；留空沿用全局默认"},
    {"key": "multimodal", "label": "多模态读图", "sensitive": False, "required": False,
     "kind": "select", "hint": "该模型是否支持图像输入：true 才加载 read_image 工具，false 不加载"},
)

#: 思考级别候选（空串 = 沿用全局默认）。
_REASONING_EFFORT_DESCS: dict = {
    "": "沿用全局默认",
    "low": "低——最快响应，思考最少",
    "medium": "中——平衡速度与深度",
    "high": "高——更深入思考",
    "max": "最大——最充分思考",
}

#: 多模态读图候选（true = 支持图像输入并加载 read_image；false = 不加载）。
_MULTIMODAL_DESCS: dict = {
    "true": "支持——加载 read_image 并返回图片",
    "false": "不支持——不加载 read_image 工具",
}

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
    """空白档案（多模态读图默认 false，其余字段为空串）。"""
    return {
        k: ("false" if k == "multimodal" else "")
        for k in FIELD_KEYS
    }


def normalize_profile(raw) -> dict:
    """任意输入 → 规范档案 dict（未知键丢弃、缺失补默认、值字符串化）。"""
    if not isinstance(raw, dict):
        return empty_profile()
    out: dict = {}
    for k in FIELD_KEYS:
        value = raw.get(k, "")
        if k == "multimodal":
            out[k] = _normalize_multimodal(value)
        else:
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
    可选生成参数（``temperature`` / ``reasoning_effort``）非空时校验值域，
    校验通过后**就地规范化**（温度 → 两位小数文本、思考级别 → 小写）——
    调用方保存的是同一 dict，因此落盘的始终是规范值。

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
    temperature = str(profile.get("temperature", "") or "").strip()
    if temperature:
        try:
            value = float(temperature)
        except (TypeError, ValueError):
            return f"温度必须是 {TEMPERATURE_RANGE[0]:g}~{TEMPERATURE_RANGE[1]:g} 之间的数字"
        if (
            not math.isfinite(value)
            or not (TEMPERATURE_RANGE[0] <= value <= TEMPERATURE_RANGE[1])
        ):
            return f"温度必须是 {TEMPERATURE_RANGE[0]:g}~{TEMPERATURE_RANGE[1]:g} 之间的数字"
        profile["temperature"] = f"{round(value, 2):g}"
    effort = str(profile.get("reasoning_effort", "") or "").strip().lower()
    if effort and effort not in REASONING_EFFORT_LEVELS:
        return f"思考级别必须是 {'/'.join(REASONING_EFFORT_LEVELS)} 或留空"
    profile["reasoning_effort"] = effort
    profile["multimodal"] = _normalize_multimodal(profile.get("multimodal", ""))
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


def _global_temperature(rc) -> float:
    """全局默认温度（RC 值；非法/缺失回退 0.2）。"""
    if rc is None:
        try:
            from .loader import get_rc
            rc = get_rc()
        except Exception:
            rc = {}
    raw = rc.get("temperature", 0.2) if isinstance(rc, dict) else 0.2
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.2
    if (
        not math.isfinite(value)
        or not (TEMPERATURE_RANGE[0] <= value <= TEMPERATURE_RANGE[1])
    ):
        return 0.2
    return value


def _global_reasoning_effort(rc) -> str:
    """全局默认思考级别（RC 值；非法/缺失回退 ``max``）。"""
    if rc is None:
        try:
            from .loader import get_rc
            rc = get_rc()
        except Exception:
            rc = {}
    raw = rc.get("reasoning_effort", "max") if isinstance(rc, dict) else "max"
    value = str(raw or "").strip().lower()
    if value not in REASONING_EFFORT_LEVELS:
        return "max"
    return value


def effective_temperature(profile=None, rc=None) -> float:
    """当前生效温度：档案值优先，空/非法 → 全局默认。

    API 请求按**当前生效档案**读取本函数（``/models`` 中每个模型可独立
    设置温度；未设置时沿用 RC 全局 ``temperature``）。
    """
    if profile is None:
        profile = active_profile(rc)
    raw = (profile or {}).get("temperature", "") if isinstance(profile, dict) else ""
    text = str(raw or "").strip()
    if text:
        try:
            value = float(text)
        except (TypeError, ValueError):
            value = None
        if (
            value is not None
            and math.isfinite(value)
            and TEMPERATURE_RANGE[0] <= value <= TEMPERATURE_RANGE[1]
        ):
            return value
    return _global_temperature(rc)


def effective_reasoning_effort(profile=None, rc=None) -> str:
    """当前生效思考级别：档案值优先，空/非法 → 全局默认。"""
    if profile is None:
        profile = active_profile(rc)
    raw = (
        (profile or {}).get("reasoning_effort", "")
        if isinstance(profile, dict) else ""
    )
    value = str(raw or "").strip().lower()
    if value in REASONING_EFFORT_LEVELS:
        return value
    return _global_reasoning_effort(rc)


def current_temperature(rc=None) -> float:
    """当前生效档案的有效温度（无档案 → 全局默认）。"""
    return effective_temperature(active_profile(rc), rc)


def current_reasoning_effort(rc=None) -> str:
    """当前生效档案的有效思考级别（无档案 → 全局默认）。"""
    return effective_reasoning_effort(active_profile(rc), rc)


# ── 多模态读图（模型档案显式开关，read_image 加载/放行的唯一判据） ──

def profile_supports_multimodal(profile) -> bool:
    """档案是否声明支持多模态读图（``multimodal`` 为 true）。"""
    if not isinstance(profile, dict):
        return False
    return _normalize_multimodal(profile.get("multimodal", "")) == "true"


def multimodal_choices() -> list[tuple[str, str]]:
    """多模态读图候选 ``[(值, 说明)]``（表单「多模态读图」选择界面）。"""
    return [(value, _MULTIMODAL_DESCS[value]) for value in ("false", "true")]


def model_profile_for_model(model: str, rc=None) -> dict | None:
    """按模型名定位档案（当前生效档案优先，其次首个同名档案）。

    模型名可能重复（不同接口地址 / 密钥）；LLM 访问以 ``active_model_profile``
    为准，因此当生效档案的 ``model`` 与给定模型名一致时优先返回它。
    找不到返回 ``None``。
    """
    target = str(model or "").strip()
    if not target:
        return None
    active = active_profile(rc)
    if active is not None and str(active.get("model") or "").strip() == target:
        return active
    for profile in load_profiles(rc):
        if str(profile.get("model") or "").strip() == target:
            return profile
    return None


def model_supports_multimodal(model: str | None = None, rc=None) -> bool:
    """模型是否支持多模态读图（读模型档案的 ``multimodal`` 开关）。

    这是 ``read_image`` 工具加载与执行放行的**唯一判据**（取代原按模型名
    模式推断的逻辑）：

    - ``model`` 为空 → 取当前生效档案；
    - 模型名匹配不到任何档案 → ``False``（未声明即不支持）；
    - 档案 ``multimodal`` 为 ``"true"`` → ``True``。
    """
    if not model:
        return profile_supports_multimodal(active_profile(rc))
    profile = model_profile_for_model(model, rc)
    return profile_supports_multimodal(profile)


def current_multimodal(rc=None) -> bool:
    """当前生效档案是否支持多模态读图（无档案 → False）。"""
    return profile_supports_multimodal(active_profile(rc))


def reasoning_effort_choices(global_default: str | None = None) -> list[tuple[str, str]]:
    """思考级别候选 ``[(值, 说明)]``（空值 = 沿用全局默认）。"""
    current = global_default
    if current is None:
        current = _global_reasoning_effort(None)
    out: list[tuple[str, str]] = []
    for value in ("",) + tuple(REASONING_EFFORT_LEVELS):
        desc = _REASONING_EFFORT_DESCS.get(value, "")
        if value == "" and desc:
            desc = f"{desc}（当前 {current}）"
        out.append((value, desc))
    return out


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


def provider_models(provider: str) -> list[str]:
    """提供商的内置模型名列表（``defaults.PROVIDERS[provider]["models"]``）。

    「模型名」字段（新增/编辑模型档案）的**候选来源**：用户选定提供商后，
    可直接从该列表中选择模型名（``/models`` 表单 ↑↓），也可手动输入其它
    自定义模型名（保证兼容自建 / 新上线模型）。

    未知提供商、``custom``（无内置模型）、配置异常 → 返回空列表（调用方
    回退纯手动输入，行为与此前一致）。
    """
    name = str(provider or "").strip()
    if not name:
        return []
    try:
        from .defaults import PROVIDERS
        models = (PROVIDERS.get(name) or {}).get("models") or []
    except Exception:
        return []
    out: list[str] = []
    seen: set = set()
    for m in models:
        text = str(m or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def default_model_for(provider: str) -> str:
    """提供商默认模型名（``defaults.PROVIDERS[provider]["default_model"]``）。

    未配置 / 未知提供商 / 异常 → 空串。
    """
    name = str(provider or "").strip()
    if not name:
        return ""
    try:
        from .defaults import PROVIDERS
        return str((PROVIDERS.get(name) or {}).get("default_model", "") or "").strip()
    except Exception:
        return ""


def provider_model_choices(provider: str) -> list[tuple[str, str]]:
    """提供商内置模型的候选 ``[(模型名, 说明)]``（模型名字段候选列表）。

    说明列：``默认``（= 该提供商默认模型）+ 计费单价（``$in/$out``，单位
    美元 / 百万 tokens，取自 ``defaults.PROVIDERS`` 的 ``token_prices``）；
    两者皆无时回退 ``内置模型``。无内置模型（``custom`` / 未知提供商）返回
    空列表。
    """
    models = provider_models(provider)
    if not models:
        return []
    default = default_model_for(provider)
    try:
        from .defaults import PROVIDERS
        prices = (PROVIDERS.get(str(provider or "").strip()) or {}).get("token_prices") or {}
    except Exception:
        prices = {}
    out: list[tuple[str, str]] = []
    for m in models:
        parts: list[str] = []
        if m == default:
            parts.append("默认")
        price = prices.get(m) if isinstance(prices, dict) else None
        if isinstance(price, dict):
            inp = price.get("input")
            outp = price.get("output")
            if isinstance(inp, (int, float)) and isinstance(outp, (int, float)):
                parts.append(f"${inp:g}/${outp:g}")
        out.append((m, " \u00b7 ".join(parts) if parts else "\u5185\u7f6e\u6a21\u578b"))
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
        temperature    档案温度（空 = 沿用全局默认）
        reasoning_effort  档案思考级别（空 = 沿用全局默认）
        multimodal     多模态读图开关（"true"/"false"）
        effective_temperature       有效温度（档案 > 全局默认）
        effective_reasoning_effort  有效思考级别（档案 > 全局默认）
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
            "temperature": (prof.get("temperature") or "").strip(),
            "reasoning_effort": (prof.get("reasoning_effort") or "").strip(),
            "multimodal": _normalize_multimodal(prof.get("multimodal", "")),
            "effective_temperature": effective_temperature(prof, rc),
            "effective_reasoning_effort": effective_reasoning_effort(prof, rc),
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
