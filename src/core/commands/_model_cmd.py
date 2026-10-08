"""模型选择命令 — /model 切换模型（独立模块）

★ 2026-08-19（模型选择界面代码独立）：/model 命令全部逻辑（模型列表
（唯一来源 = 模型档案 ``model_profiles``）、序号/名称快速切换（写
``active_model_profile`` 切换当前生效档案）、无参数弹窗交互选择）从
``_config_cmd.py`` 独立成专门模块——模型选择界面代码单一真源；
``_config_cmd.py`` 保留 re-export 向后兼容（旧导入路径 ``_special_keys.py``
等不变）。

交互式选择（无参数）：``ctx.ui_adapter.run_bottom_bar_selection`` →
标准 React Ink ``UserSelectPopup`` 协议（模态底部视图）——弹窗导航
（↑↓/j/k/g/G）、Enter 确认、Esc 取消由 ``SelectInput`` 控件经 input
router 消费。★ 关键约束：键盘分发由 **render 线程**渲染循环 INPUT 阶段
驱动（``_phase_process_input`` → ``InputDispatcher.read_stdin_once``），
调用方（``plugins/model_plugin.py``）必须保持 render 线程 + cbreak 运行
（不 suspend/stop），否则弹窗显示但上下键/Enter 无效果。
"""

from __future__ import annotations

import logging
import time as _time

from ..constants import GREEN, YELLOW, DIM, RESET
from ..adapters.output import get_default_output_port

_out = get_default_output_port()
_logger = logging.getLogger(__name__)


# ── 辅助函数：根据模型名推断 provider ─────────────────
def _infer_model_provider(model_name: str) -> str | None:
    """遍历 PROVIDERS，返回模型名对应的 provider 名称，未找到返回 None。

    模块级函数，可供 _special_keys.py 等外部模块导入使用。
    """
    try:
        from ...config.defaults import PROVIDERS as _providers
        for _p_name, _p_cfg in _providers.items():
            if model_name in _p_cfg.get("models", []):
                return _p_name
    except (ImportError, KeyError):
        pass
    return None


def _collect_models(ctx) -> list[str]:
    """获取**配置的模型列表**（模型档案 ``model_profiles`` 的模型名）。

    **模型列表的唯一来源**（RC 顶层 ``models`` 字段已移除）；``/model``
    （选择器 / 序号 / 名称）与 **Ctrl+N** 均只从此列表选择——内置 provider
    模型仅作元数据（provider 推断）；未配置时返回空列表，调用方给出
    「请先 /models 新增模型」提示。
    """
    out: list[str] = []
    seen: set = set()
    try:
        from ...config.model_profiles import load_profiles
        for prof in load_profiles():
            m = str(prof.get("model") or "").strip()
            if m and m not in seen:
                seen.add(m)
                out.append(m)
    except Exception:
        pass
    if not out:
        # 回退：配置端口（插件/适配器注入的模型列表；与档案同源）
        try:
            if ctx.config_port is not None:
                port_models = list(ctx.config_port.get_models() or [])
            else:
                from ...config import MODELS as _MODELS
                port_models = list(_MODELS or [])
            for item in port_models:
                m = str(item or "").strip()
                if m and m not in seen:
                    seen.add(m)
                    out.append(m)
        except Exception:
            pass
    return out


def _select_model_profile(ctx, model_name: str) -> tuple[bool, str]:
    """按模型名选中对应模型档案（写 ``active_model_profile``）。

    LLM 访问参数（api_key / base_url / model / provider）唯一来源 = 模型档案，
    因此切换模型 = 切换当前生效档案；调用方负责同步 ``ctx.state["model"]``。
    """
    from ...config.model_profiles import apply_entry, build_model_entries

    for entry in build_model_entries():
        if str(entry.get("model") or "") == model_name:
            return apply_entry(entry)
    return False, f"未找到模型档案: {model_name}"


# ── /model 命令 ─────────────────────────────────────────

