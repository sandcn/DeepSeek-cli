"""流式 Markdown 渲染性能优化（第十二批）—— 时间预算用例。

正确性 / 等价性回归见 ``test_stream_paragraph_throttle.py``（--cov 插桩下
本文件按 conftest 的时间预算规则跳过）。

本轮优化：

  1. ``_inline_links._scan_url_body`` —— 裸 URL 主体扫描 C 级正则化（2x+）；
  2. ``AnsiStreamRenderer._throttle_paragraph_whole`` —— 跨软换行未闭合标记
     导致「整段解析」的预览按源码增长节流，消除长段落每帧重解析的 O(n²)
     （260 行 × 每行裸 URL 实测 ~9.2s → ~0.8s）。

时间断言为宽松上界（不同机器 / 负载下留足余量），只用于拦截量级回归。
"""

from __future__ import annotations

import time

from src.renderer.ansi import AnsiStreamRenderer


def _stream_lines(md: str, chunk: int, width: int = 90) -> list[str]:
    r = AnsiStreamRenderer(width=width)
    for i in range(0, len(md), chunk):
        r.write(md[i:i + chunk])
        r.take_preview_lines()
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _batch_lines(md: str, width: int = 90) -> list[str]:
    r = AnsiStreamRenderer(width=width)
    r.write(md)
    r.close()
    return [ln.plain for ln in r.take_lines()]


_SOFT_WRAP_MD = ("**跨行粗体开始\n" + "\n".join(
    f"中间第 {i} 行普通内容 https://example.com/{i}" for i in range(260)
) + "\n结束**")


def test_soft_wrap_long_paragraph_stream_budget() -> None:
    """跨行未闭合标记 + 每行裸 URL 的 260 行段落流式渲染耗时上界。

    修复前每帧整段重解析（200 行预览窗口 × 每行 URL 扫描）累计 ~9.2s；
    本轮（URL 扫描正则化 + 整段解析节流）降至 ~0.8s。
    """
    _stream_lines(_SOFT_WRAP_MD, 64)  # 预热（导入 / 缓存）
    t0 = time.perf_counter()
    lines = _stream_lines(_SOFT_WRAP_MD, 11)
    elapsed = time.perf_counter() - t0
    assert lines == _batch_lines(_SOFT_WRAP_MD)
    assert elapsed < 3.0, f"soft-wrap 长段落流式耗时 {elapsed:.3f}s"


def test_soft_wrap_tail_frame_cost_not_growing() -> None:
    """节流后段落尾部平均帧成本不随段落长度增长（消除 O(n²) 帧成本）。

    用**平均**帧成本判定（单帧最大值受 GC / 系统调度抖动影响，不能稳定
    反映节流效果）；尾部区间平均成本应不高于前段的数倍。
    """
    md = _SOFT_WRAP_MD
    r = AnsiStreamRenderer(width=90)
    early: list[float] = []
    late: list[float] = []
    n = len(md)
    for i in range(0, n, 11):
        t0 = time.perf_counter()
        r.write(md[i:i + 11])
        r.take_preview_lines()
        dt = time.perf_counter() - t0
        if i < n // 4:
            early.append(dt)
        elif i > n * 3 // 4:
            late.append(dt)
    r.close()
    avg_early = sum(early) / max(1, len(early))
    avg_late = sum(late) / max(1, len(late))
    # 尾部平均帧成本不应显著高于前段（允许 6x 抖动余量 + 固定下限）
    assert avg_late < max(avg_early * 6, 0.01), (avg_early, avg_late)


def test_url_dense_line_stream_budget() -> None:
    """URL 密集单行（活动行窗口逐帧行内解析）流式渲染耗时上界。"""
    text = " ".join(
        f"见 https://example.com/path/{i}/x?q={i} 说明" for i in range(120))
    _stream_lines(text, 64, width=100)  # 预热
    t0 = time.perf_counter()
    _stream_lines(text, 8, width=100)
    assert time.perf_counter() - t0 < 2.0
