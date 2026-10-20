"""TUI 统一配置 — 全局可调参数集中管理。

所有TUI模块的可调参数统一在此处定义，取代各模块的硬编码魔数常量。
使用 ``frozen=True`` 确保配置不可变，可安全跨线程共享。

用法::

    from src.tui._config import TuiConfig
    cfg = TuiConfig.defaults()
    print(cfg.render_interval)  # 0.0166...（1/60）

配置模板约定（横切步骤17）：本项目配置为 ``TuiConfig`` dataclass，无独立
.env/.env.example/config.yaml 模板文件——**新增字段即默认值模板**：新增可调
参数直接在 ``TuiConfig`` 定义字段与默认值（含 docstring 注释说明语义/默认值/
影响模块），无需同步外部模板；``TuiConfig.defaults()`` 为唯一默认值真源。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeVar


__all__: list[str] = ["ConfigBase", "TuiConfig", "RENDER_HZ", "RENDER_INTERVAL_SEC"]

#: 渲染线程帧率（Hz）——恒定 60Hz，唯一真源且**不可改变**（用户需求：
#: 「render 线程跟 input 处理改成 60hz，不能降减少和降级」）。所有帧率
#: 相关参数（``render_interval`` / ``drain_lock_timeout`` /
#: ``bottom_redraw_interval`` / ``spinner_tick_hz``）均由此推导，禁止各模块
#: 另写 ``60`` / ``1/60`` 魔数。
RENDER_HZ: float = 60.0
#: 渲染线程帧间隔（秒）= ``1 / RENDER_HZ``。
RENDER_INTERVAL_SEC: float = 1.0 / RENDER_HZ

# ★ P3（review 2026-08-22）：类级 TypeVar 收窄 defaults()/with_overrides()
#   返回类型——修复前标注 "ConfigBase"（基类），子类 TuiConfig 实际返回
#   TuiConfig 实例，调用方按 ConfigBase 接收后需 cast 才能访问子类字段。
_C = TypeVar("_C", bound="ConfigBase")


class ConfigBase:
    """Frozen dataclass 工厂方法基类 — 提供 defaults() 和 with_overrides()。"""

    @classmethod
    def defaults(cls: type[_C]) -> _C:
        """返回默认配置实例。"""
        return cls()

    def with_overrides(self: _C, **kwargs: Any) -> _C:
        """返回覆盖指定字段的新实例，原实例不变。

        ★ P2（review）：用 ``dataclasses.replace`` 替代 ``self.__dict__``——
        修复前 ``type(self)(**{**self.__dict__, **kwargs})`` 在启用
        ``slots=True`` 的子类上无 ``__dict__``（AttributeError）；replace
        基于字段元数据，兼容 slots/frozen 子类。
        """
        import dataclasses as _dc
        from typing import cast
        return cast(_C, _dc.replace(cast(Any, self), **kwargs))


@dataclass(frozen=True)
class TuiConfig(ConfigBase):
    """TUI 统一配置 — 所有可调参数集中管理。

    所有属性均为不可变（frozen=True），线程安全。
    通过 ``TuiConfig.defaults()`` 获取默认实例。
    """

    # ── 渲染引擎参数 ──────────────────────────────────
    #: 渲染线程帧间隔（秒）——恒定 ``1/60``（60Hz），**不可改变**：任何构造/
    #: 覆盖路径都会被 ``__post_init__`` 强制回真源值。
    render_interval: float = RENDER_INTERVAL_SEC
    drain_lock_timeout: float = RENDER_INTERVAL_SEC  # drain 锁超时（秒），与 render_interval 对齐
    cmd_queue_maxsize: int = 10000          # 命令队列最大容量
    consecutive_full_threshold: int = 10    # 连续满队列告警阈值
    bottom_redraw_interval: float = RENDER_INTERVAL_SEC  # 底部栏重绘间隔（秒），对应 60Hz

    # ── 动画参数 ──────────────────────────────────────
    breath_cycle_len: int = 12              # 呼吸周期长度（帧数）
    pulse_cycle_len: int = 4                # 脉动周期长度（帧数）

    # ── 截断参数 ──────────────────────────────────────
    max_error_length: int = 200             # 错误消息截断长度（字符）
    # ── FadeIn 动效参数 ───────────────────────────────
    fade_total_frames: int = 6              # FadeIn 渐显帧数（兼容旧配置保留）
    fade_start_color: int = 238             # FadeIn 起始暗色（256 色号）
    fade_duration_sec: float = 0.6          # FadeIn 渐显总时长（秒）——绝对时长，与渲染帧率无关
    spinner_tick_hz: float = RENDER_HZ      # spinner 时间基推进频率（Hz），对齐渲染循环 60Hz

    # ── EventBus 参数 ──────────────────────────────────
    eventbus_throttle: float = 0.3          # EventBus 发布频率阈值（秒），对应 300ms
    default_history: int = 3                # 默认工具历史显示条数

    # ── 测试相关 ──────────────────────────────────────
    mock_terminal_width: int = 120          # MockTerminal 默认宽度
    mock_terminal_height: int = 40          # MockTerminal 默认高度

    # ── 崩溃恢复 ──────────────────────────────────────
    max_recover_attempts: int = 3           # render 线程最大重建次数
    recover_delay: float = 0.5              # 崩溃后重建等待（秒）

    # ── 系统监控 ──────────────────────────────────────
    sys_stats_interval: float = 2.0         # 系统监控采集间隔（秒），空闲 2s 一刷输入区分隔线

    # ── 方向D 步骤14：Ctrl+R 反向历史搜索 ──────────────
    # 默认 False 保持既有 Ctrl+R switch_model 语义（键位冲突配置门控）。
    # 启用后 Ctrl+R 进入/推进反向历史搜索；Esc 退出、Enter/Tab 应用匹配。
    reverse_search_enabled: bool = False

    # ── 方向D 步骤16：Esc 取消输入 ──────────────────────
    # 默认 True（2026-10 用户需求：「按一次 esc 要清空输入，在没有流式输出的
    # 时候」）：单次 Esc 在「空闲（无流式输出）+ 缓冲非空」时清空输入取消编辑；
    # 生成中（模型流式输出 / 工具执行）仍为中断当前生成，不清输入。
    # 置 False 可回退旧语义（Esc 仅中断、保留未提交输入）。
    esc_cancel_input: bool = True

    # ── 拖放文件路径规范化（2026-10-07，用户需求） ──────
    # 拖动文件到输入框时终端以「粘贴」形式注入路径（形态各异：单/双引号
    # 包裹、反斜杠转义、file:// URI、Windows 原生路径）。默认 True：粘贴
    # 文本整体判为本地路径列表时规范化为干净路径（去引号 + 还原转义 +
    # file URI 解析 + Cygwin/MSYS 下 Windows→POSIX），多文件每行一个、含
    # 空格路径双引号包裹；非路径文本（自然语言/代码）原样插入。
    # 可通过 TuiConfig 构造覆盖或 RC 键 ``tui_drop_path_normalize`` 关闭。
    drop_path_normalize: bool = True

    # ── 括号粘贴（2026-10-07，TUI React Ink 改进：对齐官方 Ink v7） ──────
    # True（默认）：TUI 启动时启用终端括号粘贴模式（``CSI ?2004h``，退出
    # 还原）——粘贴内容以 ``ESC[200~ … ESC[201~`` 整段到达，不再被拆成逐
    # 字符按键（多行粘贴不误触发 Enter 提交）。False：不协商，回退「单次
    # 输入多字符」启发式识别粘贴。可通过 RC 键 ``tui_bracketed_paste`` 关闭。
    bracketed_paste: bool = True

    # ── 鼠标上报（2026-10，用户需求：启用滚轮/点击交互） ──────────────
    # True（默认）：TUI 启动时启用终端鼠标上报（SGR 1006：``CSI ?1000h`` /
    # ``?1002h`` / ``?1006h``，退出/挂起还原）——列表（ListView/Menu/Tree）与
    # 视口（Viewport）滚轮滚动、列表点击选中生效；终端自身文本选择在多数终端
    # 需按住 Shift（xterm 约定）。False：不协商（终端原生选择优先，无滚轮/
    # 点击交互）。可通过 RC 键 ``tui_mouse`` 关闭。
    mouse: bool = True

    def __post_init__(self) -> None:
        """强制渲染帧率恒定 60Hz——帧率不可被任何构造/覆盖路径降低。

        用户需求（2026-10-20）「render 线程跟 input 处理改成 60hz，不能降减少
        和降级」：``render_interval`` 是帧率唯一表现，任何试图改动的构造
        （直接 ``TuiConfig(render_interval=...)`` 或 ``with_overrides``）都被
        此处强制回真源 ``RENDER_INTERVAL_SEC``。同时把由帧率派生的节流参数
        （``drain_lock_timeout`` / ``bottom_redraw_interval`` /
        ``spinner_tick_hz``）一并强制回 60Hz 真源——堵死任何通过配置单独降低
        渲染/输入/动画/解析刷新频率的降级路径。
        """
        if self.render_interval != RENDER_INTERVAL_SEC:
            object.__setattr__(self, "render_interval", RENDER_INTERVAL_SEC)
        if self.drain_lock_timeout != RENDER_INTERVAL_SEC:
            object.__setattr__(self, "drain_lock_timeout", RENDER_INTERVAL_SEC)
        if self.bottom_redraw_interval != RENDER_INTERVAL_SEC:
            object.__setattr__(self, "bottom_redraw_interval", RENDER_INTERVAL_SEC)
        if self.spinner_tick_hz != RENDER_HZ:
            object.__setattr__(self, "spinner_tick_hz", RENDER_HZ)