def _cmd_model(ctx):
    """切换模型：有参数按序号/名称直接切换，无参数弹窗交互选择。"""
    models = _collect_models(ctx)
    default_model = ""
    if ctx.config_port is not None:
        default_model = ctx.config_port.get_model()
    if not default_model:
        from ...config import MODEL as _m  # 配置常量 — 函数体内延迟导入（回退）
        default_model = _m

    current = ctx.state.get("model", default_model)
    arg = ctx.arg.strip()

    # ── 优先处理直接参数：按序号或名称切换 ──────────────
    if arg:
        if not models:
            _out.write(f"{YELLOW}  ! 未配置模型（请先 /models 新增）{RESET}", level="raw", source="cmd")
            return True
        # 按序号：/model 2
        if arg.isdigit():
            idx = int(arg)
            if 1 <= idx <= len(models):
                selected = models[idx - 1]
                ok, msg = _select_model_profile(ctx, selected)
                ctx.state["model"] = selected
                if ok:
                    _out.write(f"{GREEN}  + {msg}{RESET}", level="raw", source="cmd")
                else:
                    _out.write(f"{YELLOW}  ! {msg}{RESET}", level="raw", source="cmd")
                return True
            _out.write(f"{YELLOW}  ! 无效序号，范围 1-{len(models)}{RESET}", level="raw", source="cmd")
            return True
        # 按名称（模糊匹配）：/model deepseek-v4-pro
        matched = [m for m in models if arg.lower() in m.lower()]
        if len(matched) == 1:
            ok, msg = _select_model_profile(ctx, matched[0])
            ctx.state["model"] = matched[0]
            if ok:
                _out.write(f"{GREEN}  + {msg}{RESET}", level="raw", source="cmd")
            else:
                _out.write(f"{YELLOW}  ! {msg}{RESET}", level="raw", source="cmd")
            return True
        elif len(matched) > 1:
            _out.write(f"{YELLOW}  ! 匹配到多个模型: {', '.join(matched)}{RESET}", level="raw", source="cmd")
            _out.write(f"  {DIM}  请使用序号或更精确的名称{RESET}", level="raw", source="cmd")
            return True
        else:
            _out.write(f"{YELLOW}  ! 未找到匹配的模型: {arg}{RESET}", level="raw", source="cmd")
            _out.write(f"  {DIM}  可用模型: {', '.join(models)}{RESET}", level="raw", source="cmd")
            return True

    # ── 无参数：优先打开**全屏模型选择器**（ModelView——选择/新增/编辑
    #   模型档案 name/model/url/key/provider）；无活跃 ChatUI / 模型视图
    #   不可用时回退下面的弹窗交互选择 ──
    if _open_model_view(ctx):
        return True

    # ── 回退：弹窗交互式选择（UserSelectPopup 模态底部视图） ──
    if not models:
        _out.write(f"{YELLOW}  ! 没有可用的模型，请在配置文件中添加{RESET}", level="raw", source="cmd")
        return True

    # 光标定位到当前模型
    current_idx = 0
    for i, m in enumerate(models):
        if m == current:
            current_idx = i
            break

    # 构建显示项（纯文本，不含 ANSI 码 → 避免弹窗截断问题）
    display_items = []
    for m in models:
        marker = "  <-当前" if m == current else ""
        display_items.append(f"{m}{marker}")

    if ctx.ui_adapter is not None:
        result = ctx.ui_adapter.run_bottom_bar_selection(
            models, display_items, current_idx, title="模型选择",
        )
    else:
        result = {"action": "error", "index": None}

    if result["action"] == "confirmed" and result["index"] is not None:
        selected = models[result["index"]]
        if selected != current:
            ok, msg = _select_model_profile(ctx, selected)
            ctx.state["model"] = selected
            if ok:
                _out.write(f"{GREEN}  + {msg}{RESET}", level="raw", source="cmd")
            else:
                _out.write(f"{YELLOW}  ! {msg}{RESET}", level="raw", source="cmd")
        else:
            _out.write(f"{DIM}  当前已是 {selected}{RESET}", level="raw", source="cmd")
    elif result["action"] == "cancel":
        _out.write(f"{YELLOW}  ! 已取消{RESET}", level="raw", source="cmd")
    elif result["action"] == "error":
        _out.write(f"{YELLOW}  ! 底部栏不可用，请直接指定模型名称{RESET}", level="raw", source="cmd")
        _out.write(f"  {DIM}  可用模型: {', '.join(models)}{RESET}", level="raw", source="cmd")
    return True


# ── /models 命令（全屏模型选择器）──────────────────────────

def _current_entry_index(entries) -> int:
    """定位当前生效条目索引（无标记回退 0）。"""
    for i, e in enumerate(entries or []):
        if isinstance(e, dict) and e.get("current"):
            return i
    return 0


def _apply_model_entry(ctx, session, chat_ui, entry) -> None:
    """把模型选择器选中的条目应用到当前会话。

    写 RC（``config.model_profiles.apply_entry``）→ 同步 ``session.model`` /
    ``ctx.state["model"]`` → 刷新状态栏模型名。任一步失败仅提示，不中断视图。
    """
    from ...config.model_profiles import apply_entry
    ok, msg = apply_entry(entry)
    if not ok:
        _out.write(f"{YELLOW}  ! {msg}{RESET}", level="raw", source="cmd")
        return
    model_name = str(entry.get("model") or "")
    if session is not None and model_name:
        try:
            session.model = model_name
        except Exception:
            _logger.debug("同步 session.model 异常", exc_info=True)
    try:
        ctx.state["model"] = model_name
    except Exception:
        pass
    if chat_ui is not None and model_name:
        try:
            chat_ui.bottom_bar.set_model_name(model_name)
        except Exception:
            _logger.debug("刷新状态栏模型名异常", exc_info=True)
    _out.write(f"{GREEN}  + {msg}{RESET}", level="raw", source="cmd")


