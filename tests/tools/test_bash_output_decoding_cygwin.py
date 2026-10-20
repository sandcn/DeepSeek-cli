"""bash 输出解码：Cygwin / MSYS2 乱码回归测试（2026-10-20）。

背景（用户报障「bash.py 在当前环境下执行 cygwin，会有机率乱码」）：
Cygwin / MSYS2 的 ``os.name`` 是 ``posix``、``sys.platform`` 是
``cygwin``/``msys``，但内核是 Windows——**Cygwin 程序按 ``locale`` 输出
UTF-8，原生 Windows 程序（``ipconfig`` / ``driverquery`` 等按 ANSI 代码页
输出的工具）输出 GBK**，同一条命令流里两种编码都可能出现。

修复前的消歧规则把「非 ASCII 字符全部落在 ``U+0370–U+07FF`` 的合法 UTF-8
行」一律改判成本地 CJK 代码页，导致：

  - 真 UTF-8 的希腊字母 / 西里尔文被改成中文（``α`` → ``伪``、
    ``Привет`` → ``袩褉懈胁械褌``）——本环境实测复现；
  - 落在 ``U+0080–U+036F`` 的 GBK 误读行（``ʲô`` / ``ɸѡ``）又原样留下
    （``cmd /c driverquery`` 的「筛选」列实测显示成 ``ɸѡ``）。

修复：双合法行改按「误读特征（IPA / 修饰符 / 组合符号 / 古体希腊 /
科普特 / 古体西里尔等正常输出不会出现的码位，或横跨多个书写系统）+
环境主编码」判定，见 ``src/tools/_process_output.py`` 的
``_ambiguous_local_decode`` / ``prefer_local_output``。

覆盖：
  - ``prefer_local_output``：UTF-8 locale → False，C / 非 UTF-8 locale →
    True（Cygwin / POSIX 跟随 locale）；
  - 双合法 GBK 行在 ``prefer_local=False``（Cygwin 主编码 UTF-8）下仍还原
    成中文；
  - 真 UTF-8 希腊 / 西里尔 / 带重音拉丁 / 希伯来 / 阿拉伯 / 亚美尼亚文本
    在 ``prefer_local=False`` 下保持原样；
  - ``prefer_local=True``（原生 Windows 主编码本机代码页）保持既有行为，
    且不误伤 ``café`` / ``ÆØÅ`` 等拉丁文本；
  - ``OutputLineDecoder`` 注入 ``prefer_local`` 后跨块解码一致；
  - 端到端：Cygwin / POSIX 上 ``echo α β γ`` 不再变成中文。
"""

from __future__ import annotations

import locale
import os

import pytest

from src.tools._process_output import (
    OutputLineDecoder,
    decode_output,
    prefer_local_output,
)

#: Cygwin / MSYS2 / 其它 POSIX（非原生 Windows）
_IS_POSIX_LIKE = os.name != "nt"


# ── 1. 环境主编码判定 ────────────────────────────────

def test_prefer_local_output_follows_locale_on_posix(monkeypatch):
    """POSIX / Cygwin：主编码跟随 ``locale``（UTF-8 locale → 不偏向本地代码页）。"""
    if not _IS_POSIX_LIKE:
        pytest.skip("仅 POSIX / Cygwin 走 locale 判定")
    monkeypatch.setattr(locale, "getpreferredencoding", lambda *a, **k: "UTF-8")
    assert prefer_local_output() is False

    monkeypatch.setattr(locale, "getpreferredencoding", lambda *a, **k: "ANSI_X3.4-1968")
    assert prefer_local_output() is True

    monkeypatch.setattr(locale, "getpreferredencoding", lambda *a, **k: "gbk")
    assert prefer_local_output() is True


def test_output_line_decoder_records_prefer_local():
    """解码器把 ``prefer_local`` 固化在实例上（每条输出流采样一次）。"""
    assert OutputLineDecoder(prefer_local=True).prefer_local is True
    assert OutputLineDecoder(prefer_local=False).prefer_local is False
    # 未显式注入时按环境主编码判定
    assert OutputLineDecoder().prefer_local is prefer_local_output()


# ── 2. 双合法 GBK 行（Cygwin 主编码 UTF-8）仍还原为中文 ────

@pytest.mark.parametrize("chinese", ["ʲô", "Ϊʲô", "ɸѡ", "ϵͳ", "ֹͣ"])
def test_gbk_double_valid_line_restored_without_env_prior(chinese):
    """GBK 双合法行（UTF-8 误读成 ``ʲô`` / ``ɸѡ`` / ``ϵͳ`` 等）按误读特征还原。

    参数 ``chinese`` 是「UTF-8 误读形态」，用例内还原成对应 GBK 字节：
    ``ʲô`` → 什么、``Ϊʲô`` → 为什么、``ɸѡ`` → 筛选、``ϵͳ`` → 系统、
    ``ֹͣ`` → 停止。这些行不含 3/4 字节序列，也没有别的字节级证据——
    修复前在 Cygwin 上会被当成真 UTF-8 原样显示（乱码）。
    """
    expected = {
        "ʲô": "什么",
        "Ϊʲô": "为什么",
        "ɸѡ": "筛选",
        "ϵͳ": "系统",
        "ֹͣ": "停止",
    }[chinese]
    raw = expected.encode("gbk")
    # 前提：这些 GBK 字节确实是合法 UTF-8（否则本用例没有覆盖消歧逻辑）
    assert raw.decode("utf-8") == chinese
    assert decode_output(raw, ("utf-8", "cp936"), prefer_local=False) == expected


