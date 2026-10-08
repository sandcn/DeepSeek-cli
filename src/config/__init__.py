"""配置包 — 拆分自 config.py，保持完全向后兼容"""

import os
import threading
from typing import Any

from .defaults import CONFIG_DIR, LOG_FILE, RC_FILE, INPUT_HISTORY_FILE, PROVIDERS, DEFAULTS, CONFIG_KEYS

from .loader import (
    _ensure_config_dir, _load_rc, get_rc, update_config,
    get_base_url, get_audit_logger,
)

# ============================================================
# 模块级延迟属性（PEP 562 __getattr__）
# ------------------------------------------------------------
# 所有原本在 import 时触发文件 IO 或依赖 RC 的模块级变量，
# 都通过 __getattr__ 延迟初始化，首次访问时才加载。
# 保持 from ..config import XXX 接口不变。
#
# _lazy_map 是模块级常量，避免每次 __getattr__ 调用时重建闭包。
#
# 性能优化：RC 相关键使用按需单键惰性获取 + 单键缓存（_value_cache），
# 避免首次访问任意可缓存键时批量加载全部配置值。
# ============================================================



# ---- 动态键：每次访问都重新计算（模型档案 / 运行时状态） ----
def _configured_models() -> list:
    """配置的模型列表（模型档案 ``model_profiles`` 的模型名，去重保序）。

    RC 顶层 ``models`` 字段已移除——模型列表**唯一来源**是模型档案；
    任何异常回退空列表（导入期/异常配置下调用方按空处理）。
    """
    try:
        from .model_profiles import configured_models

        return configured_models()
    except Exception:
        return []


def _current_llm_field(field: str) -> str:
    """当前生效档案的 LLM 连接字段（api_key / base_url / model / provider）。

    **LLM 访问的唯一入口**：环境变量 CHAT_API_KEY/CHAT_MODEL/CHAT_BASE_URL
    与 RC 旧键 ``api_key``/``base_url``/``model``/``provider`` 均已移除，
    取值只来自模型档案（``model_profiles`` + ``active_model_profile``）；
    未配置档案时返回空串（调用方给出「请先 /models 新增」提示）。
    """
    try:
        from . import model_profiles as mp

        return str(getattr(mp, field)() or "")
    except Exception:
        return ""


def _token_prices() -> dict:
    """token 价格表：RC 配置优先，缺省回退**当前生效档案 provider** 的内置价表。

    LLM 访问参数唯一来源 = 模型档案，因此缺省价表按当前档案的 provider 取
    （provider 来自档案，不再有 RC ``provider`` 旧键）。
    """
    try:
        rc = get_rc()
        prices = rc.get("token_prices") if isinstance(rc, dict) else None
        if isinstance(prices, dict) and prices:
            return dict(prices)
        from .defaults import PROVIDERS
        from .model_profiles import current_provider

        provider = current_provider(rc)
        return dict((PROVIDERS.get(provider, {}) or {}).get("token_prices", {}) or {})
    except Exception:
        return {}


_lazy_map = {
    # LLM 连接参数（唯一来源：当前模型档案）
    "API_KEY": lambda: _current_llm_field("current_api_key"),
    "BASE_URL": lambda: _current_llm_field("current_base_url"),
    "MODEL": lambda: _current_llm_field("current_model"),
    # 模型列表唯一来源：模型档案（``model_profiles``）的模型名
    "MODELS": lambda: _configured_models(),
    # token 价格表（RC 配置优先，缺省回退当前档案 provider 内置价表）
    "TOKEN_PRICES": lambda: _token_prices(),
    "audit_logger": lambda: get_audit_logger(),
    "STAGGER_MIN_DELAY": lambda: float(os.getenv("CHAT_STAGGER_MIN_DELAY", "0.1")),
    "STAGGER_MAX_DELAY": lambda: float(os.getenv("CHAT_STAGGER_MAX_DELAY", "0.5")),
}

# ---- 缓存容器 ----
_value_cache: dict[str, Any] = {}
_value_cache_lock = threading.Lock()


def _clear_value_cache():
    """清除 _value_cache 中所有缓存条目（线程安全）。

    在 update_config() 写入 RC 文件成功后调用，
    确保后续通过 __getattr__ 读取配置属性时获取最新值。
    """
    with _value_cache_lock:
        _value_cache.clear()


# ---- 声明式 RC 键映射：键名 → (嵌套路径, 默认值) ----
# 从 CONFIG_KEYS 元数据自动派生，新增配置键无需手动维护此映射。
# "config" 为特殊条目：路径为空元组表示直接返回 rc 字典本身。
# 带特殊逻辑的键（如 MODEL 检查环境变量）在 _resolve_rc_key 内处理。
_RC_KEY_MAP: dict[str, tuple[tuple[str, ...], Any]] = {
    "config": ((), None),
    **{
        name: (entry["rc_path"], entry["default"])
        for name, entry in CONFIG_KEYS.items()
    },
}


def _resolve_rc_key(name: str, rc: dict) -> Any:
    """从 rc 配置中按名称提取对应值（声明式映射驱动）。"""
    if name not in _RC_KEY_MAP:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    path, default = _RC_KEY_MAP[name]
    # 空路径 → 返回完整 rc 字典
    if not path:
        return rc
    # 通用嵌套字典遍历
    value = rc
    for part in path:
        value = value.get(part, {}) if isinstance(value, dict) else default
    if value == {}:
        value = default
    return value


def __getattr__(name):
    """延迟初始化模块级属性（PEP 562）。

    将属性分为两类处理：
    - 动态键（_lazy_map）：每次访问都重新计算（环境变量、运行时状态）。
    - RC 键（_resolve_rc_key）：惰性单键获取 + 单键缓存（_value_cache）。

    使用 threading.Lock 保护缓存读写，防止并发竞态。
    """
    # 动态键：每次直接计算
    if name in _lazy_map:
        return _lazy_map[name]()

    # RC 键：惰性单键获取 + 单键缓存
    rc = get_rc()
    with _value_cache_lock:
        if name not in _value_cache:
            _value_cache[name] = _resolve_rc_key(name, rc)
        return _value_cache[name]


# 导出 ConfigProxy 实例（作为 __getattr__ 的补充，提供 IDE 类型提示）
from .proxy import config as config
