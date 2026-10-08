"""配置包 — 配置加载和持久化"""

import json
import logging
import sys
import threading

from .defaults import CONFIG_DIR, LOG_FILE, RC_FILE, DEFAULTS, CONFIG_KEYS
from .schema import _validate_rc


_RC = None
_RC_LOADED = False
# 并发写 RC 防护（review P3）：读改写序列加锁，避免多线程同时写文件写坏。
_update_lock = threading.Lock()


def _ensure_config_dir():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _safe_merge(defaults: dict, overrides: dict) -> dict:
    """安全合并，保留 defaults 中已有的键并合并 overrides 值。

    同时保留 overrides 中 defaults 不存在的顶层键（如 "performance"），
    信任 _validate_rc 做校验和回退。

    注（review P3）：浅合并——RC 只配置复合键部分子键（如 skills 只写
    {"enabled": false}）时会整体替换 defaults 的 skills。读取方有 .get
    默认值兜底不崩溃，但用户配置会部分丢失；深合并复合键需按类型递归，
    当前复合键消费方均容忍，暂不实施。
    """
    result = dict(defaults)
    for key in result:
        if key in overrides:
            result[key] = overrides[key]
    # 保留 overrides 中 defaults 不存在的顶层键（如 "performance"），
    # 由 _validate_rc 做校验和回退
    for key in overrides:
        if key not in result:
            result[key] = overrides[key]
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
        except (PermissionError, OSError) as e:
            logging.warning("无法读取配置文件 %s: %s，使用默认配置", RC_FILE, e)
    return _validate_rc(dict(DEFAULTS))


def get_rc():
    global _RC, _RC_LOADED
    if not _RC_LOADED:
        _RC = _load_rc()
        _RC_LOADED = True
    return _RC


def update_config(key: str, value) -> None:
    """更新配置键并持久化到 RC 文件。

    ★ P3（review 2026-08-22）：读改写序列加 ``_update_lock`` 防护——修复前
    无锁，多线程同时写 RC 文件存在整文件写坏风险（单条 write_text 原子但
    读-改-写序列并发交错仍可能丢写）。调用频率低，锁开销可忽略。
    """
    with _update_lock:
        rc = get_rc()
        # 使用 CONFIG_KEYS 中的 rc_path 进行键名映射
        if key in CONFIG_KEYS:
            path = CONFIG_KEYS[key]["rc_path"]
            if path:
                assert all(isinstance(p, str) and p for p in path), (
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
        if _write_rc_file(rc):
            from . import _clear_value_cache
            _clear_value_cache()
            # multimodal 模型判定缓存联动失效：RC 配置 multimodal_models 变更后
            # 清除 is_multimodal_model 的结果缓存（延迟导入避免 config ↔ api 循环）
            try:
                from ..core.multimodal import clear_multimodal_cache
                clear_multimodal_cache()
            except Exception:
                pass


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