def _open_model_view(ctx) -> bool:
    """打开全屏模型选择器（ModelView 模态全屏视图）。

    协议（与 ``_open_config_ui`` 同构）：设置 ``model.model_view``
    （visible=True, seq+1, entries）→ ``model.fullscreen = "model"`` →
    request_bottom_redraw → 轮询 ``state.done``（deadline 超时兜底），期间
    检测 ``applied_seq`` 变化即把选中条目应用到当前会话 → finally 清理
    （重置 model_view 保留 seq + fullscreen 置空 + request_bottom_redraw +
    flush router）。

    无活跃 ChatUI / 模型不可用（单次模式、测试桩）返回 False——调用方回退
    旧弹窗 / 文本显示。
    """
    try:
        from ..adapters.ui_runtime import get_active_chat_ui
        chat_ui = get_active_chat_ui()
        if chat_ui is None:
            return False
        model = chat_ui.get_model() if hasattr(chat_ui, "get_model") else None
        if model is None or not hasattr(model, "model_view"):
            return False
    except Exception:
        return False

    from ...config.model_profiles import build_model_entries
    from ..adapters.ui_runtime import get_model_view_state_cls

    ModelViewState = get_model_view_state_cls()
    entries = build_model_entries()
    prev_seq = getattr(model.model_view, "seq", 0)
    state = ModelViewState(
        visible=True,
        seq=prev_seq + 1,
        entries=entries,
        selected=_current_entry_index(entries),
        deadline=_time.monotonic() + 600,
    )
    model.model_view = state
    model.fullscreen = "model"
    try:
        chat_ui.request_bottom_redraw()
    except Exception:
        pass

    session = getattr(ctx, "session", None)
    last_applied = 0
    try:
        while not state.done:
            if state.applied_seq > last_applied:
                last_applied = state.applied_seq
                if state.applied is not None:
                    _apply_model_entry(ctx, session, chat_ui, state.applied)
            if _time.monotonic() >= state.deadline:
                state.try_set_final("timeout")
                break
            _time.sleep(0.05)
        if state.action == "timeout":
            _out.write(f"{YELLOW}  ! 模型选择器超时关闭{RESET}", level="raw", source="cmd")
        else:
            _out.write(f"{DIM}  模型选择器已关闭{RESET}", level="raw", source="cmd")
        return True
    finally:
        try:
            if not state.done:
                state.try_set_final("timeout")
            if getattr(model, "model_view", None) is state:
                cur_seq = getattr(model.model_view, "seq", 0)
                model.model_view = ModelViewState(seq=cur_seq)
            if getattr(model, "fullscreen", "") == "model":
                model.fullscreen = ""
            try:
                chat_ui.request_bottom_redraw()
            except Exception:
                pass
            try:
                chat_ui.flush_input_router(2.0)
            except Exception:
                pass
        except Exception:
            _logger.debug("_open_model_view cleanup 失败", exc_info=True)


def _list_models_text(ctx) -> None:
    """无 ChatUI 回退：文本列出配置的模型（档案 + RC ``models`` 条目）。"""
    from ...config.model_profiles import build_model_entries, mask_key
    entries = build_model_entries()
    _out.write(f"\n{DIM}  \u2500 模型选择器{RESET}", level="raw", source="cmd")
    if not entries:
        _out.write(f"{YELLOW}  ! 未配置模型（请先 /models 新增）{RESET}", level="raw", source="cmd")
        return
    for e in entries:
        name = e.get("name") or e.get("model") or ""
        kind = "内置" if e.get("kind") == "builtin" else "档案"
        cur = " <-当前" if e.get("current") else ""
        _out.write(
            f"  {DIM}\u2502{RESET} {name}  {DIM}[{kind}/{e.get('provider', '')}]{RESET}"
            f"{cur}",
            level="raw", source="cmd",
        )
        url = e.get("effective_base_url") or e.get("base_url") or ""
        if url:
            _out.write(f"  {DIM}\u2502{RESET}   {DIM}{url}{RESET}", level="raw", source="cmd")
        if e.get("api_key"):
            _out.write(
                f"  {DIM}\u2502{RESET}   {DIM}key: {mask_key(e.get('api_key'))}{RESET}",
                level="raw", source="cmd",
            )
    _out.write(f"  {DIM}在 TUI 中运行 /models 可交互增删改选{RESET}", level="raw", source="cmd")


def _cmd_models(ctx):
    """模型选择器：有 ChatUI 打开全屏 ModelView，无则文本列出。"""
    if _open_model_view(ctx):
        return True
    _list_models_text(ctx)
    return True


__all__ = [
    "_cmd_model",
    "_cmd_models",
    "_infer_model_provider",
    "_collect_models",
    "_select_model_profile",
    "_open_model_view",
    "_apply_model_entry",
]
