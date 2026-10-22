"""配置包 — 配置加载和持久化"""

import json
import logging
import sys
import threading

from .defaults import (
    CONFIG_DIR, LOG_FILE, RC_FILE, DEFAULTS, CONFIG_KEYS, PROVIDERS, CONFIG_BOUNDS,
)
from .schema import _validate_rc


_RC = None
_RC_LOADED = False
# 并发写 RC 防护（review P3）：读改写序列加锁，避免多线程同时写文件写坏。
_update_lock = threading.Lock()


def _ensure_config_dir():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _safe_merge(defaults: dict, overrides: dict) -> dict:
    """递归合并配置：保留 defaults 缺省键，overrides 覆盖同名键。

    - 同名键且两侧均为 ``dict`` → **递归**合并子键（修复「浅合并」缺陷：
      RC 只配 ``skills.enabled`` 时，defaults 的其余 skills 子键不再整体丢失）；
    - 其余情况（``list`` / 标量 / 两侧类型不一致）→ 整体覆盖；
    - ``overrides`` 中 defaults 不存在的顶层键原样保留（如 "performance"），
      由 ``_validate_rc`` 做校验和回退。
    """
    result = dict(defaults)
    for key, value in overrides.items():
        base = result.get(key)
        if isinstance(base, dict) and isinstance(value, dict):
            result[key] = _safe_merge(base, value)
        else:
            result[key] = value
    return result


