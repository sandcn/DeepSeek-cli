"""TUI 流式 Markdown 渲染性能与缺陷修复回归（第七批）。

覆盖本轮修复：

  - **容器块（告示 / ``<details>`` / fenced div）预览节流**：正文含块级标记时
    每帧子解析 + 完整 Markdown 渲染整段正文，成本随行数增长、累计 O(n²)
    （800 行列表实测 5.4ms/帧、8k 字符 8s）。改为按「正文总行数（含截断丢弃数，
    单调递增）」节流——短正文（< 48 行）逐帧实时，长正文按 ``行数//8`` 推进，
    活动行增长按字符节流（``_throttle_container_preview`` /
    ``_container_body_continuation``）。提交路径不受影响。
  - **列表项内块级容器预览节流**（``_list_block_preview`` 的
    ``_list_block_throttle_lookup``）：同一 O(n²) 形态（800 行列表内代码块
    实测 1.3s）。
  - **Mermaid 预览尾部滑窗节流**：源码被截断为「首行 + 尾部窗口」后不再是
    增长序列，前缀节流失效 → 每帧重排（800 边图实测 2s）。改用
    ``_throttle_preview_sliding``（按行数节流 + 首行锚定）。
  - **超长单行段落逐帧 O(全文) 扫描**：① 段落触发位置增量维护的「追加」判定
    由 ``content.startswith(prev)``（O(全文)）改为窗口相邻区域比对；②
    ``_preview_src_lines`` 的「窗口前是否还有历史行」用增量维护的
    ``_para_last_nl`` 替代 O(全文) 反向扫描；③ 解析器 ``feed`` 找换行与
    ``_prescan_refs`` 预扫描均从「本次新增文本起点」起（缓冲在 feed 开始时
    不含换行）——400k 单行段落 4.2s → ~1.6s，800k 22s → ~3.8s。

同时固化「修复后提交产出与一次性渲染一致」与「短块预览逐帧实时」不变。
"""
from __future__ import annotations

import time

import pytest

import src.renderer.ansi as _ansi
import src.renderer.ansi.mermaid as _mermaid_mod
from src.renderer.ansi import AnsiStreamRenderer, _container_body_continuation
from src.renderer._block_parser import RegexFreeBlockParser


# ══════════════════════════════════════════════════════════
# 辅助
# ══════════════════════════════════════════════════════════

def _one_shot(src: str, width: int = 100):
    r = AnsiStreamRenderer(width=width)
    r.write(src)
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _stream(src: str, chunk: int = 4, width: int = 100):
    r = AnsiStreamRenderer(width=width)
    for i in range(0, len(src), chunk):
        r.write(src[i:i + chunk])
        r.take_preview_lines()
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _count_stream_calls(obj, name, src, chunk=8, width=100):
    """流式渲染 ``src``，统计 ``obj.name`` 的调用次数。"""
    box = {"n": 0}
    orig = getattr(obj, name)

    def wrap(*a, **k):
        box["n"] += 1
        return orig(*a, **k)

    setattr(obj, name, wrap)
    try:
        r = AnsiStreamRenderer(width=width)
        for i in range(0, len(src), chunk):
            r.write(src[i:i + chunk])
            r.take_preview_lines()
        r.close()
        r.take_lines()
    finally:
        setattr(obj, name, orig)
    return box["n"], len(range(0, len(src), chunk))


ADM_LONG = "!!! note\n" + "\n".join("    - item %d" % i for i in range(800)) + "\n"
DETAILS_LONG = ("<details>\n<summary>s</summary>\n\n"
                + "\n".join("- item %d" % i for i in range(800))
                + "\n\n</details>\n")
DIV_LONG = "::: tip\n" + "\n".join("- item %d" % i for i in range(800)) + "\n:::\n"
LIST_BLOCK_LONG = ("- top\n  ```\n" + "".join("  code %d\n" % i for i in range(800))
                   + "  ```\n")
MERMAID_LONG = ("```mermaid\ngraph TD\n"
                + "".join("A%d-->B%d\n" % (i, i) for i in range(400)) + "```\n")


# ══════════════════════════════════════════════════════════
# 容器块预览节流（渲染次数与行数近线性，而非与帧数同阶）
# ══════════════════════════════════════════════════════════

