# TUI/ink 优化总结（2026-08-03）

## 一、Bug 修复（探针复现 + 测试锁定）

### BUG-1：TEXT children 含 `\n` 不换行（react-ink 语义缺失）
- **问题**：`wrap_runs_by_width` 不处理 `\n`，`h(TEXT, {"children": "a\nb\nc"})`
  渲染为单行含字面 `\n`（宽度 0），而非三行。
- **修复**：`helpers.wrap_runs_by_width` 将 `\n` 作为强制换行符（react-ink Text
  语义——先按 `\n` 拆逻辑行再按宽度 wrap）。
- **测试**：`test_render_fixes2.py::TestTextNewlineSplit`（4 例）。

### BUG-2：alignItems/alignSelf 偏移不随动后代布局盒（嵌套容器渲染错位）
- **问题**：column `alignItems: center` 偏移子节点 x 时只改子容器自身
  `layout_box.x`，不改其后代 → 嵌套容器内 TEXT 与边框错位
  （`['   ┌─┐', ' a │ │', '   └─┘']`）。
- **修复**：新增 `_translate_subtree_x`（与既有 `_translate_subtree_y` 对称），
  row/column 的 alignItems/alignSelf 偏移均整棵子树平移。
- **附带修复**：`_translate_subtree_y` 原实现隐含 sibling 遍历（探针路径 3+
  子节点重复偏移）——收敛为仅平移参数指定子树。
- **测试**：`test_render_fixes2.py::TestAlignOffsetDescendants`（4 例）。

### BUG-3：`_reflow_subtree` 不区分 flexDirection
- **问题**：flexShrink 触发时 row 容器子节点被按纵向堆叠重排 y。
- **修复**：`_reflow_subtree` 区分 row（x 累加）/column（y 累加）。
- **测试**：`test_render_fixes2.py::TestReflowSubtreeDirection`（2 例）。

## 二、完善 react ink（新语义 + 新 API）

| 特性 | 实现 | 测试 |
|---|---|---|
| `forwardRef` | hooks.py：标记 `_is_forward_ref`，reconciler 改以 `(props, ref)` 双参调用 | test_hooks.py（3 例）|
| `useImperativeHandle` | hooks.py：MemoHook 生成句柄 + EffectHook 卸载清理（引用守卫防误清） | test_hooks.py |
| TEXT shorthand 样式 | `helpers.resolve_text_style`：color/backgroundColor/bold/italic/underline/dim（含命名色映射） | test_ink_enhancements.py |
| TEXT transform | `helpers.apply_text_transform`：uppercase/lowercase/capitalize | test_ink_enhancements.py |
| BOX borderColor | `components._border_style`：int/命名色 | test_ink_enhancements.py |
| BOX display:none | layout `_measure` 零盒 + components `_paint` 跳过 | test_ink_enhancements.py |

## 三、渲染 / 性能优化

### 1. 终端尺寸变化 → 全量刷新（用户需求 #8）
- `InkSession._render_frame` 检测 width/height 变化置 `_resize_pending`，
  消费时 `InkRenderer.reset()`（`_prev=None`）——resize 后旧帧与物理屏幕
  不对齐，须全量重写而非增量 diff；同尺寸保持增量。
- **测试**：`test_session.py::TestResizeFullRefresh`（2 例）。

### 2. 字符宽度测量性能（~18% 帧耗时下降）
- `_screen.wcswidth_simple` 增加有界单字符宽度缓存（`_char_width_cache`，
  maxsize 4096）——重复 CJK/emoji 字符免区间二分（`_in_ranges_bisect`）。
  ASCII 走原有 O(1) 快路径（不经缓存，避免 dict 查找开销）。
- 基准：50 消息 + 100 工具行文档，帧耗时 1.95ms → 1.61ms。
- **测试**：`test_screen.py::TestWcswidthCharCache`（4 例）。

## 四、动效 / 呼吸效果（用户需求 #5）

- **推理块角色头呼吸色**：live 推理中 `▍💭 思考` 从暗灰 242 呼吸到亮灰
  252（8s 周期）；提交后保持静态暗灰（frozen 缓存不重算）。
- **错误标记呼吸色**：live 错误块 `▎错误` 在 196 邻域脉动（8s 周期）。

## 五、验证

- 全部测试通过：**1883 passed**（原 1850 + 新增 33）。
- 修改文件：12 个源码 + 4 个测试文件。

## 六、可追溯性

- 每个修复均在代码注释标注「方向3（本轮）」与修复原因。
- 新增测试独立成文件（`test_render_fixes2.py` / `test_ink_enhancements.py`）
  或追加既有文件（`test_hooks.py` / `test_session.py` / `test_screen.py`）。
