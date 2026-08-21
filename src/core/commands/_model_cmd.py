"""模型选择命令 — /model 切换模型（独立模块）

★ 2026-08-19（模型选择界面代码独立）：/model 命令全部逻辑（provider
推断/同步、PROVIDERS 聚合回退、序号/名称快速切换、无参数弹窗交互选择）
从 ``_config_cmd.py`` 独立成专门模块——模型选择界面代码单一真源；
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

from ..constants import GREEN, YELLOW, DIM, RESET
from ..adapters.output import get_default_output_port

_out = get_default_output_port()


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


def _merge_provider_models(models: list[str]) -> list[str]:
    """将 RC 模型列表与 PROVIDERS 内置模型合并（去重保序）。

    保证 provider 新增的默认模型（如 deepseek-v4-flash-vision-exp）始终
    出现在可用/切换列表——RC 旧配置的 models 列表（如仅 pro/flash）不会
    因未同步更新而缺失新模型。
    """
    try:
        from ...config.defaults import PROVIDERS as _providers
    except (ImportError, KeyError):
        return list(models)
    _seen: set[str] = set()
    merged: list[str] = []
    for _m in models:
        if _m not in _seen:
            _seen.add(_m)
            merged.append(_m)
    for _p in _providers.values():
        for _m in _p.get("models", []):
            if _m not in _seen:
                _seen.add(_m)
                merged.append(_m)
    return merged


def _collect_models(ctx) -> list[str]:
    """获取模型列表：ConfigPort 优先 + PROVIDERS 合并，空时 PROVIDERS 聚合回退。"""
    if ctx.config_port is not None:
        models = ctx.config_port.get_models()
    else:
        from ...config import MODELS as models  # 配置常量 — 函数体内延迟导入（回退）

    if not models:
        try:
            from ...config.defaults import PROVIDERS
            _seen: set[str] = set()
            fallback_models: list[str] = []
            for _p in PROVIDERS.values():
                for _m in _p.get("models", []):
                    if _m not in _seen:
                        _seen.add(_m)
                        fallback_models.append(_m)
            models = fallback_models
        except Exception:
            pass
    # 合并 PROVIDERS 内置模型（RC 旧 models 未包含的新模型自动出现）
    return _merge_provider_models(list(models or []))


def _sync_provider(ctx, model_name: str) -> None:
    """若模型对应的 provider 与当前不一致，更新 RC 配置。"""
    inferred = _infer_model_provider(model_name)
    if inferred is None:
        return  # 自定义模型，不修改 provider
    if ctx.config_port is not None:
        current_provider = ctx.config_port.get("provider", "")
    else:
        try:
            from ...config.loader import get_rc as _get_rc
            current_provider = _get_rc().get("provider", "")
        except (ImportError, KeyError):
            current_provider = ""
    if inferred != current_provider:
        from ...config.loader import update_config as _upd
        _upd("provider", inferred)


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
        # 按序号：/model 2
        if arg.isdigit():
            idx = int(arg)
            if 1 <= idx <= len(models):
                selected = models[idx - 1]
                ctx.state["model"] = selected
                _sync_provider(ctx, selected)
                _out.write(f"{GREEN}  + 已切换到 {selected}{RESET}", level="raw", source="cmd")
                return True
            _out.write(f"{YELLOW}  ! 无效序号，范围 1-{len(models)}{RESET}", level="raw", source="cmd")
            return True
        # 按名称（模糊匹配）：/model deepseek-v4-pro
        matched = [m for m in models if arg.lower() in m.lower()]
        if len(matched) == 1:
            ctx.state["model"] = matched[0]
            _sync_provider(ctx, matched[0])
            _out.write(f"{GREEN}  + 已切换到 {matched[0]}{RESET}", level="raw", source="cmd")
            return True
        elif len(matched) > 1:
            _out.write(f"{YELLOW}  ! 匹配到多个模型: {', '.join(matched)}{RESET}", level="raw", source="cmd")
            _out.write(f"  {DIM}  请使用序号或更精确的名称{RESET}", level="raw", source="cmd")
            return True
        else:
            _out.write(f"{YELLOW}  ! 未找到匹配的模型: {arg}{RESET}", level="raw", source="cmd")
            _out.write(f"  {DIM}  可用模型: {', '.join(models)}{RESET}", level="raw", source="cmd")
            return True

    # ── 无参数：弹窗交互式选择（UserSelectPopup 模态底部视图） ──
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
            ctx.state["model"] = selected
            _sync_provider(ctx, selected)
            _out.write(f"{GREEN}  + 已切换到 {selected}{RESET}", level="raw", source="cmd")
        else:
            _out.write(f"{DIM}  当前已是 {selected}{RESET}", level="raw", source="cmd")
    elif result["action"] == "cancel":
        _out.write(f"{YELLOW}  ! 已取消{RESET}", level="raw", source="cmd")
    elif result["action"] == "error":
        _out.write(f"{YELLOW}  ! 底部栏不可用，请直接指定模型名称{RESET}", level="raw", source="cmd")
        _out.write(f"  {DIM}  可用模型: {', '.join(models)}{RESET}", level="raw", source="cmd")
    return True


__all__ = ["_cmd_model", "_infer_model_provider", "_collect_models", "_sync_provider"]
