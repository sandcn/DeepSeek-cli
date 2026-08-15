"""结构化遥测 — JSON Lines 格式的模型调用日志。

每条记录包含：
- ts: ISO8601 时间戳
- session_id: 会话 ID（如有）
- model: 模型名称
- input_tokens / output_tokens
- latency_ms: 调用耗时（毫秒）
- tool_calls: 本次调用的工具名列表
- cost_usd: 估算费用
- interrupted: 是否被中断
- provider: 提供商名称

日志文件: .chat/log/YYYY-MM-DD.jsonl（项目根目录下）
每日轮转，自动创建目录。
"""

import json
import logging
import threading
from datetime import datetime

from ..paths import CHAT_DIR

_logger = logging.getLogger(__name__)

# ── 默认 token 单价（USD/1M tokens）────────────────────────
# 当无法从配置中获取价格时使用此默认值。
# 对应 DeepSeek V4 Pro 标准定价（匹配 config/defaults.py 中 deepseek-v4-pro/flash 价格）。
# ★ 单位修复（review 方向）：配置价格单位是「美元 / 百万 tokens」（见
#   config/defaults.py 注释与 /cost 的 compute_cost 均按 1_000_000 计费），
#   修复前命名为 _PER_1K 且按 /1000 计费，cost_usd 虚高 1000 倍。
_DEFAULT_INPUT_PRICE_PER_1M = 0.55
_DEFAULT_OUTPUT_PRICE_PER_1M = 2.19

# ── 路径常量 ───────────────────────────────────────────────
_TELEMETRY_DIR = CHAT_DIR / "log"

# ── 线程安全写入锁 ─────────────────────────────────────────
_write_lock = threading.RLock()
# 缓存当前日期，避免重复 stat
_cached_date = None
_cached_filepath = None


def _get_log_path():
    """获取当前日期对应的日志文件路径（每日轮转）。"""
    global _cached_date, _cached_filepath
    today = datetime.now().strftime("%Y-%m-%d")
    if _cached_date != today:
        _cached_date = today
        _cached_filepath = _TELEMETRY_DIR / f"{today}.jsonl"
    return _cached_filepath


def _ensure_dir():
    """确保日志目录存在。"""
    _TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)


def _estimate_cost(model, input_tokens, output_tokens, provider="unknown"):
    """估算本次调用的费用（USD）。

    优先从运行时配置读取 token 单价，回退默认值。
    """
    try:
        from ..config import TOKEN_PRICES
        prices = TOKEN_PRICES
        if model in prices:
            price = prices[model]
            input_price = price.get("input", _DEFAULT_INPUT_PRICE_PER_1M)
            output_price = price.get("output", _DEFAULT_OUTPUT_PRICE_PER_1M)
        else:
            input_price = _DEFAULT_INPUT_PRICE_PER_1M
            output_price = _DEFAULT_OUTPUT_PRICE_PER_1M
            for m, p in prices.items():
                if m in model or model in m:
                    input_price = p.get("input", _DEFAULT_INPUT_PRICE_PER_1M)
                    output_price = p.get("output", _DEFAULT_OUTPUT_PRICE_PER_1M)
                    break
    except (ImportError, Exception):
        input_price = _DEFAULT_INPUT_PRICE_PER_1M
        output_price = _DEFAULT_OUTPUT_PRICE_PER_1M

    # 单价单位为 USD/百万 tokens → 除以 1_000_000（与 /cost 的 compute_cost 一致）
    return (input_tokens / 1_000_000 * input_price) + (output_tokens / 1_000_000 * output_price)


def record_call(
    model="?",
    input_tokens=0,
    output_tokens=0,
    latency_ms=0.0,
    tool_calls=None,
    interrupted=False,
    provider="unknown",
    session_id=None,
):
    """记录一次模型调用到 JSON Lines 日志。

    线程安全，每日轮转日志文件，自动创建目录。
    """
    if tool_calls is None:
        tool_calls = []

    cost_usd = _estimate_cost(model, input_tokens, output_tokens, provider)

    record = {
        "ts": datetime.now().isoformat(),
        "session_id": session_id,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "latency_ms": round(latency_ms, 1),
        "tool_calls": tool_calls,
        "cost_usd": round(cost_usd, 6),
        "interrupted": interrupted,
        "provider": provider,
    }

    with _write_lock:
        _ensure_dir()
        log_path = _get_log_path()
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            _logger.warning("遥测日志写入失败: %s", log_path, exc_info=True)
