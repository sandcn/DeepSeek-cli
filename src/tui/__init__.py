"""TUI 精简框架 — 零第三方依赖 TUI 系统。

重构说明（2026-07-29）：
  - 删除旧 terminal/animation/components/core/frame/pipeline/layout/widgets/engine/consumer 等 100+ 文件
  - 用约 30 个顶层模块替代，零第三方依赖（blessed/wcwidth 移除）
  - rich 仅限内容渲染（OutputAdapter），TUI 框架本身不依赖 rich
  - 所有 ANSI 序列手写，终端尺寸通过 fcntl.ioctl + os.get_terminal_size 获取

模块架构：
  _config.py                — TuiConfig 配置 dataclass
  _const.py                 — RenderCommand / FrameworkCommand / ChatCommand 枚举
  _width.py                 — 字符显示宽度计算（CJK/Emoji/零宽/ANSI 跳过，Layer 0 纯计算）
  _screen.py                — 纯 ANSI 终端屏幕管理（尺寸/光标/滚动/颜色/SIGWINCH；
                              宽度计算 re-export 自 _width）
  _input.py                 — Input 统一输入管理（stdin 读取/解析/缓冲/历史/补全）
  _input_parser.py          — InputParser ANSI 解析策略（Input 组合持有委托）
  _input_layout.py          — 输入区布局纯函数（换行/光标视觉位置/制表符展开/
                              _wrap_by_width 单一真源；Layer 0 仅依赖 _width）
  _input_metrics.py         — 输入区布局度量（补全弹窗高度/反向搜索状态；ink 光标
                              定位与 app 输入区共享，消除 ink→app 反向依赖）
  _dispatcher.py            — EventDispatcher（DisplayEvent → RenderCommand 过滤+入队）
  _consumer.py              — ChatUIConsumer 兼容实现
  _completion.py            — _CmplHandler 补全处理器
  _completion_engine.py     — CompletionEngine 终端补全引擎（/命令/路径/参数补全，
                              供 _CmplHandler 委托；与 _completion.py 职责互补）
  _assembly.py              — TuiAssembly 子系统装配工厂（瘦编排器：结果容器 +
                              assemble() 编排 + _create_* 兼容转发）
  _assembly_steps.py        — 装配子步骤独立模块（create_infrastructure/...，
                              惰性 import 各自依赖；2026-08-05 装配层重构）
  _base_display.py          — 显示抽象基类
  _diff_renderer.py         — 差异渲染（纯函数，被 core/tools 引用）
  _input_orchestrator.py    — TuiInputOrchestrator 输入等待编排器
  _lifecycle.py             — TuiLifecycle 生命周期管理（start/stop/suspend/resume）
  _output_target.py         — 输出目标协议存根（IOutputTarget）
  _snapshot.py              — Token 速度快照惰性加载共享模块
  _stdout_tracker.py        — _StdoutLineTracker stdout 行追踪（环形缓冲）
  _subagent_panel.py        — SubAgent 面板控制器（EventBus 事件渲染）
  _tool_icons.py            — 工具图标 & Agent 类型标签
  input.py                  — Input 统一输入门面（委托实现至 ._input）

新结构目录（非旧残留）：
  consumer/                 — ChatUIConsumer 事件消费者 + 渲染入口
  core/                     — 核心工具（color/style/singleton；color 调色板
                              → core/_palette.py；2026-08-05 公共动效/样式工具
                              归位：core/_fx.py / core/_theme.py——app/_fx.py、
                              app/_theme.py 降为 re-export 存根）
  events/                   — UI 事件总线 + DisplayEvent 类型定义
  pipeline/                 — 消息编辑/显示管道（message_display/message_editor）
  state/                    — 消费/注册表状态管理（consumer_registry）
  ink/                      — React Ink 风格组件框架（调和器 + flexbox 布局 + 非全屏渲染；
                              2026-08-05 模块边界拆分：hooks → _hooks_core/_hooks_input/
                              _hooks_component/_hooks_focus/_hooks_env（hooks.py 门面持
                              状态唯一真源）；layout → _layout_sizing/_layout_tree/
                              _layout_transform/_layout_flex/_layout_measure/_layout_absolute
                              （layout.py 门面）；helpers → _ansi_utils/_runs_utils/
                              _style_utils/_border_box（helpers.py 门面）；components →
                              _paint_canvas/_paint_border；renderer → _frame_diff；
                              session → _render_api（render()/ _SimpleModel 轻量入口
                              独立，session 保留 re-export）——详见 ink/__init__.py 模块清单）
  app/                      — 应用组件与模型（AppModel + apply_cmd + 组件树；
                              input_area.py → _popup_builder.py 弹窗构建独立；
                              model.py → _tool_output_mixin.py 工具 box 生命周期）
  subagent/                 — SubAgent 面板子域聚合门面（控制器/渲染/状态三模块
                              re-export；实现文件保持顶层，移动受测试 patch 路径
                              依赖约束——见 subagent/__init__.py 设计说明）

子域归类（方向C，2026-08-05）：
  - 输入系统 = 顶层 ``_input*.py`` + ``input.py`` 门面（``input/`` 包因
    ``input.py`` 门面命名冲突不可创建；``_`` 前缀标识内部实现）。输入域拆分：
    ``_input.py``（Input 外观）→ ``_input_io``（I/O）/``_input_parser``（解析）/
    ``_input_buffer``（编辑+历史）/``_input_dispatcher``（分发，补全导航 →
    ``_completion_nav.py``）/``_input_layout``（布局计算）/``_input_metrics``
    （布局度量）。
  - SubAgent 面板 = 顶层 ``_subagent_panel/_subagent_render/_subagent_state``，
    经 ``subagent/`` 子包聚合门面统一入口（文件保持顶层的原因见上）。
  - 历史写盘 = 顶层 ``_history_disk.py``（共享后台 writer；自 _input_buffer.py
    拆分，re-export 保持旧导入路径兼容）。

架构改进（2026-08-16，方向 A-F）：
  - A（InkSession 拆分）：``ink/session.py`` 上帝类按职责拆为
    ``_session_queue_mixin.py``（命令入队/背压/排空安全）+
    ``_session_frame_mixin.py``（组件树/调和/渲染/光标/系统监控）——
    InkSession 组合两 mixin，保留渲染循环调度/生命周期/崩溃恢复/注入；
    常量（_KEEP_CONTENT_CMDS/_PUT_NO_DROP_TIMEOUT/_safe_int）随方法迁移
    并 re-export 保持旧导入路径。
  - B（事件发布门面）：``events/publish.py`` 提供 ``emit(event, bus=...)``
    统一发布入口——收敛 tools/core/api 的 ``get_default().publish`` 散点，
    支持显式总线注入。
  - C（SIGWINCH 多实例）：``_screen.register_sigwinch_callback(cb, token=)``
    + ``unregister_sigwinch_callback(token)``——替代模块级 ``_active_session``
    全局引用，会话 stop 时注销。
  - D（事件总线实例化）：``events/event_bus.py`` 解除强制单例构造——
    ``DisplayEventBus()`` 创建独立实例（测试/多场景隔离），``get_default()``
    保留进程级默认实例。
  - E（渲染循环状态机）：``RenderLoopPhase`` 枚举——``_drain_queue`` 六阶段
    显式迁移（SIGWINCH→INPUT→PANELS→SYSTEM_STATS→DRAIN_COMMANDS→APPLY→RENDER）。
  - F（架构守卫）：``tests/test_tui/test_arch_guard.py`` AST 依赖方向检查
    （ink 不依赖 app / app 不依赖 consumer / Layer 0 纯净 / import 无环）。

Layer 层次（由底向上）：
  _config → _const → _screen → _input → _dispatcher → ink/app → _consumer
"""

