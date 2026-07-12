"""JSON 修复模块 — 从 api/tool_parse.py 拆分而来

提供 LLM 返回的常见 JSON 格式问题的自动修复能力，
含线程安全的统计计数器。
"""

from __future__ import annotations

import json
import re
import threading
import copy
import logging

_logger = logging.getLogger(__name__)


# ── 统计（线程安全） ──

_JSON_REPAIR_STATS = {"attempts": 0, "success": 0, "fail": 0}
_JSON_REPAIR_LOCK = threading.RLock()

# 解析重试统计（由 model_async._retry_on_parse_failure_async 更新）
_PARSE_RETRY_STATS: dict[str, int] = {"retry_triggered": 0, "retry_success": 0, "retry_exhausted": 0}


def get_repair_stats():
    """返回 JSON 修复统计 + 解析重试统计的深拷贝（线程安全）。"""
    with _JSON_REPAIR_LOCK:
        stats = copy.deepcopy(_JSON_REPAIR_STATS)
        stats["parse_retry"] = dict(_PARSE_RETRY_STATS)
        return stats


def reset_repair_stats():
    """重置 JSON 修复统计和解析重试统计计数器为零（线程安全）。"""
    with _JSON_REPAIR_LOCK:
        _JSON_REPAIR_STATS["attempts"] = 0
        _JSON_REPAIR_STATS["success"] = 0
        _JSON_REPAIR_STATS["fail"] = 0
        _PARSE_RETRY_STATS["retry_triggered"] = 0
        _PARSE_RETRY_STATS["retry_success"] = 0
        _PARSE_RETRY_STATS["retry_exhausted"] = 0


# ── 内部修复函数 ──

def _strip_code_block(s: str) -> str:
    """去除 ```json ... ``` 或 ``` ... ``` 包裹。"""
    if s.startswith("```"):
        s = re.sub(r'^```(?:json)?\s*', '', s)
        s = re.sub(r'\s*```$', '', s)
        s = s.strip()
    return s


def _fix_quotes(s: str) -> str:
    """将最外层单引号替换为双引号（保护已有双引号字符串）。"""
    import uuid
    _protected = {}

    def _protect(m):
        uid = uuid.uuid4().hex
        placeholder = f'\x00PRQT{uid}\x00'
        _protected[placeholder] = m.group(0)
        return placeholder

    s = re.sub(r'"(?:[^"\\]|\\.)*"', _protect, s)
    s = s.replace("'", '"')
    for placeholder, original_str in _protected.items():
        s = s.replace(placeholder, original_str)
    return s


def _remove_comments(s: str) -> str:
    """去除行内注释 // 和块注释 /* */。"""
    s = re.sub(r'//[^\n]*', '', s)
    s = re.sub(r'/\*.*?\*/', '', s, flags=re.DOTALL)
    return s


def _fix_unquoted_keys(s: str) -> str:
    """为未加引号的 key 补上双引号，并补上值后缺失的逗号。

    处理场景（按顺序）：
    1. { 后的第一个 key（如 {key: → {"key":）
    2. , 后的 key（如 , key: → , "key":）
    3. 值（true/false/null/数字）后紧跟的未引号 key（如 true key: → true, "key":）
    4. 结构结束符 } ] " ' 后紧跟的未引号 key（如 } key: → }, "key":）
    """
    s = re.sub(r'(?<=\{)(\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', s)
    s = re.sub(r'(,\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', s)
    # 值后缺逗号 + 未引号 key：补逗号并加引号
    s = re.sub(r'(true|false|null|\d+)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1, "\2":', s)
    # 结构结束符后缺逗号 + 未引号 key：补逗号并加引号
    s = re.sub(r'([}\]"\'])\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1, "\2":', s)
    return s


def _fix_trailing_commas(s: str) -> str:
    """去除 ]} 前的多余逗号。"""
    return re.sub(r',\s*([}\]])', r'\1', s)


def _remove_control_chars(s: str) -> str:
    """移除未转义的控制字符。"""
    return re.sub(r'[\x00-\x1f]', '', s)


def _fix_python_literals(s: str) -> str:
    """将 Python 布尔值/None 替换为 JSON 标准格式。"""
    _protected = {}

    def _protect(m):
        idx = len(_protected)
        ph = f'\x00PYLIT{idx}\x00'
        _protected[ph] = m.group(0)
        return ph

    s = re.sub(r'"(?:[^"\\]|\\.)*"', _protect, s)
    s = re.sub(r"'(?:[^'\\]|\\.)*'", _protect, s)

    s = re.sub(r'\bTrue\b', 'true', s)
    s = re.sub(r'\bFalse\b', 'false', s)
    s = re.sub(r'\bNone\b', 'null', s)

    for ph, orig in _protected.items():
        s = s.replace(ph, orig)
    return s


