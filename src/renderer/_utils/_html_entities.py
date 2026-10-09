"""HTML 实体映射与解码。"""

from __future__ import annotations

try:  # 标准库完整 HTML5 命名实体表（全量支持；导入失败时仅用内置常用表）
    from html.entities import html5 as _HTML5_ENTITIES
except Exception:  # pragma: no cover - 标准库缺失的极端环境
    _HTML5_ENTITIES = None


# HTML 实体映射表（常用命名实体：拉丁补充 / 标点 / 希腊字母 / 数学 / 箭头）
_HTML_ENTITIES: dict[str, str] = {
    # ── 基础 ──
    '&amp;': '&', '&lt;': '<', '&gt;': '>',
    '&quot;': '"', '&apos;': "'", '&nbsp;': '\u00A0',
    # ── 货币 / 法律 ──
    '&cent;': '\u00A2', '&pound;': '\u00A3', '&yen;': '\u00A5',
    '&euro;': '\u20AC', '&curren;': '\u00A4', '&copy;': '\u00A9',
    '&reg;': '\u00AE', '&trade;': '\u2122', '&sect;': '\u00A7',
    '&para;': '\u00B6', '&micro;': '\u00B5', '&deg;': '\u00B0',
    # ── 标点 / 引号 / 破折号 ──
    '&mdash;': '\u2014', '&ndash;': '\u2013', '&hellip;': '\u2026',
    '&laquo;': '\u00AB', '&raquo;': '\u00BB', '&lsaquo;': '\u2039',
    '&rsaquo;': '\u203A', '&lsquo;': '\u2018', '&rsquo;': '\u2019',
    '&ldquo;': '\u201C', '&rdquo;': '\u201D', '&sbquo;': '\u201A',
    '&bdquo;': '\u201E', '&bull;': '\u2022', '&middot;': '\u00B7',
    '&dagger;': '\u2020', '&Dagger;': '\u2021', '&permil;': '\u2030',
    '&prime;': '\u2032', '&Prime;': '\u2033', '&oline;': '\u203E',
    '&frasl;': '\u2044', '&shy;': '\u00AD', '&cedil;': '\u00B8',
    '&uml;': '\u00A8', '&acute;': '\u00B4', '&macr;': '\u00AF',
    '&circ;': '\u02C6', '&tilde;': '\u02DC',
    # ── 空白 ──
    '&ensp;': '\u2002', '&emsp;': '\u2003', '&thinsp;': '\u2009',
    '&zwnj;': '\u200C', '&zwj;': '\u200D', '&lrm;': '\u200E',
    '&rlm;': '\u200F',
    # ── 分数 / 上下标 / 符号 ──
    '&frac14;': '\u00BC', '&frac12;': '\u00BD', '&frac34;': '\u00BE',
    '&sup1;': '\u00B9', '&sup2;': '\u00B2', '&sup3;': '\u00B3',
    '&iexcl;': '\u00A1', '&iquest;': '\u00BF', '&ordf;': '\u00AA',
    '&ordm;': '\u00BA', '&not;': '\u00AC', '&brvbar;': '\u00A6',
    '&plusmn;': '\u00B1', '&times;': '\u00D7', '&divide;': '\u00F7',
    '&minus;': '\u2212', '&lowast;': '\u2217',
    # ── 拉丁扩展 ──
    '&OElig;': '\u0152', '&oelig;': '\u0153', '&Scaron;': '\u0160',
    '&scaron;': '\u0161', '&Yuml;': '\u0178', '&fnof;': '\u0192',
    # ── 希腊字母 ──
    '&Alpha;': '\u0391', '&Beta;': '\u0392', '&Gamma;': '\u0393',
    '&Delta;': '\u0394', '&Epsilon;': '\u0395', '&Zeta;': '\u0396',
    '&Eta;': '\u0397', '&Theta;': '\u0398', '&Iota;': '\u0399',
    '&Kappa;': '\u039A', '&Lambda;': '\u039B', '&Mu;': '\u039C',
    '&Nu;': '\u039D', '&Xi;': '\u039E', '&Omicron;': '\u039F',
    '&Pi;': '\u03A0', '&Rho;': '\u03A1', '&Sigma;': '\u03A3',
    '&Tau;': '\u03A4', '&Upsilon;': '\u03A5', '&Phi;': '\u03A6',
    '&Chi;': '\u03A7', '&Psi;': '\u03A8', '&Omega;': '\u03A9',
    '&alpha;': '\u03B1', '&beta;': '\u03B2', '&gamma;': '\u03B3',
    '&delta;': '\u03B4', '&epsilon;': '\u03B5', '&zeta;': '\u03B6',
    '&eta;': '\u03B7', '&theta;': '\u03B8', '&iota;': '\u03B9',
    '&kappa;': '\u03BA', '&lambda;': '\u03BB', '&mu;': '\u03BC',
    '&nu;': '\u03BD', '&xi;': '\u03BE', '&omicron;': '\u03BF',
    '&pi;': '\u03C0', '&rho;': '\u03C1', '&sigmaf;': '\u03C2',
    '&sigma;': '\u03C3', '&tau;': '\u03C4', '&upsilon;': '\u03C5',
    '&phi;': '\u03C6', '&chi;': '\u03C7', '&psi;': '\u03C8',
    '&omega;': '\u03C9', '&thetasym;': '\u03D1', '&upsih;': '\u03D2',
    '&piv;': '\u03D6',
    # ── 数学 ──
    '&forall;': '\u2200', '&part;': '\u2202', '&exist;': '\u2203',
    '&empty;': '\u2205', '&nabla;': '\u2207', '&isin;': '\u2208',
    '&notin;': '\u2209', '&ni;': '\u220B', '&prod;': '\u220F',
    '&sum;': '\u2211', '&radic;': '\u221A', '&prop;': '\u221D',
    '&infin;': '\u221E', '&ang;': '\u2220', '&and;': '\u2227',
    '&or;': '\u2228', '&cap;': '\u2229', '&cup;': '\u222A',
    '&int;': '\u222B', '&there4;': '\u2234', '&sim;': '\u223C',
    '&cong;': '\u2245', '&asymp;': '\u2248', '&ne;': '\u2260',
    '&equiv;': '\u2261', '&le;': '\u2264', '&ge;': '\u2265',
    '&sub;': '\u2282', '&sup;': '\u2283', '&nsub;': '\u2284',
    '&sube;': '\u2286', '&supe;': '\u2287', '&oplus;': '\u2295',
    '&otimes;': '\u2297', '&perp;': '\u22A5', '&sdot;': '\u22C5',
    '&lceil;': '\u2308', '&rceil;': '\u2309', '&lfloor;': '\u230A',
    '&rfloor;': '\u230B', '&lang;': '\u2329', '&rang;': '\u232A',
    '&alefsym;': '\u2135',
    # ── 箭头 ──
    '&larr;': '\u2190', '&uarr;': '\u2191', '&rarr;': '\u2192',
    '&darr;': '\u2193', '&harr;': '\u2194', '&crarr;': '\u21B5',
    '&lArr;': '\u21D0', '&uArr;': '\u21D1', '&rArr;': '\u21D2',
    '&dArr;': '\u21D3', '&hArr;': '\u21D4',
    # ── 卡片花色 / 几何 ──
    '&hearts;': '\u2665', '&diams;': '\u2666', '&clubs;': '\u2663',
    '&spades;': '\u2660', '&loz;': '\u25CA',
}