def test_gbk_line_with_invalid_utf8_bytes_still_decoded():
    """普通 GBK 行（UTF-8 严格解码失败）走本机代码页回退，与主编码无关。"""
    raw = "系统找不到指定的路径。".encode("gbk")
    for prefer_local in (False, True):
        assert decode_output(raw, ("utf-8", "cp936"), prefer_local=prefer_local) == (
            "系统找不到指定的路径。")


# ── 3. 真 UTF-8 文本不再被改判成本地代码页 ────────────

@pytest.mark.parametrize("text", [
    "α β γ π μ λ Ω",          # 希腊字母（Cygwin 实测被改成「伪 尾 纬…」）
    "α = 0.5 °C",
    "Привет мир",             # 西里尔文（实测被改成「袩褉懈胁械褌 屑懈褉」）
    "Γειά σου",
    "café naïve résumé",      # 带重音拉丁文本
    "Müller Straße",
    "ÆØÅ",
    "Copyright © 2024",
    "温度 25°C",
    "Ω=1.5V",
    "约 2μs 延迟",
    "中文测试",
    "שלום עולם",
    "مرحبا بالعالم",
    "Բարեւ աշխարհ",
])
def test_utf8_text_not_switched_to_local_codepage(text):
    """Cygwin 主编码为 UTF-8 时，真 UTF-8 文本逐字保持（不再被改成中文乱码）。"""
    raw = text.encode("utf-8")
    assert decode_output(raw, ("utf-8", "cp936"), prefer_local=False) == text


# ── 4. 原生 Windows 主编码（本机代码页）：既有行为不变 ────

def test_local_prior_keeps_windows_behavior_for_double_valid_lines():
    """``prefer_local=True`` 时双合法 GBK 行仍判为本机代码页（Windows 既有行为）。"""
    assert decode_output("系统".encode("gbk"), ("utf-8", "cp936"),
                         prefer_local=True) == "系统"
    assert decode_output("筛选".encode("gbk"), ("utf-8", "cp936"),
                         prefer_local=True) == "筛选"
    assert decode_output("什么".encode("gbk"), ("utf-8", "cp936"),
                         prefer_local=True) == "什么"


@pytest.mark.parametrize("text", ["café", "Zürich", "ÆØÅ", "25 °C", "2μs", "中文测试"])
def test_local_prior_never_touches_latin_or_cjk_text(text):
    """即使主编码是本机代码页，纯拉丁（含重音）/ CJK 文本也保持 UTF-8。"""
    raw = text.encode("utf-8")
    assert decode_output(raw, ("utf-8", "cp936"), prefer_local=True) == text


# ── 5. 流式解码：跨块切分不影响判定 ──────────────────

def test_line_decoder_cross_chunk_preserves_utf8_greek():
    """真 UTF-8 希腊文跨块到达（逐字节切分）时不被改判。"""
    data = "α β γ\nПривет\n".encode("utf-8")
    decoder = OutputLineDecoder(("utf-8", "cp936"), prefer_local=False)
    lines = []
    for index in range(len(data)):
        lines.extend(decoder.feed(data[index:index + 1]))
    lines.extend(decoder.flush())
    assert lines == ["α β γ\n", "Привет\n"]


def test_line_decoder_cross_chunk_restores_gbk_double_valid_line():
    """GBK 双合法行跨块到达时仍按误读特征还原（每条流采样主编码一次）。"""
    data = "什么\n筛选\n".encode("gbk")   # 误读形态即 ʲô / ɸѡ
    decoder = OutputLineDecoder(("utf-8", "cp936"), prefer_local=False)
    lines = []
    for index in range(0, len(data), 3):
        lines.extend(decoder.feed(data[index:index + 3]))
    lines.extend(decoder.flush())
    assert lines == ["什么\n", "筛选\n"]


# ── 6. 端到端：bash 工具在本环境不再把希腊文改成中文 ────

@pytest.mark.skipif(not _IS_POSIX_LIKE, reason="原生 Windows 的 echo 走控制台代码页")
async def test_bash_greek_output_stays_greek():
    """``echo α β γ`` 的工具结果是希腊字母（Cygwin 实测曾被改成「伪 尾 纬」）。"""
    import json

    from src.tools.bash import BashFunc

    result = json.loads(await BashFunc(command="echo α β γ π").execute())
    assert result["returncode"] == 0
    assert result["stdout"] == "α β γ π"


@pytest.mark.skipif(not _IS_POSIX_LIKE, reason="原生 Windows 的 echo 走控制台代码页")
async def test_bash_cyrillic_output_stays_cyrillic():
    """``echo Привет`` 的工具结果是西里尔文（修复前被改成「袩褉懈胁械褌」）。"""
    import json

    from src.tools.bash import BashFunc

    result = json.loads(await BashFunc(command="echo Привет мир").execute())
    assert result["returncode"] == 0
    assert result["stdout"] == "Привет мир"
