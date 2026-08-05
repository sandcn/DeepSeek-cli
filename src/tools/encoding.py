"""编码检测模块 — 从 tools/utils.py 拆分而来

提供 detect_encoding() 和 async_detect_encoding() 两个入口，
均支持通过 raw_bytes 参数传入已读取的文件字节，避免重复 IO。
"""

from __future__ import annotations

import asyncio
import logging

from ._constants import (
    CATCHALL_ENCODINGS,
    COMMON_ENCODINGS,
    ENCODING_ALIASES,
    FALLBACK_ENCODINGS,
    MAX_DETECT_BYTES,
)

_logger = logging.getLogger(__name__)

# ── 编码检测 ──

try:
    import chardet
    CHARDET_AVAILABLE = True
except ImportError:
    CHARDET_AVAILABLE = False


def _read_bytes(file_path: str, max_bytes: int = MAX_DETECT_BYTES) -> bytes:
    """从文件读取字节用于编码检测，读取前 max_bytes 字节。"""
    with open(file_path, 'rb') as f:
        return f.read(max_bytes)


def pick_best_decoding(
    raw_bytes: bytes,
    candidate_encodings: list[str],
) -> tuple[str, str]:
    """从候选编码列表中选解码质量最好的编码，返回 (encoding, content) 元组。

    质量评分规则（降序）：
      1. 零错误解码且无 \ufffd 替代字符（非通吃编码）→ 首选，立即返回
      2. 通吃编码零替代字符 → 不立即返回，给非通吃候选竞争机会
      3. 非通吃编码有少量替代字符（strict 解码成功但含 \ufffd）
      4. 有解码错误（需 errors='replace'）→ 末选

    通吃编码（latin-1 / iso-8859-* / koi8-r 等）能解码任意字节且无 \ufffd，
    因此「零替代字符」是其固有属性而非真实质量信号。必须大幅降低其评分，
    防止通吃编码因得分虚高而覆盖真实编码（如 UTF-8 文件有少量损坏字节时）。

    返回评分最高的 (编码名, 解码后文本)，同分时返回列表中靠前的。
    若所有候选均无法解码出任何内容，用首个候选的 replace 模式兜底。
    """
    best_enc = candidate_encodings[0]
    best_content = ""
    best_score = -1  # 越大越好

    seen: set[str] = set()
    for enc in candidate_encodings:
        # 跳过已尝试的重复编码，避免重复解码
        if enc in seen:
            continue
        seen.add(enc)
        # 尝试 strict 解码
        try:
            decoded = raw_bytes.decode(enc, errors='strict')
            replacement_count = decoded.count('\ufffd')
            if replacement_count == 0:
                # 通吃编码：能解码任意字节但不一定是真实编码，不立即返回
                if enc.lower() in CATCHALL_ENCODINGS:
                    score = 60  # 通吃编码降分——解码任意字节是无意义的"成功"
                else:
                    return enc, decoded  # 完美解码且非通吃编码，直接返回
            else:
                # 非通吃编码有少量损坏字节：仍可能优于通吃编码
                score = 100 - replacement_count * 2  # 替代字符越少越好
        except UnicodeDecodeError:
            # strict 失败，用 replace 降级
            try:
                decoded = raw_bytes.decode(enc, errors='replace')
                replacement_count = decoded.count('\ufffd')
                # replace 模式评分基础 70——高于通吃编码的 60，确保非通吃编码
                # 即使 strict 失败且有少量损坏字节，仍优于通吃编码（如 latin-1
                # 虽然 0 替代字符但内容完全错误）。仅当损坏较多时才让通吃编码胜出。
                score = 70 - replacement_count
            except Exception:
                continue

        if score > best_score:
            best_score = score
            best_enc = enc
            best_content = decoded

    if best_content:
        return best_enc, best_content
    # 终极降级：用第一个候选编码的 replace 模式
    return candidate_encodings[0], raw_bytes.decode(candidate_encodings[0], errors='replace')