from __future__ import annotations

# ═══════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════
from ._config import TuiConfig

# ═══════════════════════════════════════════════════════════
# 命令枚举
# ═══════════════════════════════════════════════════════════
from ._const import RenderCommand, FrameworkCommand, ChatCommand

# ═══════════════════════════════════════════════════════════
# 输入系统
# ═══════════════════════════════════════════════════════════
from ._input import Input, KeyEvent

# ═══════════════════════════════════════════════════════════
# 消费者 API
# ═══════════════════════════════════════════════════════════
from ._consumer import ChatUIConsumer
from .state.consumer_registry import get_active_chat_ui

# ═══════════════════════════════════════════════════════════
# 应用模型（替代 RenderState/ChatRenderState — render_state.py 已并入 AppModel）
# ═══════════════════════════════════════════════════════════
from .app.model import AppModel

# ═══════════════════════════════════════════════════════════
# 聊天域配置
# ═══════════════════════════════════════════════════════════
from .consumer.chat_config import ChatConfig

# ═══════════════════════════════════════════════════════════
# 差异渲染（纯函数，被 core/tools 引用）
# ═══════════════════════════════════════════════════════════
from ._diff_renderer import render_diff_to_ansi, show_file_diff

# ═══════════════════════════════════════════════════════════
# 显示抽象基类
# ═══════════════════════════════════════════════════════════
from ._base_display import BaseDisplay


def __getattr__(name: str):
    """模块级 __getattr__ — 对已删除的旧组件符号提供明确的 ImportError 提示。"""
    _OBSOLETE_SYMBOLS = {
        "Box", "BoxStyle", "RoundedBox", "DoubleBox", "Separator",
        "Spinner", "ProgressBar", "SplashScreen",
        "Widget", "WidgetTree",
        "create_widget", "get_animator", "get_framework", "frame_from_context",
        "create_component",
        "Vertical", "Horizontal", "Padding", "Border", "Grid", "Center",
        "apply_fade_in",
        "MockConsumer", "MockTerminal",
    }
    if name in _OBSOLETE_SYMBOLS:
        raise ImportError(
            f"{name!r} 已在 TUI 重构中移除。"
            f" 请参考 src/tui/* 新模块。"
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """支持 dir() 列出所有导出符号。"""
    return sorted(__all__)


__all__ = [
    # 配置
    "TuiConfig",
    # 命令枚举
    "RenderCommand",
    "FrameworkCommand",
    "ChatCommand",
    # 输入系统
    "Input",
    "KeyEvent",
    # 消费者
    "ChatUIConsumer",
    "get_active_chat_ui",
    # 应用模型
    "AppModel",
    # 聊天域配置
    "ChatConfig",
    # 差异渲染
    "render_diff_to_ansi",
    "show_file_diff",
    # 显示抽象
    "BaseDisplay",
]
