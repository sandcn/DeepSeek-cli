"""配置包 — 配置校验逻辑"""

from .defaults import CONFIG_KEYS, DEFAULTS


def _derive_rc_fields(key_type):
    """从 CONFIG_KEYS 元数据中派生指定类型的扁平 RC 字段名列表。

    仅返回 rc_path 长度为 1（即 RC 文件顶层键）的条目，
    嵌套路径（如 HTTP 性能配置）由各自模块自行处理。
    """
    return sorted(
        entry["rc_path"][0]
        for entry in CONFIG_KEYS.values()
        if entry["type"] == key_type and len(entry["rc_path"]) == 1
    )


# 模块级派生常量（单次计算，避免 _validate_rc 每次调用重建列表）
_INT_FIELDS = _derive_rc_fields(int)
_FLOAT_FIELDS = _derive_rc_fields(float)
_BOOL_FIELDS = _derive_rc_fields(bool)

# reasoning_effort 允许值域（模块级常量，避免每次 _validate_rc 调用重建）
_REASONING_EFFORT_LEVELS = frozenset({"low", "medium", "high", "max"})


def _validate_rc(rc):
    """校验配置值类型，无效值回退到默认值

    注（review P3）：嵌套 HTTP 性能配置（``performance.*``，见 CONFIG_KEYS
    rc_path 含 2 段的条目）不做类型校验——其消费方（client_async）读取时
    有类型兜底；如需严格校验需按路径段递归遍历，当前收益低不实施。
    """
    int_fields = _INT_FIELDS
    for field in int_fields:
        if field in rc:
            if isinstance(rc[field], bool):
                rc[field] = DEFAULTS.get(field, 0)
            if not isinstance(rc[field], int):
                try:
                    rc[field] = int(rc[field])
                except (ValueError, TypeError):
                    rc[field] = DEFAULTS.get(field, 0)

    float_fields = _FLOAT_FIELDS
    for field in float_fields:
        if field in rc:
            # bool 是 int 子类：显式排除（True/False 不应作为 float 配置）
            if isinstance(rc[field], bool):
                rc[field] = DEFAULTS.get(field, 1.0)
            elif not isinstance(rc[field], (int, float)):
                try:
                    rc[field] = float(rc[field])
                except (ValueError, TypeError):
                    rc[field] = DEFAULTS.get(field, 1.0)

    bool_fields = _BOOL_FIELDS
    for field in bool_fields:
        if field in rc:
            if isinstance(rc[field], bool):
                continue
            elif isinstance(rc[field], str):
                rc[field] = rc[field].lower() in ("true", "1", "yes", "on")
            elif isinstance(rc[field], int):
                rc[field] = bool(rc[field])
            else:
                rc[field] = DEFAULTS.get(field, True)

    # ── 旧 LLM 访问配置清理（环境变量 CHAT_API_KEY/CHAT_MODEL/CHAT_BASE_URL
    #    /CHAT_LOW_MODEL 与 RC 旧键 api_key/base_url/model/provider/low_model
    #    均已移除）——LLM 访问参数唯一来源 = 模型档案（``model_profiles`` +
    #    ``active_model_profile``）。历史配置文件中的遗留键在此清理，避免
    #    出现两套来源造成的语义分叉。
    for legacy_key in ("models", "api_key", "base_url", "model", "provider", "low_model"):
        rc.pop(legacy_key, None)

    # reasoning_effort 值域校验：非 str 或不在允许集合时回退默认值
    # （_REASONING_EFFORT_LEVELS 为模块级常量，见上方定义）
    if "reasoning_effort" in rc:
        effort = rc["reasoning_effort"]
        if not isinstance(effort, str) or effort.lower() not in _REASONING_EFFORT_LEVELS:
            rc["reasoning_effort"] = DEFAULTS.get("reasoning_effort", "max")
        else:
            rc["reasoning_effort"] = effort.lower()

    # temperature 值域校验：非法类型或超出 [0.0, 2.0] 时回退默认值
    if "temperature" in rc:
        temp = rc["temperature"]
        if isinstance(temp, bool) or not isinstance(temp, (int, float)):
            rc["temperature"] = DEFAULTS.get("temperature", 0.2)
        else:
            temp = float(temp)
            if not (0.0 <= temp <= 2.0):
                rc["temperature"] = DEFAULTS.get("temperature", 0.2)
            else:
                rc["temperature"] = temp

    # ── 旧配置清理（见上方说明）：历史遗留的顶层 models 字段 ──
    rc.pop("models", None)

    if "multimodal_models" in rc:
        if not isinstance(rc["multimodal_models"], (list, tuple)):
            rc["multimodal_models"] = DEFAULTS.get("multimodal_models", [])
        else:
            rc["multimodal_models"] = [str(m) for m in rc["multimodal_models"]]

    # MCP 外部工具服务器：非列表回退空列表；有配置时才导入 mcp 包做结构清洗
    # （默认空列表路径零导入开销，避免无 MCP 场景拖慢配置加载）
    if "mcp_servers" in rc:
        if not isinstance(rc["mcp_servers"], (list, tuple)):
            rc["mcp_servers"] = []
        elif rc["mcp_servers"]:
            try:
                from ..mcp.config import validate_mcp_servers
                rc["mcp_servers"] = validate_mcp_servers(rc["mcp_servers"])
            except Exception:
                # mcp 子系统导入失败（依赖缺失等）不应让配置加载崩溃：
                # 退化为结构过滤（仅保留对象条目），副作用是条目不被清洗。
                rc["mcp_servers"] = [c for c in rc["mcp_servers"] if isinstance(c, dict)]

    if "token_prices" in rc:
        if not isinstance(rc["token_prices"], dict):
            rc["token_prices"] = DEFAULTS["token_prices"]
        else:
            cleaned = {}
            for model, prices in rc["token_prices"].items():
                if isinstance(prices, dict) and "input" in prices and "output" in prices:
                    try:
                        entry = {
                            "input": float(prices["input"]),
                            "output": float(prices["output"])
                        }
                        # 保留可选缓存命中价格（缺失时 /cost 回退按 input 全价）
                        if "input_cache_hit" in prices:
                            entry["input_cache_hit"] = float(prices["input_cache_hit"])
                        cleaned[str(model)] = entry
                    except (ValueError, TypeError):
                        continue
            rc["token_prices"] = cleaned

    if rc.get("max_retries", 1) < 0:
        rc["max_retries"] = DEFAULTS["max_retries"]
    if rc.get("max_context_chars", 1) < 0:
        rc["max_context_chars"] = DEFAULTS["max_context_chars"]
    # auto_force_compress_threshold 为 token 口径阈值：负值无意义，回退默认
    if rc.get("auto_force_compress_threshold", 1) < 0:
        rc["auto_force_compress_threshold"] = DEFAULTS["auto_force_compress_threshold"]

    # token_prices 缺省回退（按当前生效档案的 provider 取内置价表）由
    # ``src/config/__init__.py`` 的 TOKEN_PRICES 惰性键负责——schema 保持零
    # 依赖（不引用 model_profiles，避免 config 包内循环导入）。
    return rc
