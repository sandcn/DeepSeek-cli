"""TUI 性能改进回归（2026-10-07，性能专项）。

覆盖本轮针对 TUI 性能的改动：

方向 4（启动与初始化）
  1. ``src.core.__init__`` / ``src.core.internal.__init__`` /
     ``src.api.escape_monitor.__init__`` 改为 PEP 562 惰性导出——导入轻量子
     模块不再连锁加载 agent/tools/commands 重链（冷启动导入时间实测
     ~930ms → ~530ms）；
  2. 惰性导出不改变对外 API（``from src.core import AgentBuilder`` 等仍可用）；
  3. 消除隐性导入顺序契约：``import src.core.sandbox_manager`` /
     ``src.core.base_agent`` / ``src.core.subagent`` 任意顺序导入均不再触发
     ``partially initialized module`` 循环导入错误。

方向 1（流式 Markdown 渲染/预览）
  4. ``ansi.table._wrap_runs`` 整段宽度快路径与逐字符通用路径产出完全等价；
  5. ``TablePreviewCache._reuse_data`` 纯追加快路径等价（列宽变化重建、
     头部滑窗、前缀分歧各形态输出与全量重渲染一致）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_isolated(code: str) -> subprocess.CompletedProcess:
    """在独立解释器中执行代码（隔离 sys.modules，验证导入副作用）。"""
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
    )


# ═══════════════════════════════════════════════════════════
# 方向 4：启动懒导出
# ═══════════════════════════════════════════════════════════


class TestLazyExports:
    def test_core_submodule_import_skips_agent_chain(self):
        """导入 ``src.core.tokens`` 不连锁加载 agent_builder / tools。"""
        proc = _run_isolated(
            "import sys; import src.core.tokens; "
            "print('src.core.agent_builder' in sys.modules, "
            "'src.tools' in sys.modules)"
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "False False"

    def test_core_agent_builder_still_accessible(self):
        """``from src.core import AgentBuilder`` 惰性解析仍可用。"""
        proc = _run_isolated(
            "from src.core import AgentBuilder; print(AgentBuilder.__name__)"
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "AgentBuilder"

    def test_core_dir_and_all(self):
        code = (
            "import src.core; "
            "print('AgentBuilder' in dir(src.core), src.core.__all__)"
        )
        proc = _run_isolated(code)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "True ['AgentBuilder']"

    def test_core_unknown_attribute_raises(self):
        proc = _run_isolated(
            "import src.core\n"
            "try:\n"
            "    src.core.NotAThing\n"
            "except AttributeError as e:\n"
            "    print('AttributeError')\n"
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "AttributeError"

    def test_internal_shared_import_skips_agent_and_commands(self):
        """导入 ``internal.shared`` 不连锁加载 internal.agent / commands。"""
        proc = _run_isolated(
            "import sys; "
            "from src.core.internal.shared._sandbox_history import _FileHistory; "
            "print('src.core.internal.agent' in sys.modules, "
            "'src.core.internal.commands' in sys.modules)"
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "False False"

    def test_internal_lazy_exports_available(self):
        """``internal`` 惰性导出名全部可解析。"""
        code = (
            "from src.core.internal import ("
            "add_message, CoreEventBus, SessionState, "
            "ToolCallbackChain, SubAgentSpawner, CaptureManager, "
            "register_command, handle_command, get_dynamic_help_text, "
            "MessageStatsCache, FileSnapshot); "
            "print('ok')"
        )
        proc = _run_isolated(code)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "ok"

    def test_internal_all_names_resolvable(self):
        """``__all__`` 中每个名字均可惰性解析（无遗漏映射）。"""
        code = (
            "import src.core.internal as m\n"
            "missing = []\n"
            "for n in m.__all__:\n"
            "    try:\n"
            "        getattr(m, n)\n"
            "    except Exception as e:\n"
            "        missing.append(n)\n"
            "print(missing)\n"
        )
        proc = _run_isolated(code)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "[]"

    def test_escape_monitor_lazy_export_available(self):
        proc = _run_isolated(
            "from src.api.escape_monitor import ("
            "EscapeMonitor, get_active_monitor, stop_active_monitor, "
            "INPUT_HISTORY_FILE); print(EscapeMonitor.__name__)"
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "EscapeMonitor"

    def test_escape_monitor_history_import_skips_monitor(self):
        """导入 ``escape_monitor.history`` 不连锁加载 ``_monitor``。"""
        proc = _run_isolated(
            "import sys; "
            "from src.api.escape_monitor.history import _read_history_file; "
            "print('src.api.escape_monitor._monitor' in sys.modules)"
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "False"

    def test_tui_input_import_chain_light(self):
        """TUI 输入路径导入不再预先加载完整 agent/tools 栈。"""
        proc = _run_isolated(
            "import sys; import src.tui._input; "
            "print('src.core.agent' in sys.modules, 'src.tools' in sys.modules)"
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "False False"

    def test_renderer_ansi_import_skips_rich_chain(self):
        """导入 ``src.renderer.ansi``（TUI 内容路径）不加载 Rich 渲染链。"""
        proc = _run_isolated(
            "import sys; import src.renderer.ansi; "
            "print('rich.console' in sys.modules, "
            "'src.renderer.engine' in sys.modules)"
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "False False"

    def test_renderer_rich_exports_still_accessible(self):
        """``src.renderer`` 的 Rich 侧符号经惰性解析仍可用。"""
        proc = _run_isolated(
            "from src.renderer import (IncrementalRenderer, _StyledOutputAdapter, "
            "OutputAdapter, TokenType, Style, Console, render_toc, "
            "RenderEngine, TokenPipeline, RenderContext, get_safe_console_config); "
            "print(IncrementalRenderer.__name__)"
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "IncrementalRenderer"

    def test_renderer_all_names_resolvable(self):
        proc = _run_isolated(
            "import src.renderer as m\n"
            "missing = []\n"
            "for n in m.__all__:\n"
            "    try:\n"
            "        getattr(m, n)\n"
            "    except Exception:\n"
            "        missing.append(n)\n"
            "print(missing)\n"
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "[]"

    def test_renderer_unknown_attribute_raises(self):
        proc = _run_isolated(
            "import src.renderer\n"
            "try:\n"
            "    src.renderer.NotAThing\n"
            "except AttributeError:\n"
            "    print('AttributeError')\n"
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "AttributeError"


# ═══════════════════════════════════════════════════════════
# 方向 4：循环导入（导入顺序契约消除）
# ═══════════════════════════════════════════════════════════


class TestImportOrderIndependence:
    @pytest.mark.parametrize(
        "module",
        [
            "src.core.base_agent",
            "src.core.sandbox_manager",
            "src.core.subagent",
            "src.core.internal.shared._sandbox_history",
        ],
    )
    def test_module_importable_in_fresh_interpreter(self, module):
        """每个模块都可作为首个导入目标成功加载（无循环导入）。"""
        proc = _run_isolated(f"import {module}; print('ok')")
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "ok"

    def test_base_agent_then_subagent_order(self):
        """``base_agent`` 先于 ``subagent`` 导入（原循环触发顺序）。"""
        proc = _run_isolated(
            "import src.core.base_agent; import src.core.subagent; print('ok')"
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "ok"

    def test_internal_subpackages_importable(self):
        proc = _run_isolated(
            "import src.core.internal.agent, src.core.internal.commands, "
            "src.core.internal.session, src.core.internal.shared; print('ok')"
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "ok"


# ═══════════════════════════════════════════════════════════
# 方向 1：表格预览 _wrap_runs 整段快路径
# ═══════════════════════════════════════════════════════════


def _wrap_runs_reference(runs, maxw):
    """修复前的逐字符通用实现（等价性参考）。"""
    from src.renderer.ansi.helpers import Run
    from src.renderer._utils import cjk_display_width as wcswidth_simple

    if maxw <= 0:
        return [list(runs)] if runs else [[]]
    lines = []
    cur = []
    cur_w = 0
    for run in runs:
        buf = ""
        buf_w = 0
        for ch in run.text:
            cw = wcswidth_simple(ch)
            if cur_w + buf_w + cw > maxw and (cur or buf):
                if buf:
                    cur.append(Run(buf, run.style))
                    buf = ""
                    buf_w = 0
                lines.append(cur)
                cur = []
                cur_w = 0
            buf += ch
            buf_w += cw
        if buf:
            cur.append(Run(buf, run.style))
            cur_w += buf_w
    if cur:
        lines.append(cur)
    return lines if lines else [[]]


def _shape(lines):
    return [[(r.text, r.style) for r in row] for row in lines]


class TestWrapRunsFastPath:
    @pytest.mark.parametrize("maxw", [1, 2, 3, 5, 8, 12, 40, 120])
    @pytest.mark.parametrize(
        "texts",
        [
            ("short",),
            ("",),
            ("abc", "def"),
            ("中文内容",),
            ("mixed 中文 abc",),
            ("x" * 200,),
            ("中文" * 60,),
            ("", "abc", ""),
            ("a", "", "b"),
        ],
    )
    def test_equivalent_to_reference(self, texts, maxw):
        from src.renderer.ansi.helpers import Run
        from src.renderer.ansi.style import Style
        from src.renderer.ansi.table import _wrap_runs

        runs = [Run(t, Style(fg=(i + 1))) for i, t in enumerate(texts)]
        got = _wrap_runs(runs, maxw)
        exp = _wrap_runs_reference(runs, maxw)
        assert _shape(got) == _shape(exp), (texts, maxw)

    def test_fast_path_single_line_for_short_content(self):
        from src.renderer.ansi.helpers import Run
        from src.renderer.ansi.style import Style
        from src.renderer.ansi.table import _wrap_runs

        runs = [Run("短内容", Style(fg=1))]
        out = _wrap_runs(runs, 40)
        assert len(out) == 1
        assert "".join(r.text for r in out[0]) == "短内容"
        # 快路径返回的 runs 可独立于入参列表
        assert out[0] is not runs

    def test_empty_runs_returns_single_empty_line(self):
        from src.renderer.ansi.table import _wrap_runs

        assert _wrap_runs([], 10) == [[]]

    def test_zero_width_keeps_original_semantics(self):
        from src.renderer.ansi.helpers import Run
        from src.renderer.ansi.table import _wrap_runs

        runs = [Run("ab", None)]
        assert _shape(_wrap_runs(runs, 0)) == _shape([[Run("ab", None)]])

    def test_fuzz_equivalence(self):
        import random

        from src.renderer.ansi.helpers import Run
        from src.renderer.ansi.style import Style
        from src.renderer.ansi.table import _wrap_runs

        rnd = random.Random(20261007)
        alphabet = ["a", " ", "中", "文", "é", "🎉", "-"]
        for _ in range(150):
            texts = [
                "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 12)))
                for _ in range(rnd.randint(0, 4))
            ]
            runs = [Run(t, Style(fg=i + 1)) for i, t in enumerate(texts)]
            maxw = rnd.randint(1, 20)
            assert _shape(_wrap_runs(runs, maxw)) == _shape(
                _wrap_runs_reference(runs, maxw)
            ), (texts, maxw)


# ═══════════════════════════════════════════════════════════
# 方向 1：表格预览 _reuse_data 纯追加快路径
# ═══════════════════════════════════════════════════════════


def _render_fresh(rows, aligns, width):
    from src.renderer.ansi.table import TablePreviewCache

    cache = TablePreviewCache()
    return cache.render(tuple(rows[0]) if rows else (), rows, aligns, width)


def _plain(lines):
    return [ln.plain for ln in lines]


class TestTablePreviewIncremental:
    HEADER = ["列一", "列二"]
    ALIGNS = ["left", "left"]

    def _rows(self, n):
        return [list(self.HEADER)] + [[f"a{i}", f"b{i}"] for i in range(n)]

    def test_pure_append_matches_fresh_render(self):
        from src.renderer.ansi.table import TablePreviewCache

        cache = TablePreviewCache()
        for n in (1, 2, 3, 10, 25):
            rows = self._rows(n)
            got = cache.render(tuple(self.HEADER), rows, self.ALIGNS, 80)
            exp = _render_fresh(rows, self.ALIGNS, 80)
            assert _plain(got) == _plain(exp), f"n={n}"

    def test_column_width_change_rebuilds_correctly(self):
        from src.renderer.ansi.table import TablePreviewCache

        cache = TablePreviewCache()
        cache.render(tuple(self.HEADER), self._rows(3), self.ALIGNS, 80)
        # 追加超宽行 → 列宽变化 → 全表重建
        rows = [list(self.HEADER)] + [["a0", "b0"], ["a1", "b1"], ["a2", "b2"],
                                      ["a" * 30, "b3"]]
        got = cache.render(tuple(self.HEADER), rows, self.ALIGNS, 80)
        assert _plain(got) == _plain(_render_fresh(rows, self.ALIGNS, 80))

    def test_head_slide_window(self):
        from src.renderer.ansi.table import TablePreviewCache

        cache = TablePreviewCache()
        rows = self._rows(30)
        cache.render(tuple(self.HEADER), rows, self.ALIGNS, 80)
        slid = [rows[0]] + rows[5:]  # 头部滑窗：移除前 5 个数据行
        got = cache.render(tuple(self.HEADER), slid, self.ALIGNS, 80)
        assert _plain(got) == _plain(_render_fresh(slid, self.ALIGNS, 80))

    def test_prefix_divergence_keeps_common_prefix(self):
        from src.renderer.ansi.table import TablePreviewCache

        cache = TablePreviewCache()
        rows = self._rows(6)
        cache.render(tuple(self.HEADER), rows, self.ALIGNS, 80)
        diverged = [rows[0], rows[1], rows[2], ["完全不同的内容", "x"], ["a9", "b9"]]
        got = cache.render(tuple(self.HEADER), diverged, self.ALIGNS, 80)
        assert _plain(got) == _plain(_render_fresh(diverged, self.ALIGNS, 80))

    def test_incremental_reuses_cached_source_list(self):
        """纯追加时 ``_data_src`` 前缀对象保持（未全量重建数据源）。"""
        from src.renderer.ansi.table import TablePreviewCache

        cache = TablePreviewCache()
        rows = self._rows(4)
        cache.render(tuple(self.HEADER), rows, self.ALIGNS, 80)
        first_src = list(cache._data_src)
        rows2 = rows + [["a4", "b4"]]
        cache.render(tuple(self.HEADER), rows2, self.ALIGNS, 80)
        assert cache._data_src[:len(first_src)] == first_src
        assert len(cache._data_src) == len(first_src) + 1


# ═══════════════════════════════════════════════════════════
# 方向 2：host 注册表查询热路径（无锁读 + 模块级导入）
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def ink_registry():
    from src.tui.ink import registry as reg

    reg.reset()
    yield reg
    reg.reset()


class TestHostRegistryFastPath:
    def test_get_host_reads_extension_registry(self, ink_registry):
        m = lambda fiber, w: (1, 1)  # noqa: E731
        p = lambda fiber, canvas: None  # noqa: E731
        ink_registry.register_host("perf-test-host", m, p)
        assert ink_registry.get_host("perf-test-host") == (m, p)

    def test_get_host_reads_active_builtin(self, ink_registry):
        host = ink_registry.get_host("static-lines")
        assert host is not None and len(host) == 2

    def test_get_host_unknown_returns_none(self, ink_registry):
        assert ink_registry.get_host("no-such-host-tag") is None

    def test_get_host_reflects_disable(self, ink_registry):
        assert ink_registry.get_host("static-lines") is not None
        undo = ink_registry.disable_builtin_hosts(["static-lines"])
        assert ink_registry.get_host("static-lines") is None
        undo()
        assert ink_registry.get_host("static-lines") is not None

    def test_get_host_concurrent_read_write_safe(self, ink_registry):
        """并发查询（无锁读）与注册/注销（持锁写）不产生异常/不一致。"""
        import threading

        stop = threading.Event()
        errors: list = []

        def writer():
            try:
                while not stop.is_set():
                    ink_registry.register_host("perf-race", lambda f, w: (0, 0),
                                               lambda f, c: None)
                    ink_registry.unregister_host("perf-race")
            except Exception as exc:  # pragma: no cover - 竞态失败即缺陷
                errors.append(exc)

        def reader():
            try:
                while not stop.is_set():
                    ink_registry.get_host("perf-race")
                    ink_registry.get_host("static-lines")
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=writer), threading.Thread(target=reader),
                   threading.Thread(target=reader)]
        for t in threads:
            t.start()
        import time as _t

        _t.sleep(0.2)
        stop.set()
        for t in threads:
            t.join(timeout=5)
        assert not errors, errors

    def test_layout_and_paint_import_get_host_at_module_level(self):
        """``_layout_measure``/``components`` 在模块级导入 ``get_host``（热路径）。"""
        import inspect

        from src.tui.ink import _layout_measure, components

        for mod in (_layout_measure, components):
            src = inspect.getsource(mod)
            assert "from .registry import get_host" in src
            # 函数体内不再重复导入
            assert "        from .registry import get_host" not in src


# ═══════════════════════════════════════════════════════════
# 方向 3：路径补全大目录扫描（scandir 类型映射）
# ═══════════════════════════════════════════════════════════


class TestPathCompletionScan:
    def _make_dir(self, tmp_path, n_files=300, dirs=("sub_a", "sub_b")):
        for d in dirs:
            (tmp_path / d).mkdir()
        for i in range(n_files):
            (tmp_path / f"file_{i:04d}.txt").touch()
        return tmp_path

    def test_large_dir_completion_matches_isdir_semantics(self, tmp_path, monkeypatch):
        """大目录（触发预扫描）补全结果与逐项 isdir 语义一致：目录优先 + 字母序。"""
        import os

        from src.tui._completion_engine import CompletionEngine

        self._make_dir(tmp_path)
        monkeypatch.chdir(tmp_path)
        eng = CompletionEngine()
        items = eng.complete("s")
        assert [it.display for it in items] == sorted(
            [d + os.sep for d in ("sub_a", "sub_b")]
        )
        assert all(it.item_type == "dir" for it in items)

        files = eng.complete("file_")
        assert len(files) > 0
        assert all(it.item_type == "file" for it in files)
        assert [it.display for it in files] == sorted(
            it.display for it in files
        )

    def test_enumeration_orders_dirs_first(self, tmp_path, monkeypatch):
        import os

        from src.tui._completion_engine import CompletionEngine

        self._make_dir(tmp_path, n_files=200)
        monkeypatch.chdir(tmp_path)
        eng = CompletionEngine()
        items = eng.complete(".")
        displays = [it.display for it in items]
        # 目录（带 os.sep）排在文件前
        dirs = [d for d in displays if d.endswith(os.sep)]
        files = [d for d in displays if not d.endswith(os.sep)]
        assert dirs and files
        assert displays[:len(dirs)] == dirs

    def test_large_dir_scan_budget(self, tmp_path, monkeypatch):
        """400 项目录补全耗时有界（修复前逐项 stat 需数百 ms）。"""
        import time as _t

        from src.tui._completion_engine import CompletionEngine

        self._make_dir(tmp_path, n_files=400)
        monkeypatch.chdir(tmp_path)
        eng = CompletionEngine()
        t0 = _t.perf_counter()
        items = eng._complete_path("file_")
        elapsed = _t.perf_counter() - t0
        assert items
        assert elapsed < 0.5, f"大目录补全耗时 {elapsed:.3f}s"

    def test_small_dir_still_correct(self, tmp_path, monkeypatch):
        from src.tui._completion_engine import CompletionEngine

        (tmp_path / "only.txt").touch()
        monkeypatch.chdir(tmp_path)
        eng = CompletionEngine()
        items = eng._complete_path("on")
        assert len(items) == 1
        assert items[0].display == "only.txt"
        assert items[0].item_type == "file"


# ═══════════════════════════════════════════════════════════
# 方向 5：输出模型 slots 化（每实例省 __dict__）
# ═══════════════════════════════════════════════════════════


class TestOutputModelSlots:
    def test_styled_run_has_no_instance_dict(self):
        from src.tui.ink.output import StyledRun

        assert not hasattr(StyledRun("abc"), "__dict__")
        assert not hasattr(StyledRun("中文", None, "http://x"), "__dict__")
        assert not hasattr(StyledRun.fast("中文", None, None, 4), "__dict__")

    def test_ansi_run_has_no_instance_dict(self):
        from src.renderer.ansi.helpers import Run

        assert not hasattr(Run("abc"), "__dict__")

    def test_ansi_style_has_no_instance_dict(self):
        from src.renderer.ansi.style import Style

        assert not hasattr(Style(fg=1), "__dict__")

    def test_styled_run_behavior_preserved(self):
        from src.tui.core.style import Style
        from src.tui.ink.output import StyledRun

        run = StyledRun("中文", Style(fg=3))
        assert run.width == 4
        assert run.render() == Style(fg=3).apply("中文")
        assert run == StyledRun("中文", Style(fg=3))
        assert run != StyledRun("中文", None)
        assert "width" not in repr(run)  # repr 不暴露缓存字段
        fast = StyledRun.fast("中", None, None, 2)
        assert fast.width == 2

    def test_ansi_run_width_cache_works(self):
        from src.renderer.ansi.helpers import Run

        run = Run("中文内容")
        assert run.width == 8
        assert run._w == 8  # 缓存写回（slot 字段）
        assert Run("a") == Run("a")
        assert Run("a") != Run("b")

    def test_ansi_line_slots_and_escape_flag(self):
        from src.renderer.ansi.helpers import AnsiLine, Run

        line = AnsiLine([Run("x")])
        line._esc_checked = True
        assert line._esc_checked is True
        line.append("y")
        assert line._esc_checked is False
        assert not hasattr(line, "__dict__")


# ═══════════════════════════════════════════════════════════
# 端到端帧渲染预算（防回归；阈值宽松 10x+ 余量）
# ═══════════════════════════════════════════════════════════


def _build_full_tree(n_history=200, width=120, live_lines=60, tool_lines=40):
    """构造接近真实运行态的组件树（历史 + 开放流式块 + 工具卡 + 补全弹窗）。"""
    import io as _io

    from src.renderer.ansi.helpers import AnsiLine, Run as AnsiRun
    from src.tui.app._state_types import ChatBlock
    from src.tui.app.app import App
    from src.tui.app.model import AppModel
    from src.tui.ink import components as _components
    from src.tui.ink import h
    from src.tui.ink.output import Line, StyledRun
    from src.tui.ink.reconciler import Reconciler
    from src.tui.ink.renderer import InkRenderer

    model = AppModel()
    model.width = width
    for i in range(n_history):
        model.committed_lines.append(
            Line([StyledRun(f"历史消息 {i}: " + "内容" * 40, None)])
        )
    cb = ChatBlock(kind="content")
    for i in range(live_lines):
        cb.lines.append(AnsiLine([AnsiRun(f"流式行 {i} " + "内容" * 10, None)]))
    model.blocks.append(cb)
    tb = ChatBlock(kind="tool")
    tb.extra["tool_name"] = "Bash"
    tb.extra["tool_id"] = "t1"
    for i in range(tool_lines):
        tb.lines.append(AnsiLine([AnsiRun(f"工具输出 {i} " + "x" * 40, None)]))
    model.blocks.append(tb)
    model.completion.visible = True
    model.completion.items = [f"cmd-{i}" for i in range(10)]
    model.completion.texts = list(model.completion.items)
    model.completion.selected = 3
    model.input_text = "测试输入内容"
    model.input_cursor = len(model.input_text)

    rec = Reconciler(schedule_callback=None)
    root = rec.create_root()
    renderer = InkRenderer(stream=_io.StringIO(), height=40)

    def frame():
        element = h(App, {"model": model, "width": width})
        rec.render(root, element, width, 40)
        f = _components.render_frame(root, width)
        renderer.render(f)
        return f

    return model, frame


class TestFrameRenderBudget:
    def _per_frame(self, fn, n=40):
        import time as _t

        fn()
        t0 = _t.perf_counter()
        for _ in range(n):
            fn()
        return (_t.perf_counter() - t0) / n

    def test_full_tree_idle_frame_budget(self):
        """完整组件树（大历史 + 开放块 + 工具卡 + 补全弹窗）无变化帧预算。"""
        _model, frame = _build_full_tree()
        assert self._per_frame(frame) < 0.05

    def test_full_tree_growing_frame_budget(self):
        """流式增长帧（每帧追加一行开放块）预算。"""
        from src.renderer.ansi.helpers import AnsiLine, Run as AnsiRun

        model, frame = _build_full_tree()

        def grow():
            model.blocks[0].lines.append(AnsiLine([AnsiRun("新增流式行", None)]))
            frame()

        assert self._per_frame(grow, n=20) < 0.05

    def test_large_history_frame_scales_sublinearly(self):
        """历史行数增长不线性放大单帧成本（committed 前缀复用）。"""
        _m_small, frame_small = _build_full_tree(n_history=50)
        _m_big, frame_big = _build_full_tree(n_history=2000)
        t_small = self._per_frame(frame_small, n=30)
        t_big = self._per_frame(frame_big, n=30)
        # 历史 40 倍但单帧成本不应随之线性增长（宽松 4x 上限）
        assert t_big < max(t_small * 4, 0.05), (t_small, t_big)


# ═══════════════════════════════════════════════════════════
# 方向 1：活动行「无行内触发字符」增量判定快路径
# ═══════════════════════════════════════════════════════════


class TestPlainActiveWindow:
    def test_trigger_position_helpers(self):
        from src.renderer.ansi import (
            _last_core_trigger_pos, _last_url_prefix_pos,
        )
        from src.renderer.inline_parser import _CORE_FORMAT_CHARS

        # 核心格式字符：命中位置（与 render_inline 快路径判否同源）
        assert _last_core_trigger_pos("") == -1
        assert _last_core_trigger_pos("中文文本内容及数字123 空格") == -1
        assert _last_core_trigger_pos("a*b") == 1
        assert _last_core_trigger_pos("abc*") == 3
        assert _last_core_trigger_pos("*abc") == 0
        for ch in _CORE_FORMAT_CHARS:
            assert _last_core_trigger_pos(f"x{ch}y") == 1, ch
        # ★ 语义变化（英文性能优化）：裸 URL 首字母（h/f/w）不再算核心触发字符
        #   ——普通英文单词不再触发整段行内解析
        assert _last_core_trigger_pos("with") == -1
        assert _last_core_trigger_pos("http://x") == 4  # ':' 是核心字符

        # 裸 URL 前缀：大小写不敏感，位置为前缀起点
        assert _last_url_prefix_pos("") == -1
        assert _last_url_prefix_pos("with the flow") == -1
        assert _last_url_prefix_pos("see http://x") == 4
        assert _last_url_prefix_pos("see HTTPS://x") == 4
        assert _last_url_prefix_pos("go to WWW.example.com") == 6
        assert _last_url_prefix_pos("FTP://h") == 0
        assert _last_url_prefix_pos("no url here, just words") == -1

    def test_plain_paragraph_line_matches_render_paragraph_line(self):
        from src.renderer.ansi import _plain_paragraph_line
        from src.renderer.ansi.blocks import render_paragraph_line

        for text in ("中文内容" * 50, "ascii " * 50, "a", "", "x\ty", "emoji 🎉 混排"):
            got = _plain_paragraph_line(text)
            exp = render_paragraph_line(text)
            assert [(r.text, r.style) for r in got.runs] == [
                (r.text, r.style) for r in exp.runs
            ], text

    def test_note_updates_triggers_incrementally(self):
        from src.renderer.ansi import AnsiStreamRenderer

        r = AnsiStreamRenderer(width=80)
        r._note_paragraph_triggers("纯文本内容")
        assert r._para_last_core == -1
        assert r._para_last_url == -1
        marked = "纯文本内容再加 **标记**"
        r._note_paragraph_triggers(marked)
        assert r._para_last_core == marked.rfind("*")
        # 追加无触发字符的尾部 → 最后一个触发字符位置不变（增量命中）
        extended = marked + " 后续内容"
        r._note_paragraph_triggers(extended)
        assert r._para_last_core == marked.rfind("*")
        # 追加含触发字符的尾部 → 位置前移
        extended2 = extended + " ~尾"
        r._note_paragraph_triggers(extended2)
        assert r._para_last_core == extended2.rfind("~")
        # 裸 URL 前缀跨增量边界（回看窗口命中：'ht' + 'tp://…'）
        r._note_paragraph_triggers("see ht")
        assert r._para_last_url == -1
        r._note_paragraph_triggers("see http://a")
        assert r._para_last_url == 4
        # 前缀关系不成立时全量重扫
        r._note_paragraph_triggers("全新段落无标记内容")
        assert r._para_last_core == -1
        assert r._para_last_url == -1

    def test_plain_active_window_boundaries(self):
        from src.renderer.ansi import AnsiStreamRenderer, _PREVIEW_MAX_LINE_CHARS

        r = AnsiStreamRenderer(width=80)
        limit = _PREVIEW_MAX_LINE_CHARS
        # 短内容：整体窗口
        r._note_paragraph_triggers("无标记")
        assert r._plain_active_window("无标记")
        # ★ 英文纯文本（含 h/f/w 字母，无标记、无 URL）→ 纯文本窗口
        en = "the quick brown fox jumps over the lazy dog"
        r._note_paragraph_triggers(en)
        assert r._plain_active_window(en)
        # 超长内容：触发位置落在窗口之外 → 纯文本窗口
        long_plain = "文" * (limit + 100)
        content = "a**b" + long_plain
        r._note_paragraph_triggers(content)
        assert r._plain_active_window(content)
        # 触发位置落在窗口之内 → 非纯文本
        content2 = long_plain + "**尾标记"
        r._note_paragraph_triggers(content2)
        assert not r._plain_active_window(content2)
        # 窗口内的裸 URL 前缀 → 非纯文本
        content3 = "x" * (limit + 10) + " see http://e.com"
        r._note_paragraph_triggers(content3)
        assert not r._plain_active_window(content3)

    def test_streaming_preview_equivalent_with_and_without_fast_path(self):
        """端到端：长段落流式预览，快路径开/关产出完全一致。"""
        from src.renderer.ansi import AnsiStreamRenderer

        def run(text, chunk, fast):
            r = AnsiStreamRenderer(width=120)
            if not fast:
                r._plain_active_window = lambda content: False
            frames = []
            for i in range(0, len(text), chunk):
                r.write(text[i:i + chunk])
                frames.append([
                    [(run.text, run.style) for run in ln.runs]
                    for ln in r.take_preview_lines()
                ])
            r.close()
            return frames

        cases = {
            "plain_cjk": "中文段落内容" * 800,
            "plain_ascii": "lorem ipsum dolor sit amet " * 200,
            "with_markers": "说明 with `code` 和 **bold** 混排。" * 120,
            "marker_then_plain": "**开头标记** " + "普通文本" * 900,
            "plain_then_marker": "普通文本" * 900 + " **结尾**",
        }
        for name, text in cases.items():
            assert run(text, 16, True) == run(text, 16, False), name

    def test_plain_paragraph_streaming_budget(self):
        """超长纯文本段落流式预览耗时有界（快路径省 O(窗口) 逐帧扫描）。"""
        import time as _t

        from src.renderer.ansi import AnsiStreamRenderer

        text = "中文段落内容" * 1500

        def run():
            r = AnsiStreamRenderer(width=120)
            for i in range(0, len(text), 16):
                r.write(text[i:i + 16])
                r.take_preview_lines()
            r.close()

        run()
        t0 = _t.perf_counter()
        run()
        assert (_t.perf_counter() - t0) < 1.5