@pytest.mark.parametrize("src", [ADM_LONG, DETAILS_LONG, DIV_LONG])
def test_container_preview_render_calls_bounded(src):
    """长容器预览的子解析/渲染次数远小于帧数（节流生效）。"""
    calls, frames = _count_stream_calls(
        _ansi.AnsiStreamRenderer, "_preview_sub_parse", src, chunk=8)
    assert frames > 1000, frames
    assert calls < frames // 3, (calls, frames)


def test_list_block_preview_render_calls_bounded():
    """列表项内块级容器预览的渲染次数远小于帧数。"""
    calls, frames = _count_stream_calls(
        _ansi.AnsiStreamRenderer, "_parse_list_block_preview",
        LIST_BLOCK_LONG, chunk=8)
    assert frames > 900, frames
    assert calls < frames // 3, (calls, frames)


def test_mermaid_preview_layout_calls_bounded():
    """Mermaid 预览的图形排版次数远小于帧数（尾部滑窗节流生效）。"""
    calls, frames = _count_stream_calls(
        _mermaid_mod, "render_mermaid_block", MERMAID_LONG, chunk=8)
    assert frames > 400, frames
    assert calls < frames // 2, (calls, frames)


# ══════════════════════════════════════════════════════════
# 提交完整性（节流只影响预览，不影响提交产出）
# ══════════════════════════════════════════════════════════

@pytest.mark.parametrize("src", [
    ADM_LONG, DETAILS_LONG, DIV_LONG, LIST_BLOCK_LONG, MERMAID_LONG,
    "!!! note\n" + "\n".join("    | a | b |\n    |---|---|"
                            + "\n    | %d | v |" % i for i in [1]) + "\n",
])
def test_long_block_commit_matches_one_shot(src):
    base = _one_shot(src)
    for chunk in (1, 7, 64, len(src)):
        assert _stream(src, chunk=chunk) == base, chunk


# ══════════════════════════════════════════════════════════
# 短容器预览逐帧实时（不因节流而滞后）
# ══════════════════════════════════════════════════════════

SHORT_CASES = {
    "admonition": "!!! note\n    short body line\n",
    "details": "<details>\n<summary>s</summary>\n\nshort body\n\n</details>\n",
    "fenced_div": "::: tip\nshort body\n:::\n",
}


@pytest.mark.parametrize("src", list(SHORT_CASES.values()))
def test_short_container_preview_realtime(src):
    """短容器（< 节流阈值）预览逐字符写入与一次性渲染该前缀一致。"""
    def keys_of(r):
        return [
            [(run.text, repr(run.style)) for run in ln.runs]
            for ln in list(r.lines) + list(r.take_preview_lines())
        ]

    for i in range(1, len(src) + 1):
        p = src[:i]
        fresh = AnsiStreamRenderer(width=50)
        fresh.write(p)
        want = keys_of(fresh)
        st = AnsiStreamRenderer(width=50)
        for ch in p:
            st.write(ch)
        assert keys_of(st) == want, (i, repr(p[-16:]))


