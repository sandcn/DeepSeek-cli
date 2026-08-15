"""InkSession prefill 立即渲染测试（2026-08-15，editmsg 显示延迟修复）。

修复背景：/editmsg /deitmsg /retry 等命令的 prefill 注入后，输入区应立即显示
prefill 供用户编辑。修复前 prefill 仅经 ``_request_render`` 置位渲染标志：
  - ``_should_render`` 的 interval 检查可能因 EditmsgPlugin flush 刚渲染
    （``_last_bottom_redraw`` 刚更新）而不满足 → 延迟到下一 10Hz 拍；
  - 渲染线程 busy（处理 clear/display/write 命令）期间注入的 force 请求
    会被 ``_drain_queue`` 后的 ``_cmd_event.clear()`` 丢失 → 下一轮循环
    节流等待不立即返回。
  双重延迟下输入区 0.1~0.5s 空白，用户感知「编辑后没立即显示，要再按一次
  回车才刷新显示」。

修复（session 层，方案 J）：
  1. ``_should_render``：force（_bottom_redraw_requested）跳过 interval 节流；
  2. ``_render`` 循环：force 未消费时保持 ``_cmd_event`` 唤醒 + 节流等待中
     force 提前退出（下一轮循环立即处理）。

本测试锁定：_should_render 的 force 语义（跳过 interval / 消费清除 / 无 force
仍受节流），以及渲染线程下 prefill 注入后及时渲染（<300ms，修复前 200ms+）。
"""

from __future__ import annotations

import io
import threading
import time

from src.tui.app.app import build_app_element
from src.tui.app.apply import apply_cmd
from src.tui.app.model import AppModel
from src.tui._config import TuiConfig
from src.tui._screen import TerminalWidthCache
from src.tui.ink.session import InkSession


def _make_session() -> tuple[InkSession, AppModel]:
    """构造 InkSession（MockStream + AppModel），返回 (session, model)。"""
    cache = TerminalWidthCache.get_default()
    cache._width = 80
    cache._height = 24
    model = AppModel()
    session = InkSession(
        model=model,
        apply_cmd=apply_cmd,
        build_tree=build_app_element,
        config=TuiConfig.defaults(),
        stream=io.StringIO(),
    )
    session.set_line_tracker(None)
    return session, model


# ── _should_render force 语义 ─────────────────────────────

def test_should_render_force_skips_interval():
    """force（_bottom_redraw_requested）跳过 interval 节流——prefill 注入后
    即使 EditmsgPlugin flush 刚渲染（interval 未满）也立即渲染。"""
    session, _ = _make_session()
    # 模拟刚渲染过（interval 未满）→ 修复前 now - last < 0.1 返回 False
    session._last_bottom_redraw = time.monotonic()
    session._bottom_redraw_requested.set()
    session._dirty = True

    assert session._should_render(changed=False) is True


def test_should_render_no_force_respects_interval():
    """无 force（仅 dirty）仍受 interval 节流——高频命令 10Hz 批处理不回归。"""
    session, _ = _make_session()
    session._last_bottom_redraw = time.monotonic()  # 刚渲染过
    session._dirty = True
    # 不设置 _bottom_redraw_requested（高频命令路径只置 dirty + cmd_event）

    assert session._should_render(changed=False) is False


def test_should_render_force_consumes_flag():
    """_should_render 消费 force 后清除 _bottom_redraw_requested（防重复 force）。"""
    session, _ = _make_session()
    session._last_bottom_redraw = time.monotonic() - 1.0  # interval 已满
    session._bottom_redraw_requested.set()
    session._dirty = True

    assert session._should_render(changed=False) is True
    assert not session._bottom_redraw_requested.is_set()


def test_should_render_force_idle_still_skips():
    """force 即使 idle（无 dirty）也渲染——force 表示用户可感知 UI 更新。"""
    session, _ = _make_session()
    session._last_bottom_redraw = time.monotonic()
    session._bottom_redraw_requested.set()
    session._dirty = False

    assert session._should_render(changed=False) is True


# ── 渲染线程下 prefill 注入及时渲染（端到端） ──────────────

def test_prefill_injection_renders_promptly_with_render_thread():
    """渲染线程运行中 prefill 注入后及时渲染（<300ms，修复前 200ms+ 延迟）。

    模拟 /editmsg 编辑完成后的关键时序：
      1. EditmsgPlugin flush 刚渲染（_last_bottom_redraw 刚更新，interval 未满）；
      2. prefill 注入（update_input → _request_render 置 force）；
      3. 渲染线程应经 force 语义立即渲染（不等下一 10Hz 拍）。
    """
    session, model = _make_session()
    rendered = []
    orig = session._render_frame

    def spy():
        rendered.append(model.input_text)
        return orig()

    session._render_frame = spy
    session.start()
    time.sleep(0.15)  # 渲染线程稳定

    try:
        # 模拟 EditmsgPlugin 完成时 flush 刚渲染（interval 未满）
        session._last_bottom_redraw = time.monotonic()
        session._dirty = False
        session._bottom_redraw_requested.clear()

        # prefill 注入：update_input 置 input_text + _request_render（force）
        start = time.monotonic()
        model.input_text = "第一条用户消息（编辑后）"
        session._request_render()

        # 等待渲染（轮询最多 1s）
        deadline = start + 1.0
        while time.monotonic() < deadline:
            if rendered and rendered[-1] == "第一条用户消息（编辑后）":
                break
            time.sleep(0.01)

        elapsed = time.monotonic() - start
        assert rendered, "prefill 注入后应发生渲染"
        assert rendered[-1] == "第一条用户消息（编辑后）", "渲染帧应包含 prefill 内容"
        # 修复前延迟 200ms+（interval 未满被拦截）；修复后 <300ms
        assert elapsed < 0.3, f"prefill 渲染延迟过大: {elapsed:.3f}s"
    finally:
        session.stop()
