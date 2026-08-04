"""ink — React Ink 风格组件框架核心（零 Rich 依赖）。

组件树 + 调和器（reconciler）+ hooks + flexbox 布局 + 帧差异渲染，
非全屏（随内容流动）模型。由以下子模块组成：
  - element.py    — 不可变元素（Element/h）
  - fiber.py      — 调和器工作单元（Fiber/hook 节点）
  - hooks.py      — use_state/use_effect/use_ref/use_reducer/useImperativeHandle
  - reconciler.py — 挂载/更新 fiber 树 + effect 队列（含 forwardRef）
  - layout.py     — flexbox 子集 + 文本换行
  - output.py     — StyledRun/Line/Frame 输出模型
  - helpers.py    — ANSI 剥离 / 宽度测量 / 换行截断
  - components.py — host 组件渲染函数
  - renderer.py   — InkRenderer 非全屏渲染器（行级 diff）
  - diff.py       — 新旧 Frame 行级 diff
  - error_boundary.py — ErrorBoundary 函数组件（组件树异常局部降级）
  - session.py    — InkSession（PriorityQueue + render 线程 + 生命周期）

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
