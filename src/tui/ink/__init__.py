"""ink — React Ink 风格组件框架核心（零 Rich 依赖）。

组件树 + 调和器（reconciler）+ hooks + flexbox 布局 + 帧差异渲染，
非全屏（随内容流动）模型。由以下子模块组成：
  - element.py    — 不可变元素（Element/h）
  - fiber.py      — 调和器工作单元（Fiber/hook 节点）
  - hooks.py      — hooks 公共门面（use_state/use_effect/use_ref/... +
                    模块级可变状态唯一真源；实现拆分至 _hooks_* 子模块）
  - _hooks_core.py      — hook 基础设施 + 基础 hooks（state/ref/effect/memo/context/id）
  - _hooks_input.py     — 输入 hooks（use_input / router 发布 / 双签名适配）
  - _hooks_component.py — 组件 hooks（useApp/memo/forwardRef/useImperativeHandle/...）
  - _hooks_focus.py     — 焦点 hooks（useFocus/useFocusManager + 仲裁状态）
  - _hooks_env.py       — 环境 hooks（useMeasure/useWindowSize/useCursor/...）
  - reconciler.py — 挂载/更新 fiber 树 + effect 队列（含 forwardRef）
  - layout.py     — 布局公共门面（LayoutBox/layout_tree + re-export；
                    实现拆分至 _layout_* 子模块）
  - _layout_sizing.py   — 尺寸解析（width/height/padding/flexGrow/flexShrink）
  - _layout_tree.py     — 布局树遍历（host 子节点收集 / function 链下降）
  - _layout_transform.py— 坐标变换（子树平移 / reflow 重排）
  - _layout_flex.py     — flexbox 分布（余数分配 / row justifyContent）
  - _layout_measure.py  — 测量核心（_measure / 行宽 / 对齐 / 收缩）
  - _layout_absolute.py — 绝对定位（第二遍放置）
  - output.py     — StyledRun/Line/Frame 输出模型
  - helpers.py    — 工具门面（ANSI 剥离/换行截断/样式解析/边框块 +
                    re-export；实现拆分至 _ansi_utils/_runs_utils/
                    _style_utils/_border_box）
  - _ansi_utils.py      — ANSI 转义剥离/检测/视觉宽度/ASCII 快路径判定
  - _runs_utils.py      — StyledRun 换行/截断（wrap_runs_by_width + truncate 族）
  - _style_utils.py     — TEXT shorthand 样式解析（color/bold/transform → Style）
  - _border_box.py      — 边框块构建（build_border_box）
  - components.py — host 组件渲染主模块（_paint/_paint_impl/render_frame +
                    re-export；画布/边框辅助拆分至 _paint_canvas/_paint_border）
  - _paint_canvas.py    — 画布行操作（Line↔dict 转换/合并/裁剪，CJK 安全）
  - _paint_border.py    — 边框字符/样式解析 + 边框/背景画布绘制
  - renderer.py   — InkRenderer 非全屏渲染器（行级 diff；diff 纯逻辑经
                    _frame_diff 共享）
  - _frame_diff.py      — 帧差异区间纯函数（差异收集/尾部位移/锚点查找）
  - diff.py       — 新旧 Frame 行级 diff
  - error_boundary.py — ErrorBoundary 函数组件（组件树异常局部降级）
  - session.py    — InkSession（PriorityQueue + render 线程 + 生命周期）；
                    render()/ _SimpleModel 轻量入口经 ``_render_api`` 独立
                    （2026-08-05 架构优化：模块边界拆分）
  - _render_api.py — React Ink render() 轻量入口（render / _SimpleModel；
                    依赖 session.InkSession，函数内惰性 import 避免循环）
  - widgets/      — 控件库（interactive/display 门面 + 按控件拆分模块）

React Ink 语义覆盖（完善 ink，方向3）：
  - forwardRef / useImperativeHandle（命令式句柄暴露）
  - useId（React 18 稳定唯一 ID——挂载时分配，fiber 复用不重分配）
  - TEXT shorthand 样式 props（color/bold/italic/underline/dim/backgroundColor/dimColor）
  - TEXT transform（uppercase/lowercase/capitalize）
  - TEXT wrap prop（textWrap 别名：wrap/truncate/truncate-start/truncate-middle/truncate-end）
  - BOX borderColor / borderStyle 变体（single/double/round/bold/classic/dashed）
  - BOX display:none
  - BOX flexBasis（主轴初始尺寸：column=高度 / row=宽度，与 flexGrow/flexShrink 协同）
  - alignItems/alignSelf（row+column 横轴对齐，偏移随动后代布局盒）
  - flexGrow / flexShrink / justifyContent（space-between/around/evenly）
  - 词边界换行（方向8）：``textWrap="wrap"`` 空格优先断行，单词完整
    （长单词/CJK 回退字符级硬拆）
  - 单边 padding（方向8）：``paddingLeft/Right/Top/Bottom`` 覆盖
    ``paddingX/Y``（缺省回退 ``padding`` 均一值）
  - host ref + useMeasure（方向8）：host 元素 ``ref`` 绑定（RefHook/函数
    ref），layout 后填充布局盒；``useMeasure()`` 返回 ``{ref,width,height}``
    测量 host 组件渲染尺寸（首帧 0x0，layout effect 后触发重渲染）

视口/滚动评估（方向B 步骤12）：
  当前架构为非全屏流动模型：文档高度 = 内容高度（内容驱动），无 DECSTBM
  视口 pin；滚动由终端 scrollback 承担（内容自然流入 scrollback）。实现
  视口/滚动需引入内容偏移模型（如 viewport offset + 滚动条 + 内容裁剪），
  与「内容自然流入 scrollback」设计冲突。**评估结论：不做视口/滚动**
  （记录理由；未来若引入须新增独立 viewport 层，不影响现有流动模型）。
"""