def _fix_unescaped_quotes(s: str) -> str:
    """修复字符串值中未转义的双引号。"""
    _protected = {}

    def _protect(m):
        idx = len(_protected)
        ph = f'\x00UQ{idx}\x00'
        _protected[ph] = m.group(0)
        return ph

    s = re.sub(r'"(?:[^"\\]|\\.)*"', _protect, s)
    s = s.replace('"', '\\"')

    for ph, orig in _protected.items():
        s = s.replace(ph, orig)
    return s


def _fix_extra_brackets(s: str) -> str:
    """去除多余的右括号 } 和 ]，并按需补全缺失的右括号。
    
    注意：会跳过字符串值内的括号字符，避免误处理。
    """
    open_to_close = {'{': '}', '[': ']'}
    close_to_open = {'}': '{', ']': '['}
    stack: list[str] = []
    pos_stack: list[int] = []
    extra_close_positions: list[int] = []
    
    in_string = False
    escape = False

    for i, ch in enumerate(s):
        # 跳过字符串值内的内容
        if in_string:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch in '{[':
            stack.append(ch)
            pos_stack.append(i)
        elif ch in '}]':
            if stack and stack[-1] == close_to_open.get(ch):
                stack.pop()
                pos_stack.pop()
            else:
                extra_close_positions.append(i)

    chars = list(s)
    for pos in reversed(extra_close_positions):
        if pos < len(chars):
            chars.pop(pos)

    result = ''.join(chars)
    if stack:
        for ch in reversed(stack):
            result += open_to_close[ch]

    return result


def _remove_zero_width_chars(s: str) -> str:
    """移除零宽空格和其他不可见 Unicode 字符。"""
    return re.sub(
        r'[\u200b\u200c\u200d\u200e\u200f\u2060\u2061\u2062\u2063\u2064'
        r'\ufeff\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5'
        r'\u180e\u2028\u2029\u202a\u202b\u202c\u202d\u202e\u202f]',
        '', s
    )


def _fix_missing_commas(s: str) -> str:
    """修复 JSON 中缺失的逗号和双逗号，预防 'Expecting ',' delimiter' 错误。

    处理场景（顺序）：
    1. 双逗号 → 合并为单逗号: `,,` → `,`
    2. } 后缺逗号（后跟 key 或下一个 value）
    3. ] 后缺逗号（后跟下一个元素）
    4. 数字/bool/null 值后缺逗号（后跟下一个 key/value）

    所有修复都在保护双引号字符串的前提下进行，避免误伤字符串内内容。
    """
    _protected = {}

    def _protect(m):
        idx = len(_protected)
        ph = f'\x00MC{idx}\x00'
        _protected[ph] = m.group(0)
        return ph

    s = re.sub(r'"(?:[^"\\]|\\.)*"', _protect, s)

    # 1. 双逗号合并
    s = re.sub(r',\s*,', ', ', s)

    # 2. } 后缺逗号：} 紧跟 "key"/{/[ → 补逗号
    s = re.sub(r'}(\s*)(\x00MC\d+\x00)', r'},\1\2', s)
    s = re.sub(r'}(\s*)(\{)', r'},\1\2', s)
    s = re.sub(r'}(\s*)(\[)', r'},\1\2', s)

    # 3. ] 后缺逗号：] 紧跟 "key"/{/[ → 补逗号
    s = re.sub(r'\](\s*)(\x00MC\d+\x00)', r'],\1\2', s)
    s = re.sub(r'\](\s*)(\{)', r'],\1\2', s)
    s = re.sub(r'\](\s*)(\[)', r'],\1\2', s)

    # 4. 两个字符串值之间缺逗号（"value" "key" → "value", "key"）
    s = re.sub(r'(\x00MC\d+\x00)(\s+)(\x00MC\d+\x00)', r'\1,\2\3', s)

    # 5. 值后缺逗号：数字/bool/null 后跟下一个值（含空格连接 → 循环补全）
    while True:
        new_s = re.sub(
            r'(\d+|true|false|null)(\s+)'
            r'(\d+|true|false|null|\x00MC\d+\x00|[\{\[])',
            r'\1,\2\3', s
        )
        if new_s == s:
            break
        s = new_s

    for ph, orig in _protected.items():
        s = s.replace(ph, orig)
    return s