def _lookup_named_entity(entity: str) -> str | None:
    """命名实体查找：内置常用表 → 标准库完整 HTML5 表（含无分号形式）。

    ★ 全量实体支持：标准库 ``html.entities.html5`` 含 2231 个 HTML5 命名
    实体（``&AElig;`` / ``&Dcaron;`` / ``&NotNestedGreaterGreater;`` …）——
    修复前仅内置约 150 个常用实体，其余原样输出（``&AElig;`` 显示为字面
    文本）。内置表保留为快速路径（高频实体免字典切换）。
    """
    val = _HTML_ENTITIES.get(entity)
    if val is not None:
        return val
    if _HTML5_ENTITIES is not None:
        # 标准库表的键不含前导 ``&``（``'AElig;'`` / ``'amp;'``）
        name = entity[1:] if entity.startswith('&') else entity
        val = _HTML5_ENTITIES.get(name)
        if val is not None:
            return val
        if name.endswith(';'):
            # HTML5 允许部分实体省略分号（``&amp`` / ``&copy``）
            val = _HTML5_ENTITIES.get(name[:-1])
            if val is not None:
                return val
        else:
            val = _HTML5_ENTITIES.get(name + ';')
            if val is not None:
                return val
    return None


def decode_html_entities(text: str) -> str:
    """HTML 实体解码为 Unicode。

    支持命名实体（如 &amp;）和数字实体（如 &#169; / &#x00A9;）。

    ★ 无效码点（``&#0;`` / 代理区 / 超出 U+10FFFF）替换为 U+FFFD——修复前
    ``&#0;`` 产出真正的 NUL 字符（``\\x00``）混入正文，宽度测量与终端输出
    均异常。

    Args:
        text: 含 HTML 实体的文本

    Returns:
        解码后的文本
    """
    if '&' not in text:
        return text

    result: list[str] = []
    i = 0
    while i < len(text):
        amp = text.find('&', i)
        if amp == -1:
            result.append(text[i:])
            break
        result.append(text[i:amp])
        semicolon = text.find(';', amp)
        if semicolon == -1:
            result.append(text[amp:])
            break

        entity = text[amp:semicolon + 1]
        named = _lookup_named_entity(entity)
        if named is not None:
            result.append(named)
            i = semicolon + 1
            continue

        if entity.startswith('&#') and len(entity) > 3:
            try:
                num_str = entity[2:-1]
                cp: int = int(num_str[1:], 16) if num_str.startswith(('x', 'X')) else int(num_str)
                # ★ 无效码点（0 / 代理区 / 超出 Unicode 范围）→ U+FFFD
                #   （CommonMark；修复前 ``&#0;`` 产出 NUL 字符混入正文）
                if cp == 0 or cp > 0x10FFFF or 0xD800 <= cp <= 0xDFFF:
                    result.append('\ufffd')
                else:
                    result.append(chr(cp))
                i = semicolon + 1
                continue
            except (ValueError, OverflowError):
                pass

        result.append(entity)
        i = semicolon + 1

    return ''.join(result)