def _write_rc_file(rc: dict) -> bool:
    """把 RC 字典原子写入配置文件（写失败仅告警，返回是否成功）。"""
    _ensure_config_dir()
    try:
        RC_FILE.write_text(
            json.dumps(rc, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        sys.__stderr__.write(f"警告: 无法写入配置文件 {RC_FILE}: {e}\n")
        return False
    return True


def _load_rc():
    _ensure_config_dir()
    if RC_FILE.exists():
        try:
            raw_config = json.loads(RC_FILE.read_text(encoding="utf-8"))
            raw = _safe_merge(DEFAULTS, raw_config)
            rc = _validate_rc(raw)
            # 遗留字段清理落盘：RC 顶层 ``models`` 已废弃（模型列表唯一来源 =
            # 模型档案 ``model_profiles``）——``_validate_rc`` 已在内存中移除，
            # 这里把它从磁盘文件一并清掉。仅在文件确实含该键时写一次，避免
            # 每次启动都写文件。
            if isinstance(raw_config, dict) and "models" in raw_config:
                _write_rc_file(rc)
            return rc
        except json.JSONDecodeError as e:
            logging.warning("配置文件 %s 解析失败: %s，使用默认配置", RC_FILE, e)
        except OSError as e:
            logging.warning("无法读取配置文件 %s: %s，使用默认配置", RC_FILE, e)
    return _validate_rc(dict(DEFAULTS))


def get_rc():
    global _RC, _RC_LOADED
    if not _RC_LOADED:
        _RC = _load_rc()
        _RC_LOADED = True
    return _RC


# ── 数值边界校验（配置写入的唯一真源处执行） ──────────────
def _bounds_for(key: str):
    """取配置键的数值边界（大写写回键或 rc_path 末段均可；无边界返回 None）。"""
    bounds = CONFIG_BOUNDS.get(key)
    if bounds is not None:
        return bounds
    text = str(key or "")
    for name, meta in CONFIG_KEYS.items():
        path = meta.get("rc_path") or ()
        if path and str(path[-1]) == text:
            return CONFIG_BOUNDS.get(name)
    return None


def check_config_bounds(key: str, value) -> None:
    """数值配置边界校验：越界抛 ``ValueError``。

    ★ 修复（2026-10）：修复前配置层不校验数值区间，``config set temperature 99``
    / ``config set max_retries -5`` 均写入成功，直到调用 API 才报错（报错点
    远离根因）。本校验置于 ``update_config``（CLI / TUI / 配置导入等**所有**
    写入路径的唯一入口），边界声明见 ``config.defaults.CONFIG_BOUNDS``。

    仅对显式声明边界且值为 int/float 的键生效（bool 不计入——True/False 不是
    数值输入）；无边界键、非数值键不受影响。
    """
    bounds = _bounds_for(key)
    if bounds is None or isinstance(value, bool):
        return
    if not isinstance(value, (int, float)):
        return
    low, high = bounds
    if low is not None and value < low:
        raise ValueError(f"{key} 不能小于 {low}（当前: {value}）")
    if high is not None and value > high:
        raise ValueError(f"{key} 不能大于 {high}（当前: {value}）")


def update_config(key: str, value, *, persist: bool = True) -> bool:
    """更新配置键并按需持久化到 RC 文件。

    ★ P3（review 2026-08-22）：读改写序列加 ``_update_lock`` 防护——修复前
    无锁，多线程同时写 RC 文件存在整文件写坏风险（单条 write_text 原子但
    读-改-写序列并发交错仍可能丢写）。调用频率低，锁开销可忽略。

    ★ 修复（2026-10）：写入前做数值边界校验（``check_config_bounds``）——
    所有调用方（CLI / TUI 配置界面 / 配置导入 / 内部命令）共用同一道闸，
    越界值抛 ``ValueError`` 且不落盘。

    Args:
        persist: False 时只更新进程内 RC（供 /theme 导航即时预览，避免每按
            一次方向键就整文件落盘）；调用方在用户确认（Enter）时再用
            ``persist=True`` 落盘。

    Returns:
        True — 内存更新成功且（persist 时）RC 文件写入成功；
        False — 持久化写入失败（只读/磁盘满等）。
        ``check_config_bounds`` 等前置校验异常仍按原语义抛出。
    """
    with _update_lock:
        check_config_bounds(key, value)
        rc = get_rc()
        # 使用 CONFIG_KEYS 中的 rc_path 进行键名映射
        if key in CONFIG_KEYS:
            path = CONFIG_KEYS[key]["rc_path"]
            if path:
                # ★ P0（review）：显式校验替代 ``assert``——``assert`` 在
                #   ``python -O`` 下被剥离，配置写入路径的完整性校验会静默
                #   消失（配置键表损坏时会写出畸形 RC）。
                if not all(isinstance(p, str) and p for p in path):
                    raise ValueError(
                        f"CONFIG_KEYS['{key}']['rc_path'] 包含无效路径段: {path}"
                    )
                target = rc
                for part in path[:-1]:
                    nxt = target.setdefault(part, {})
                    # 防御：RC 嵌套键（如 performance.*）被写成非 dict（字符串等）
                    # 时 setdefault 返回非 dict → 再 .setdefault 抛 AttributeError。
                    # 此处重建为 dict，保证配置写入路径健壮。
                    if not isinstance(nxt, dict):
                        nxt = {}
                        target[part] = nxt
                    target = nxt
                target[path[-1]] = value
            else:
                rc[key] = value
        else:
            rc[key] = value
        written = _write_rc_file(rc) if persist else True
        if written:
            from . import _clear_value_cache
            _clear_value_cache()
            # multimodal 模型判定缓存联动失效：RC 配置 multimodal_models 变更后
            # 清除 is_multimodal_model 的结果缓存（延迟导入避免 config ↔ api 循环）
            try:
                from ..core.multimodal import clear_multimodal_cache
                clear_multimodal_cache()
            except Exception:
                logging.getLogger(__name__).debug("忽略异常（best-effort 路径）", exc_info=True)
            return True
        return False


def get_base_url(provider=None):
    """接口地址：给定 ``provider`` 时用其内置默认地址（PROVIDERS 元数据）。

    当前生效地址（**唯一来源 = 当前模型档案**）见 ``src.config.BASE_URL``——
    本函数不做任何模型档案解析，保持 ``config.loader ↔ config.model_profiles``
    零相互依赖（避免 config 包内循环导入）。
    """
    if provider is None:
        return ""
    cfg = PROVIDERS.get(provider, {})
    url = str(cfg.get("base_url", "") or "")
    if not url and provider != "custom":
        sys.__stderr__.write(
            f"警告: provider '{provider}' 的 API 格式与 OpenAI 不兼容，"
            f"当前客户端不支持，请使用支持的 provider。\n",
        )
    return url


def get_audit_logger():
    _ensure_config_dir()
    audit_logger = logging.getLogger("chat_audit")
    audit_logger.setLevel(logging.INFO)
    if not audit_logger.handlers:
        _handler = logging.FileHandler(str(LOG_FILE), encoding="utf-8")
        _handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
        audit_logger.addHandler(_handler)
    return audit_logger


def _get_performance_config() -> dict:
    """获取性能配置，从 RC 文件中读取 performance 节点。"""
    rc = get_rc()
    return rc.get("performance", {})