def _validate_decoding_quality(raw_bytes: bytes, detected_encoding: str) -> str:
    """验证检测到的编码解码质量，如果质量差则尝试其他常见编码。

    对通吃编码（如 latin-1, koi8-r, iso-8859-* 等）即使 strict 解码成功
    也不立即返回——它们能解码任意字节，可能掩盖真实编码。
    """
    if not raw_bytes:
        return detected_encoding

    # 通吃编码：strict 解码必然成功，但可能非真实编码
    if detected_encoding.lower() in CATCHALL_ENCODINGS:
        pass  # 不返回，走 fallback 候选择优
    else:
        try:
            decoded = raw_bytes.decode(detected_encoding, errors='strict')
            if decoded.count('\ufffd') == 0:
                return detected_encoding
        except UnicodeDecodeError:
            pass

    # 从 fallback 候选重选，检测结果排首位供候选
    candidates = [detected_encoding]
    for enc in FALLBACK_ENCODINGS:
        if enc not in candidates:
            candidates.append(enc)

    best, _ = pick_best_decoding(raw_bytes, candidates)
    if best != detected_encoding:
        _logger.info(
            "编码质量验证: %s 解码质量差，改用 %s (raw_bytes=%d)",
            detected_encoding, best, len(raw_bytes),
        )
    return best


def detect_encoding(file_path: str = "", raw_bytes: bytes | None = None) -> str:
    """检测文件编码。

    参数：
      file_path: 文件路径（当 raw_bytes 为 None 时使用）
      raw_bytes: 已读取的文件字节（优先使用，避免重复 IO）

    返回编码名称（小写）。
    """
    try:
        # 优先使用传入的 raw_bytes，否则从文件读取
        sample: bytes = raw_bytes
        if sample is None:
            if not file_path:
                return 'utf-8'
            sample = _read_bytes(file_path)

        if not sample:
            return 'utf-8'

        # 检查BOM标记（优化：分支匹配替代循环遍历，消除dict迭代+startswith开销）
        head4 = sample[:4]
        if head4 == b'\x00\x00\xfe\xff':
            return 'utf-32-be'
        if head4 == b'\xff\xfe\x00\x00':
            return 'utf-32-le'
        if head4[:3] == b'\xef\xbb\xbf':
            return 'utf-8-sig'
        if head4[:2] == b'\xff\xfe':
            return 'utf-16-le'
        if head4[:2] == b'\xfe\xff':
            return 'utf-16-be'

        # UTF-8 fast path：BOM 检查之后、chardet 之前先尝试 UTF-8 解码，
        # 避免 chardet 将 UTF-8 内容误检为 windows-1252 等通吃编码。
        try:
            sample.decode('utf-8')
            return 'utf-8'
        except UnicodeDecodeError:
            pass

        chardet_encoding = None  # chardet 检测到的原始编码（未映射前）

        # 使用 chardet 检测（用全部样本数据，提高统计准确率）
        if CHARDET_AVAILABLE and sample:
            result = chardet.detect(sample)
            if result and result.get('encoding'):
                raw_enc = result['encoding'].lower()
                confidence = result.get('confidence', 0)

                if raw_enc == 'windows-1252' and confidence > 0.5:
                    # windows-1252 常误检 UTF-8 内容，UTF-8 fast path 已在上方验证，
                    # 此处直接映射为 latin-1
                    chardet_encoding = 'latin-1'
                elif confidence > 0.5:
                    chardet_encoding = raw_enc
                    # 别名映射
                    if chardet_encoding in ENCODING_ALIASES:
                        chardet_encoding = ENCODING_ALIASES[chardet_encoding]

        # 如果 chardet 给出了高置信度结果，返回映射后结果（经解码质量验证）
        if chardet_encoding:
            # iso-8859-5 特殊处理：若 GBK 解码无替代字符，优先返回 gbk
            if chardet_encoding == 'iso-8859-5':
                decoded = sample.decode('gbk', errors='replace')
                if decoded.count('\ufffd') == 0:
                    return 'gbk'
                return 'iso-8859-5'
            return _validate_decoding_quality(sample, chardet_encoding)

        # chardet 无结果或低置信度 → 尝试常见编码
        for enc in COMMON_ENCODINGS:
            try:
                sample.decode(enc)
                return enc
            except UnicodeDecodeError:
                continue

        return 'utf-8'
    except Exception:
        _logger.exception("编码检测异常，回退 utf-8")
        return 'utf-8'


async def async_detect_encoding(file_path: str = "", raw_bytes: bytes | None = None) -> str:
    """异步检测文件编码。

    参数：
      file_path: 文件路径（当 raw_bytes 为 None 时使用）
      raw_bytes: 已读取的文件字节（优先使用）

    委托同步版通过 asyncio.to_thread 执行。
    """
    return await asyncio.to_thread(detect_encoding, file_path, raw_bytes)