from __future__ import annotations

from .element import (
    BOX,
    TEXT,
    STATIC,
    SPACER,
    APP,
    FRAGMENT,
    Element,
    ElementType,
    Child,
    h,
)
from .output import StyledRun, Line, Frame, FrameBuilder
from .helpers import (
    strip_ansi,
    has_ansi,
    visual_width,
    wrap_runs_by_width,
    truncate_runs,
    truncate_line,
    pad_line,
    line_to_ansi,
)
from .registry import register_host, unregister_host, get_host, has_host
from .error_boundary import ErrorBoundary, create_error_boundary
from .extra import Transform, Static, Newline, Fragment, STATIC_TEXT
from ._render_api import render, measureElement
from .widgets import (
    SelectInput,
    TextInput,
    MultiSelect,
    ConfirmInput,
    Checkbox,
    Spinner,
    ProgressBar,
    Table,
    Badge,
    Divider,
    SPINNER_FRAMES,
    Toggle,
    Panel,
    Tree,
    ListView,
    FocusGroup,
    Key,
    Menu,
    SearchInput,
    Tabs,
    Breadcrumbs,
    Row,
    Column,
    Box,
    Text,
    Flex,
    Spacer,
    Center,
    Stack,
    HStack,
    VStack,
    Grid,
    ZStack,
    RadioList,
    CodeBlock,
    InlineSpinner,
    Gradient,
    StaticLines,
)
from .hooks import (
    use_state,
    use_reducer,
    use_ref,
    use_effect,
    useLayoutEffect,
    use_memo,
    use_callback,
    use_context,
    create_context,
    useId,
    use_input,
    use_fullscreen,
    use_modal,
    use_error_state,
    memo,
    forwardRef,
    useImperativeHandle,
    useMeasure,
    usePrevious,
    useApp,
    useFocus,
    useStdin,
    useStdout,
    useStderr,
    useSyncExternalStore,
    usePaste,
    useBoxMetrics,
    useWindowSize,
    useFocusManager,
    useCursor,
    useIsScreenReaderEnabled,
    useAnimation,
    set_input_router_callback,
    set_app_control,
    set_app_callbacks,
    set_std_accessors,
    set_window_size_accessor,
    set_cursor_position_fn,
    set_render_flush_fn,
    set_suspend_terminal_fn,
)

__all__ = [
    # element
    "BOX",
    "TEXT",
    "STATIC",
    "SPACER",
    "APP",
    "FRAGMENT",
    "Element",
    "ElementType",
    "Child",
    "h",
    # output
    "StyledRun",
    "Line",
    "Frame",
    "FrameBuilder",
    # helpers
    "strip_ansi",
    "has_ansi",
    "visual_width",
    "wrap_runs_by_width",
    "truncate_runs",
    "truncate_line",
    "pad_line",
    "line_to_ansi",
    # registry
    "register_host",
    "unregister_host",
    "get_host",
    "has_host",
    # error boundary
    "ErrorBoundary",
    "create_error_boundary",
    # render API
    "measureElement",
    # generic components
    "Transform",
    "Static",
    "Newline",
    "Fragment",
    "STATIC_TEXT",
    # widgets
    "SelectInput",
    "TextInput",
    "MultiSelect",
    "ConfirmInput",
    "Spinner",
    "ProgressBar",
    "Table",
    "Badge",
    "Divider",
    "SPINNER_FRAMES",
    # layout containers
    "Row",
    "Column",
    "Box",
    "Text",
    "Flex",
    "Spacer",
    "Center",
    "Stack",
    "HStack",
    "VStack",
    "Grid",
    "ZStack",
    # 新增标准控件
    "Toggle",
    "Checkbox",
    "Panel",
    "Tree",
    "ListView",
    "FocusGroup",
    "Key",
    "Menu",
    "SearchInput",
    "Tabs",
    "Breadcrumbs",
    "RadioList",
    "CodeBlock",
    "InlineSpinner",
    "Gradient",
    "StaticLines",
    # hooks
    "use_state",
    "use_reducer",
    "use_ref",
    "use_effect",
    "useLayoutEffect",
    "use_memo",
    "use_callback",
    "use_context",
    "create_context",
    "useId",
    "use_input",
    "use_fullscreen",
    "use_modal",
    "use_error_state",
    "memo",
    "forwardRef",
    "useImperativeHandle",
    "useMeasure",
    "usePrevious",
    "useApp",
    "useFocus",
    "useStdin",
    "useStdout",
    "useStderr",
    "useSyncExternalStore",
    "usePaste",
    "useBoxMetrics",
    "useWindowSize",
    "useFocusManager",
    "useCursor",
    "useIsScreenReaderEnabled",
    "useAnimation",
    "set_input_router_callback",
    "set_app_control",
    "set_app_callbacks",
    "set_std_accessors",
    "set_window_size_accessor",
    "set_cursor_position_fn",
    "set_render_flush_fn",
    "set_suspend_terminal_fn",
]