def _repair_json(s: str) -> str:
    """尝试修复 LLM 返回的常见 JSON 格式问题。

    处理场景（按顺序）：
    1. 去除代码块标记 (```json ... ```)
    2. 去除 BOM、前后空白
    3. 单引号 → 双引号
    4. 去除注释
    5. 未引号 key → 补引号
    6. Python 布尔值/None → JSON 标准
    7. 尾逗号 → 去掉
    8. 缺失逗号 → 补上（含双逗号合并）
    9. 控制字符 → 移除
    10. 零宽空格 → 移除
    11. 多余/缺失括号 → 修正
    12. 未转义双引号 → 转义（激进兜底）
    """
    if not s or not s.strip():
        return s

    s = s.strip()
    s = _strip_code_block(s)
    s = s.lstrip('\ufeff')

    try:
        json.loads(s)
        return s
    except json.JSONDecodeError:
        pass

    with _JSON_REPAIR_LOCK:
        _JSON_REPAIR_STATS["attempts"] += 1
    original = s

    def _try_early(s):
        try:
            json.loads(s)
            return True, s
        except json.JSONDecodeError:
            return False, s

    s = _fix_quotes(s)
    _ok, s = _try_early(s)
    if _ok:
        with _JSON_REPAIR_LOCK:
            _JSON_REPAIR_STATS["success"] += 1
        return s

    s = _remove_comments(s)
    _ok, s = _try_early(s)
    if _ok:
        with _JSON_REPAIR_LOCK:
            _JSON_REPAIR_STATS["success"] += 1
        return s

    s = _fix_unquoted_keys(s)
    _ok, s = _try_early(s)
    if _ok:
        with _JSON_REPAIR_LOCK:
            _JSON_REPAIR_STATS["success"] += 1
        return s

    s = _fix_python_literals(s)
    _ok, s = _try_early(s)
    if _ok:
        with _JSON_REPAIR_LOCK:
            _JSON_REPAIR_STATS["success"] += 1
        return s

    s = _fix_trailing_commas(s)
    _ok, s = _try_early(s)
    if _ok:
        with _JSON_REPAIR_LOCK:
            _JSON_REPAIR_STATS["success"] += 1
        return s

    s = _fix_missing_commas(s)
    _ok, s = _try_early(s)
    if _ok:
        with _JSON_REPAIR_LOCK:
            _JSON_REPAIR_STATS["success"] += 1
        return s

    s = _remove_control_chars(s)
    _ok, s = _try_early(s)
    if _ok:
        with _JSON_REPAIR_LOCK:
            _JSON_REPAIR_STATS["success"] += 1
        return s

    s = _remove_zero_width_chars(s)
    _ok, s = _try_early(s)
    if _ok:
        with _JSON_REPAIR_LOCK:
            _JSON_REPAIR_STATS["success"] += 1
        return s

    s = _fix_extra_brackets(s)
    _ok, s = _try_early(s)
    if _ok:
        with _JSON_REPAIR_LOCK:
            _JSON_REPAIR_STATS["success"] += 1
        return s

    s_with_escaped = _fix_unescaped_quotes(s)
    s_with_escaped = _fix_trailing_commas(s_with_escaped)

    try:
        json.loads(s_with_escaped)
        with _JSON_REPAIR_LOCK:
            _JSON_REPAIR_STATS["success"] += 1
        return s_with_escaped
    except json.JSONDecodeError:
        with _JSON_REPAIR_LOCK:
            _JSON_REPAIR_STATS["fail"] += 1
        _logger.debug("JSON修复失败: %s -> %s", original[:100], s[:100])
        return original


def json_loads_safe(s: str):
    """安全的 JSON 解析，含自动修复。返回 (dict, 是否修复)。"""
    if not s or s.strip() == "null":
        return {}, False
    try:
        result = json.loads(s)
        if result is None:
            return {}, False
        return result, False
    except json.JSONDecodeError as e:
        _logger.debug("JSON解析失败，尝试修复: %s", e)
        original_exc = e
        repaired = _repair_json(s)
        if repaired != s:
            try:
                result = json.loads(repaired)
                _logger.info("JSON修复成功: %s...", s[:80])
                return result, True
            except json.JSONDecodeError:
                pass
        raise original_exc
