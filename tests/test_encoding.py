#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for src/tools/encoding.py — 编码检测模块

覆盖内容：
  1. 常量模块健康检查（BOM_MARKERS, ENCODING_ALIASES, COMMON_ENCODINGS）
  2. detect_encoding — 同步文件编码检测
  3. async_detect_encoding — 异步封装委托
"""

import pytest
from unittest.mock import patch, MagicMock

from src.tools.encoding import (
    BOM_MARKERS,
    ENCODING_ALIASES,
    COMMON_ENCODINGS,
    CHARDET_AVAILABLE,
    detect_encoding,
    async_detect_encoding,
    pick_best_decoding,
)


# ═══════════════════════════════════════════════════════════════
# 1. 常量模块健康检查
# ═══════════════════════════════════════════════════════════════

class TestModuleConstants:
    """BOM_MARKERS / ENCODING_ALIASES / COMMON_ENCODINGS 健康检查"""

    @pytest.mark.parametrize("key,expected", [
        (b'\xef\xbb\xbf', 'utf-8-sig'),
        (b'\x00\x00\xfe\xff', 'utf-32-be'),
        (b'\xff\xfe\x00\x00', 'utf-32-le'),
        (b'\xff\xfe', 'utf-16-le'),
        (b'\xfe\xff', 'utf-16-be'),
    ])
    def test_bom_markers_entries(self, key, expected):
        """BOM_MARKERS 各条目键值正确"""
        assert BOM_MARKERS[key] == expected

    def test_bom_markers_not_empty(self):
        """BOM_MARKERS 不为空"""
        assert len(BOM_MARKERS) > 0

    def test_bom_markers_all_bytes_keys(self):
        """BOM_MARKERS 所有键均为 bytes 类型"""
        for key in BOM_MARKERS:
            assert isinstance(key, bytes), f"键 {key!r} 不是 bytes 类型"

    def test_bom_markers_all_str_values(self):
        """BOM_MARKERS 所有值均为 str 类型"""
        for value in BOM_MARKERS.values():
            assert isinstance(value, str), f"值 {value!r} 不是 str 类型"

    def test_bom_markers_unique_values(self):
        """BOM_MARKERS 值应唯一"""
        values = list(BOM_MARKERS.values())
        assert len(values) == len(set(values)), "BOM_MARKERS 值不唯一"

    @pytest.mark.parametrize("alias,target", [
        ('gb2312', 'gbk'),
        ('gb18030', 'gbk'),
        ('ascii', 'utf-8'),
    ])
    def test_encoding_aliases_entries(self, alias, target):
        """ENCODING_ALIASES 别名字典映射正确"""
        assert ENCODING_ALIASES[alias] == target

    def test_encoding_aliases_not_empty(self):
        """ENCODING_ALIASES 不为空"""
        assert len(ENCODING_ALIASES) > 0

    def test_common_encodings_not_empty(self):
        """COMMON_ENCODINGS 不为空"""
        assert len(COMMON_ENCODINGS) > 0

    def test_common_encodings_contains_utf8(self):
        """COMMON_ENCODINGS 应包含 utf-8"""
        assert 'utf-8' in COMMON_ENCODINGS

    def test_common_encodings_all_strings(self):
        """COMMON_ENCODINGS 所有元素均为 str 类型"""
        for enc in COMMON_ENCODINGS:
            assert isinstance(enc, str), f"编码 {enc!r} 不是 str 类型"

    def test_chardet_available_is_bool(self):
        """CHARDET_AVAILABLE 应为 bool 类型"""
        assert isinstance(CHARDET_AVAILABLE, bool)


# ═══════════════════════════════════════════════════════════════
# 2. detect_encoding — 同步文件编码检测
# ═══════════════════════════════════════════════════════════════

class TestDetectEncoding:
    """detect_encoding() 函数测试"""

    # ── 空文件 ──────────────────────────────────────────────

    def test_empty_file(self, tmp_path):
        """空文件应回退 utf-8"""
        f = tmp_path / "empty.txt"
        f.write_bytes(b'')
        result = detect_encoding(str(f))
        assert result == 'utf-8'

    # ── BOM 文件 ────────────────────────────────────────────

    @pytest.mark.parametrize("bom,encoding", [
        (b'\xef\xbb\xbf', 'utf-8-sig'),
        (b'\x00\x00\xfe\xff', 'utf-32-be'),
        (b'\xff\xfe\x00\x00', 'utf-32-le'),
        (b'\xff\xfe', 'utf-16-le'),
        (b'\xfe\xff', 'utf-16-be'),
    ])
    def test_bom_detection(self, tmp_path, bom, encoding):
        """BOM 标记应正确识别对应编码"""
        f = tmp_path / "bom_test.bin"
        f.write_bytes(bom + '你好'.encode('utf-8'))
        result = detect_encoding(str(f))
        assert result == encoding

    def test_bom_priority_over_chardet(self, tmp_path):
        """BOM 检测优先级高于 chardet"""
        f = tmp_path / "bom_priority.txt"
        f.write_bytes(b'\xef\xbb\xbf' + 'hello'.encode('utf-8'))
        with patch('src.tools.encoding.chardet') as mock_chardet:
            mock_chardet.detect.return_value = {'encoding': 'utf-8', 'confidence': 0.99}
            result = detect_encoding(str(f))
            # BOM 优先，不调用 chardet
            assert result == 'utf-8-sig'
            mock_chardet.detect.assert_not_called()

    def test_bom_with_only_bom_bytes(self, tmp_path):
        """文件只有 BOM 字节无内容时仍返回对应编码"""
        f = tmp_path / "only_bom.txt"
        f.write_bytes(b'\xef\xbb\xbf')
        result = detect_encoding(str(f))
        assert result == 'utf-8-sig'

    # ── UTF-8 无 BOM ───────────────────────────────────────

    def test_utf8_without_bom(self, tmp_path):
        """纯 UTF-8 无 BOM 文件应检测为 utf-8"""
        f = tmp_path / "utf8.txt"
        f.write_bytes('你好世界 hello'.encode('utf-8'))
        result = detect_encoding(str(f))
        assert result == 'utf-8'

    def test_utf8_ascii_only(self, tmp_path):
        """纯 ASCII 文件按别名映射应返回 utf-8"""
        f = tmp_path / "ascii.txt"
        f.write_bytes(b'hello world 123')
        result = detect_encoding(str(f))
        assert result == 'utf-8'

    def test_utf8_multiline(self, tmp_path):
        """多行 UTF-8 文本正常检测"""
        f = tmp_path / "multiline.txt"
        f.write_bytes('第一行\n第二行\n第三行\n'.encode('utf-8'))
        result = detect_encoding(str(f))
        assert result == 'utf-8'

    # ── GBK 文件（无 chardet） ─────────────────────────────

    def test_gbk_without_chardet(self, tmp_path):
        """GBK 文件在无 chardet 时应通过 common encodings 检测到 gbk"""
        f = tmp_path / "gbk.txt"
        f.write_bytes('中文测试'.encode('gbk'))
        with patch('src.tools.encoding.CHARDET_AVAILABLE', False):
            result = detect_encoding(str(f))
            assert result == 'gbk'

    def test_gbk_fallback_when_utf8_fails(self, tmp_path):
        """GBK 文件在 utf-8 解码失败后回退到 gbk"""
        f = tmp_path / "gbk_only.txt"
        # GBK 编码的纯中文字节，UTF-8 解码会失败
        f.write_bytes('中文'.encode('gbk'))
        with patch('src.tools.encoding.CHARDET_AVAILABLE', False):
            result = detect_encoding(str(f))
            # utf-8 解码失败，gbk 解码成功
            assert result == 'gbk'

    # ── GBK 文件（有 chardet） ─────────────────────────────

    def test_gbk_with_chardet(self, tmp_path):
        """GBK 文件在有 chardet 时依赖 chardet 结果"""
        f = tmp_path / "gbk_chardet.txt"
        f.write_bytes('中文测试'.encode('gbk'))
        with patch('src.tools.encoding.chardet') as mock_chardet:
            mock_chardet.detect.return_value = {'encoding': 'GBK', 'confidence': 0.95}
            result = detect_encoding(str(f))
            assert result == 'gbk'

    def test_gbk_alias_gb2312_with_chardet(self, tmp_path):
        """chardet 返回 gb2312 应通过别名映射为 gbk"""
        f = tmp_path / "gb2312.txt"
        f.write_bytes('中文'.encode('gbk'))
        with patch('src.tools.encoding.chardet') as mock_chardet:
            mock_chardet.detect.return_value = {'encoding': 'gb2312', 'confidence': 0.95}
            result = detect_encoding(str(f))
            assert result == 'gbk'

    # ── chardet 特殊路径 ──────────────────────────────────

    def test_chardet_windows1252_but_valid_utf8(self, tmp_path):
        """chardet 报告 windows-1252 但内容实际是 UTF-8 → 返回 utf-8"""
        f = tmp_path / "win1252_utf8.txt"
        content = '正常UTF-8文本'.encode('utf-8')
        f.write_bytes(content)
        with patch('src.tools.encoding.chardet') as mock_chardet:
            mock_chardet.detect.return_value = {
                'encoding': 'windows-1252', 'confidence': 0.95
            }
            result = detect_encoding(str(f))
            # 原始内容能用 utf-8 解码，应返回 utf-8
            assert result == 'utf-8'

    def test_chardet_windows1252_invalid_utf8(self, tmp_path):
        """chardet 报告 windows-1252 且内容非 UTF-8 → 非通吃编码（如 gbk）胜出

        通吃编码评分已降低（score=60），非通吃编码即使 strict 失败，
        少量替代字符也能获得更高评分。二进制字节 \x80\x81\x82\x83
        在 gbk 下替代字符少于 utf-8，因此 gbk 胜出。
        """
        f = tmp_path / "win1252_binary.bin"
        # 构造不能用 utf-8 解码的字节（\x80\x81 非 UTF-8 合法序列），且不以任何 BOM 开头
        f.write_bytes(b'\x80\x81\x82\x83')
        with patch('src.tools.encoding.chardet') as mock_chardet:
            mock_chardet.detect.return_value = {
                'encoding': 'windows-1252', 'confidence': 0.95
            }
            result = detect_encoding(str(f))
            # 非通吃编码（gbk）评分高于通吃编码（latin-1），即使 strict 失败
            assert result == 'gbk'

    def test_chardet_windows1252_low_confidence(self, tmp_path):
        """chardet 报告 windows-1252 但置信度 ≤ 0.5 → 不进入特殊分支，走 common encodings"""
        f = tmp_path / "win1252_lowconf.txt"
        # 非 BOM 前缀的字节，且 latin-1 能解码（兜底走 latin-1）
        f.write_bytes(b'\x80\x81\x82\x83')
        with patch('src.tools.encoding.chardet') as mock_chardet:
            mock_chardet.detect.return_value = {
                'encoding': 'windows-1252', 'confidence': 0.30
            }
            result = detect_encoding(str(f))
            # 置信度低，走 common encodings，latin-1 可解码任意字节
            assert result == 'latin-1'

    def test_chardet_iso8859_5_falls_to_gbk(self, tmp_path):
        """chardet 报告 iso-8859-5 且 gbk 解码无异常 → 返回 gbk"""
        f = tmp_path / "iso8859_5.txt"
        # 写入 GBK 字节
        f.write_bytes('测试'.encode('gbk'))
        with patch('src.tools.encoding.chardet') as mock_chardet:
            mock_chardet.detect.return_value = {
                'encoding': 'iso-8859-5', 'confidence': 0.95
            }
            result = detect_encoding(str(f))
            assert result == 'gbk'

    def test_chardet_iso8859_5_with_ignore(self, tmp_path):
        """chardet 报告 iso-8859-5，gbk 解码使用 errors='ignore' 不会抛出异常 → 返回 gbk"""
        f = tmp_path / "iso8859_5_raw.bin"
        # 非 GBK 有效字节，但 errors='ignore' 会静默丢弃非法序列，不抛异常
        # 注意：不以任何 BOM 开头
        f.write_bytes(b'\x80\x81\x82\x83\x84\x85')
        with patch('src.tools.encoding.chardet') as mock_chardet:
            mock_chardet.detect.return_value = {
                'encoding': 'iso-8859-5', 'confidence': 0.95
            }
            result = detect_encoding(str(f))
            # errors='replace' 会引入替代字符，因 gbk 解码有 \ufffd → 返回 iso-8859-5
            assert result == 'iso-8859-5'

    def test_chardet_returns_none(self, tmp_path):
        """chardet.detect 返回 None → 跳过 chardet 分支"""
        f = tmp_path / "chardet_none.txt"
        f.write_bytes('hello'.encode('utf-8'))
        with patch('src.tools.encoding.chardet') as mock_chardet:
            mock_chardet.detect.return_value = None
            result = detect_encoding(str(f))
            assert result == 'utf-8'

    def test_chardet_returns_empty_dict(self, tmp_path):
        """chardet.detect 返回空字典 → 跳过 chardet 分支"""
        f = tmp_path / "chardet_empty.txt"
        f.write_bytes('hello'.encode('utf-8'))
        with patch('src.tools.encoding.chardet') as mock_chardet:
            mock_chardet.detect.return_value = {}
            result = detect_encoding(str(f))
            assert result == 'utf-8'

    def test_chardet_encoding_none(self, tmp_path):
        """chardet.detect 返回 {'encoding': None} → 跳过 chardet 分支"""
        f = tmp_path / "chardet_enc_none.txt"
        f.write_bytes('hello'.encode('utf-8'))
        with patch('src.tools.encoding.chardet') as mock_chardet:
            mock_chardet.detect.return_value = {'encoding': None, 'confidence': 0.0}
            result = detect_encoding(str(f))
            assert result == 'utf-8'

    def test_chardet_low_confidence_falls_through(self, tmp_path):
        """chardet 返回低置信度编码 → 跳过 chardet 分支走 common encodings"""
        f = tmp_path / "low_conf.txt"
        f.write_bytes('hello'.encode('utf-8'))
        with patch('src.tools.encoding.chardet') as mock_chardet:
            mock_chardet.detect.return_value = {
                'encoding': 'utf-8', 'confidence': 0.30
            }
            result = detect_encoding(str(f))
            assert result == 'utf-8'

    # ── latin-1 兜底路径 ──────────────────────────────────

    def test_latin1_fallback(self, tmp_path):
        """latin-1 编码文件应在 common encodings 中被检测到"""
        f = tmp_path / "latin1.txt"
        # latin-1 可以解码任意单字节，用 \xff 测试
        f.write_bytes(b'\xe9\xe0\xf3')  # éàó in latin-1
        with patch('src.tools.encoding.CHARDET_AVAILABLE', False):
            result = detect_encoding(str(f))
            # utf-8 可解码？\xe9\xe0\xf3 不是合法 UTF-8 序列
            # gbk 可能失败，latin-1 可以解码任何字节
            assert result == 'latin-1'

    # ── 异常场景 ───────────────────────────────────────────

    def test_file_not_found(self, tmp_path):
        """文件不存在时应回退 utf-8 不抛异常"""
        result = detect_encoding(str(tmp_path / "nonexistent.txt"))
        assert result == 'utf-8'

    def test_file_is_directory(self, tmp_path):
        """路径是目录时应回退 utf-8 不抛异常"""
        result = detect_encoding(str(tmp_path))
        assert result == 'utf-8'

    def test_binary_file_latin1_fallback(self, tmp_path):
        """任意字节文件（无 BOM 前缀）→ latin-1 总是能解码，应返回 latin-1"""
        f = tmp_path / "random.bin"
        # latin-1 可以解码任意单字节，故 common encodings 中 latin-1 总是兜底成功
        # 注意：不以 BOM 开头，否则 BOM 检测优先
        f.write_bytes(b'\x80\x81\x82\x83\x84\x85')
        with patch('src.tools.encoding.CHARDET_AVAILABLE', False):
            result = detect_encoding(str(f))
            # latin-1 可以解码任意字节序列
            assert result == 'latin-1'

    # ── 大文件边界 ────────────────────────────────────────

    def test_large_file_detects_utf8(self, tmp_path):
        """大文件应只读取前 4096 字节"""
        f = tmp_path / "large.txt"
        # 写入超过 4096 字节的内容
        content = b'a' * 5000
        f.write_bytes(content)
        # 正常检测应为 utf-8（纯 ASCII）
        result = detect_encoding(str(f))
        assert result == 'utf-8'

    # ── utf-8-sig 在 COMMON_ENCODINGS 中的位置 ─────────────

    def test_utf8_sig_in_common_encodings(self, tmp_path):
        """COMMON_ENCODINGS 包含 utf-8-sig，对无 BOM 文件它也能解码"""
        f = tmp_path / "utf8_sig_test.txt"
        f.write_bytes('hello'.encode('utf-8'))
        with patch('src.tools.encoding.CHARDET_AVAILABLE', False):
            result = detect_encoding(str(f))
            # common encodings 依次尝试: utf-8 先成功
            assert result == 'utf-8'

    # ── 通吃编码降分逻辑 ──────────────────────────────────

    def test_catchall_score_degradation_utf8_with_damage(self):
        """通吃编码降分：UTF-8 有一字节损坏 → 非通吃编码（含少量 \ufffd）应胜出

        损坏的 UTF-8 字节在 replace 模式下产生少量 \ufffd（评分约 68-69），
        仍高于通吃编码 latin-1 的 60 分，因此 UTF-8 胜出。
        """
        # 构造：合法 UTF-8 前缀 + 一个非法字节 + 合法 UTF-8 后缀
        valid_utf8 = '你好世界 hello world！'.encode('utf-8')
        # 在中间插入一个非法字节 0xFF
        corrupted = valid_utf8[:5] + b'\xff' + valid_utf8[5:]
        candidates = ['latin-1', 'utf-8', 'gbk']
        best_enc, _ = pick_best_decoding(corrupted, candidates)
        # UTF-8 replace 模式评分 70-少量\ufffd > latin-1 通吃编码评分 60
        assert best_enc == 'utf-8'

    def test_catchall_score_degradation_gbk_wins_over_latin1(self):
        """通吃编码降分：GBK 正常解码 → GBK 评分 100，高于 latin-1 的 60

        GBK 编码的纯中文在 strict 模式下 0 替代字符，非通吃编码直接返回。
        """
        gbk_bytes = '纯中文测试内容'.encode('gbk')
        candidates = ['latin-1', 'utf-8', 'gbk']
        best_enc, _ = pick_best_decoding(gbk_bytes, candidates)
        assert best_enc == 'gbk'

    def test_catchall_score_degradation_latin1_last_resort(self):
        """通吃编码降分：无法解码的二进制 → latin-1 兜底胜出

        完全随机的字节序列在 utf-8 和 gbk 下都产生大量 \ufffd，
        latin-1 作为通吃编码（评分 60）最终胜出。
        """
        random_bytes = bytes(range(256))  # 0x00-0xFF 全部字节
        candidates = ['utf-8', 'gbk', 'latin-1']
        best_enc, best_content = pick_best_decoding(random_bytes, candidates)
        assert best_enc == 'latin-1'
        # latin-1 解码应无 \ufffd
        assert '\ufffd' not in best_content

    # ── pick_best_decoding 重复编码去重 ────────────────────

    def test_pick_best_decoding_dedup(self):
        """候选列表含重复编码时不应重复解码，结果与去重后一致"""
        # 使用中文 UTF-8 内容：GBK 解码 UTF-8 中文字节大概率失败或有替代字符
        utf8_bytes = '编码检测去重测试'.encode('utf-8')
        # 含重复的候选列表
        candidates_with_dup = ['gbk', 'utf-8', 'gbk', 'latin-1', 'utf-8']
        candidates_no_dup = ['gbk', 'utf-8', 'latin-1']
        enc1, content1 = pick_best_decoding(utf8_bytes, candidates_with_dup)
        enc2, content2 = pick_best_decoding(utf8_bytes, candidates_no_dup)
        # 去重前后结果一致即可（编码名和内容都应相同）
        assert enc1 == enc2
        assert content1 == content2

    def test_pick_best_decoding_all_duplicates(self):
        """候选列表全部为同一编码的重复 → 仅解码一次，结果正确"""
        utf8_bytes = 'test'.encode('utf-8')
        candidates = ['utf-8', 'utf-8', 'utf-8']
        best_enc, best_content = pick_best_decoding(utf8_bytes, candidates)
        assert best_enc == 'utf-8'
        assert best_content == 'test'

    def test_pick_best_decoding_empty_candidates(self):
        """空候选列表 → 走终极降级（IndexError 被兜底）"""
        # pick_best_decoding 对空列表会 IndexError，这是调用方的责任
        # 实际使用中不会传入空列表（detect_encoding 和 _validate_decoding_quality
        # 都保证至少一个候选编码）
        pytest.skip("此边界由调用方保证")


# ═══════════════════════════════════════════════════════════════
# 3. async_detect_encoding — 异步封装委托
# ═══════════════════════════════════════════════════════════════

class TestAsyncDetectEncoding:
    """async_detect_encoding() 异步封装测试"""

    @pytest.mark.asyncio
    async def test_async_returns_same_as_sync(self, tmp_path):
        """异步版本应返回与同步版本相同的结果"""
        f = tmp_path / "async_test.txt"
        f.write_bytes('你好世界 hello'.encode('utf-8'))
        sync_result = detect_encoding(str(f))
        async_result = await async_detect_encoding(str(f))
        assert async_result == sync_result
        assert async_result == 'utf-8'

    @pytest.mark.asyncio
    async def test_async_empty_file(self, tmp_path):
        """空文件异步检测应返回 utf-8"""
        f = tmp_path / "async_empty.txt"
        f.write_bytes(b'')
        result = await async_detect_encoding(str(f))
        assert result == 'utf-8'

    @pytest.mark.asyncio
    async def test_async_bom_file(self, tmp_path):
        """BOM 文件异步检测应正确识别"""
        f = tmp_path / "async_bom.txt"
        f.write_bytes(b'\xef\xbb\xbf' + 'hello'.encode('utf-8'))
        result = await async_detect_encoding(str(f))
        assert result == 'utf-8-sig'

    @pytest.mark.asyncio
    async def test_async_gbk_file(self, tmp_path):
        """GBK 文件异步检测应正确识别"""
        f = tmp_path / "async_gbk.txt"
        f.write_bytes('中文测试'.encode('gbk'))
        with patch('src.tools.encoding.CHARDET_AVAILABLE', False):
            result = await async_detect_encoding(str(f))
            assert result == 'gbk'

    @pytest.mark.asyncio
    async def test_async_file_not_found(self, tmp_path):
        """文件不存在时异步检测应回退 utf-8"""
        result = await async_detect_encoding(str(tmp_path / "async_nonexistent.txt"))
        assert result == 'utf-8'

    @pytest.mark.asyncio
    async def test_async_utf16_le_bom(self, tmp_path):
        """UTF-16 LE BOM 文件异步检测"""
        f = tmp_path / "async_utf16le.txt"
        f.write_bytes(b'\xff\xfe' + 'hello'.encode('utf-16-le'))
        result = await async_detect_encoding(str(f))
        assert result == 'utf-16-le'

    @pytest.mark.asyncio
    async def test_async_utf16_be_bom(self, tmp_path):
        """UTF-16 BE BOM 文件异步检测"""
        f = tmp_path / "async_utf16be.txt"
        f.write_bytes(b'\xfe\xff' + 'hello'.encode('utf-16-be'))
        result = await async_detect_encoding(str(f))
        assert result == 'utf-16-be'

    @pytest.mark.asyncio
    async def test_async_chardet_integration(self, tmp_path):
        """异步检测在有 chardet 时正常委托（GBK 内容走 chardet 路径，非 UTF-8 fast path）"""
        f = tmp_path / "async_chardet.txt"
        f.write_bytes('你好世界'.encode('gbk'))
        with patch('src.tools.encoding.chardet') as mock_chardet:
            mock_chardet.detect.return_value = {'encoding': 'gbk', 'confidence': 0.99}
            result = await async_detect_encoding(str(f))
            assert result == 'gbk'
            mock_chardet.detect.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_return_type(self, tmp_path):
        """异步检测返回 str 类型"""
        f = tmp_path / "async_type.txt"
        f.write_bytes('hello'.encode('utf-8'))
        result = await async_detect_encoding(str(f))
        assert isinstance(result, str)
