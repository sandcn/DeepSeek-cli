"""应用主入口 — 从 app_init.py 拆分而来

包含 async main() 函数，是应用的异步入口点。

「一切皆插件」：main() 只保留最薄组合根——解析参数、配置日志级别、构建内核
插件树、经 ``ctx.app`` 运行应用、退出前清理。应用生命周期（可观测性启动、
输出消费者、信号处理、ChatUI 错误处理器、子命令分发、clawbot、交互/单次
模式装配）全部由 ``src.plugins.app`` 的 ``ctx.app`` 服务承载，不再在入口
直接装配。
"""

from __future__ import annotations

import asyncio
import logging

from ._args import _parse_args

_logger = logging.getLogger(__name__)

_DEFAULT_PROFILE = "cli"


def _select_model_profile(name: str) -> bool:
    """``-m/--model``：按模型名/显示名匹配模型档案并设为当前生效。

    LLM 访问参数（api_key / base_url / model / provider）唯一来源 = 模型档案；
    因此 CLI 只能**选择已有档案**，不能直接指定任意模型名。未匹配时打印可用
    档案清单并返回 False（调用方终止启动）。
    """
    from ..config.model_profiles import build_model_entries, set_active_profile

    target = str(name or "").strip()
    entries = build_model_entries()
    matched = [
        e for e in entries
        if target and target in (str(e.get("model") or ""), str(e.get("name") or ""))
    ]
    if not matched:
        available = ", ".join(str(e.get("model") or "") for e in entries) or "（无）"
        print(
            f"\n  ! 未找到模型档案: {target}\n"
            f"    可用档案: {available}\n"
            f"    请先运行 `python chat.py` 并用 /models 新增模型档案。",
        )
        return False
    ok = set_active_profile(matched[0].get("index"))
    if not ok:
        print(f"\n  ! 写入模型选择失败: {target}")
        return False
    return True


async def main():
    """异步主入口 — 使用 asyncio.run() 调用"""
    args = _parse_args()

    # ── 设置日志级别（须在 ChatUI 错误处理器注册之前——root 已有 handler 时
    #    basicConfig 静默失效，故 AppService.bootstrap 之后不得再配置） ──
    if args.verbose >= 2:
        logging.basicConfig(level=logging.DEBUG)
    elif args.verbose >= 1:
        logging.basicConfig(level=logging.INFO)

    # ── CLI 模型选择（须在内核构建之前——配置读取在构建期发生）：按模型名
    #    匹配已有模型档案并设为当前生效（模型参数唯一来源 = 模型档案） ──
    if args.model and not _select_model_profile(args.model):
        return

    profile = getattr(args, "profile", "") or _DEFAULT_PROFILE
    patch_paths = list(getattr(args, "patch", None) or [])

    from ..plugins.bootstrap import build_kernel, shutdown_kernel
    from ..tui.events.consumers import publish_output

    kernel = None
    app = None
    try:
        # ── 构建内核插件树（一切皆插件的组合根） ──
        kernel = await build_kernel(profile, patch_paths=patch_paths)
        app = kernel.resolve_service("app")
        if app is None:
            raise RuntimeError("app 插件未加载：profile 缺少 app 条目")
        await app.run(args)

    except KeyboardInterrupt:
        publish_output("\n\n  ⚠ 用户中断", level="raw")
    except asyncio.CancelledError:
        publish_output("\n\n  ⚠ 任务被取消", level="raw")
    except Exception as e:
        publish_output(f"\n  ❌ 致命错误: {e}", level="raw")
        logging.critical("应用崩溃", exc_info=True)
    finally:
        # ── 停止输出消费者 / 退出清理（保留输出至异常路径之后） ──
        if app is not None:
            try:
                app.shutdown()
            except Exception:
                _logger.debug("应用清理异常", exc_info=True)
        # ── 卸载内核插件树（逆序撤销全部注册，含 MCP 连接关闭） ──
        try:
            await shutdown_kernel(kernel)
        except Exception:
            _logger.debug("内核卸载异常", exc_info=True)