def test_long_container_preview_tail_within_step():
    """长容器预览尾部滞后不超过一个刷新步长（节流契约，非内容丢失）。

    预览取自最近一次节流刷新；末行可能是**活动行的部分内容**（其序号是
    完整序号的截断前缀），故取预览中最大的完整条目序号作为「已显示到」的
    位置，断言其落后最新行不超过一个刷新步长（``总行数 // 8``）。
    """
    import re

    total = 400
    src = "!!! note\n" + "".join("    - item %d\n" % i for i in range(total))
    r = AnsiStreamRenderer(width=80)
    for i in range(0, len(src), 8):
        r.write(src[i:i + 8])
    prev = [ln.plain for ln in r.take_preview_lines()]
    assert prev[0] == "\u25a0 NOTE"
    nums = [int(m.group(1)) for x in prev
            for m in [re.search(r"item (\d+)", x)] if m]
    assert nums, prev[-3:]
    shown = max(nums)
    step = max(1, total // 8)
    assert total - 1 - step - 2 <= shown <= total - 1, (shown, step, total)


# ══════════════════════════════════════════════════════════
# _container_body_continuation 单元语义
# ══════════════════════════════════════════════════════════

def test_continuation_growing_prefix():
    """未截断（追加）：新正文以上一帧稳定行为前缀。"""
    prev = ["a", "b", "c"]
    assert _container_body_continuation(prev, ["a", "b", "c", "d"], 1)
    assert _container_body_continuation(prev, ["a", "b", "c"], 0)
    assert not _container_body_continuation(prev, ["x", "b", "c", "d"], 1)


def test_continuation_sliding_window():
    """截断滑窗：窗口右移 delta 行，重叠稳定行一致。"""
    prev = ["l0", "l1", "l2", "l3", "l4", "l5"]
    # 右移 2 行：新窗口 = l2..l5 + n0,n1（末行 l5 不计入稳定行比较）
    body = ["l2", "l3", "l4", "l5", "n0", "n1"]
    assert _container_body_continuation(prev, body, 2)
    assert not _container_body_continuation(prev, ["x2", "x3", "x4", "x5", "n0", "n1"], 2)


def test_continuation_rejects_new_container():
    """行数回退（新容器 / 重建）判定为非继续。"""
    assert not _container_body_continuation(["a", "b", "c"], ["z"], -2)


# ══════════════════════════════════════════════════════════
# 超长单行段落：每帧成本不随文本增长（复杂度上界）
# ══════════════════════════════════════════════════════════

def test_long_single_line_paragraph_frame_cost_bounded():
    """超长单行段落：后段单帧成本相对前段不呈线性增长（修复前 O(全文)/帧）。"""
    src = "x" * 60000
    r = AnsiStreamRenderer(width=100)
    n = len(src)
    worst_early = 0.0
    worst_late = 0.0
    for i in range(0, n, 8):
        t0 = time.perf_counter()
        r.write(src[i:i + 8])
        r.take_preview_lines()
        dt = time.perf_counter() - t0
        if i < n // 4:
            worst_early = max(worst_early, dt)
        elif i > n * 3 // 4:
            worst_late = max(worst_late, dt)
    r.close()
    assert worst_late < max(worst_early * 20, 0.5), (worst_early, worst_late)


def test_long_single_line_paragraph_output_not_windowed():
    """超长单行段落提交内容完整（预览窗口化不影响提交）。"""
    src = "y" * 9000 + "\n"
    assert _stream(src, chunk=64) == _one_shot(src)


# ══════════════════════════════════════════════════════════
# _preview_src_lines 的 last_nl 口径与反向扫描一致
# ══════════════════════════════════════════════════════════

@pytest.mark.parametrize("content", [
    "x" * 9000,
    "a" * 5000 + "\n" + "b" * 5000,
    "a\n" * 100,
    "\n".join("line %d" % i for i in range(50)) + "\n" + "z" * 9000,
    "short",
])
def test_preview_src_lines_last_nl_equivalent(content):
    r = AnsiStreamRenderer(width=80)
    want = r._preview_src_lines(content)
    last_nl = content.rfind("\n")
    got = r._preview_src_lines(content, last_nl=last_nl)
    assert got == want


# ══════════════════════════════════════════════════════════
# 解析器：分块 feed 与一次性 feed 的引用/脚注解析结果一致
# ══════════════════════════════════════════════════════════

def test_prescan_refs_start_scans_completed_first_line():
    """``_prescan_refs(start)`` 合并「此前累积的部分行」为完整行后再预扫描。"""
    p = RegexFreeBlockParser()
    p.feed("[id]: http://a.com")      # 部分行（无换行）
    p.feed(" \"T\"\n")                # 补完该行
    assert p._ctx.ref_map.get("id") == ("http://a.com", "T")

    p2 = RegexFreeBlockParser()
    p2.feed("[id]: http://a.com \"T\"\n")
    assert p2._ctx.ref_map.get("id") == ("http://a.com", "T")


def test_prescan_footnote_across_chunks():
    p = RegexFreeBlockParser()
    p.feed("see[^a]\n\n[^a]: note ")
    p.feed("body\n")
    assert p._ctx.fn_map.get("a") == "note body"


def test_chunked_feed_equivalent_refs_and_footnotes():
    doc = ("[r1]: http://e.com/1 \"T1\"\n\nsee [x][r1] and [^f1]\n\n"
           "[^f1]: footnote body\n")
    for chunk in (1, 3, 8, 64):
        p = RegexFreeBlockParser()
        for i in range(0, len(doc), chunk):
            p.feed(doc[i:i + chunk])
        p.flush()
        assert p._ctx.ref_map.get("r1") == ("http://e.com/1", "T1")
        assert p._ctx.fn_map.get("f1") == "footnote body"
