# TUI/ink 优化总结（2026-08-03，含三轮）

## 第一轮（commit deb9b21）

### Bug 修复（探针复现 + 测试锁定）
| Bug | 问题 | 修复 |
|---|---|---|
| BUG-1 | TEXT children 含 `\n` 不换行 | `wrap_runs_by_width` 按 `\n` 强制拆行 |
| BUG-2 | alignItems/alignSelf 偏移不随动后代布局盒 | 新增 `_translate_subtree_x`；`_translate_subtree_y` 收敛为仅平移指定子树 |
| BUG-3 | `_reflow_subtree` 不区分 flexDirection | row 横向累加 x / column 纵向累加 y |

### 完善 react ink
- `forwardRef` + `useImperativeHandle`（命令式句柄 + 卸载清理引用守卫）
- TEXT shorthand 样式 props（color/backgroundColor/bold/italic/underline/dim + 命名色映射）
- TEXT transform（uppercase/lowercase/capitalize）
- BOX borderColor / display:none

### 渲染 / 性能
- 终端尺寸变化 → 全量刷新（resize 后旧帧与物理屏幕不对齐，reset renderer；
  同尺寸保持增量 diff——用户需求 #8）
- `wcswidth_simple` 有界单字符宽度缓存（重复 CJK/emoji 免 bisect，~18% 帧耗时下降）

### 动效
- 推理块 / 错误块 live 角色头呼吸色（提交后 frozen 保持静态）

---

## 第二轮（commit d12b742 → ea5d0b2）

### BUG-4：开放块增量提交打乱 committed_lines 块顺序（内容交错）
- **问题**：流式期间 content 块（索引在 reasoning 之后）被 `commit_open_block`
  提前写入 committed_lines，reasoning 关闭提交时被插到 content 之后——
  形成 content 前半 + reasoning + content 后半的内容交错。
- **修复**：`commit_open_block` 仅允许「连续提交窗口」内（块索引 ==
  committed_count）增量提交；否则等待前面块关闭后随 `commit_block` 一并提交。
- **测试**：`test_render_integration.py::TestOpenBlockCommitOrder`。

### 更多动效（用户需求 #5）
- subagent running 工具 ● 呼吸色（琥珀 208-220 脉动）
- 解析进度行前缀 `~` → 时间基 spinner（⠋⠙⠹… 10Hz 推进）+ 呼吸色

### 补全弹窗超屏防护
- 新增 `_completion_item_rows()`（终端高度推算）——大量选项 / 超长说明时
  弹窗不超终端高度（100 项弹窗从 102 行降至 ≤16 行），`_completion_height`
  与 `_build_lines` 同步限制（光标定位正确）。

### 状态栏空状态压缩
- 无模型名 + 无统计时只渲染分隔线一行（启动期 / 未配置模型时视觉紧凑）。

### 技术债清理
- `_dispatcher` 移除未使用 import（UserMsgCmd/NotificationCmd/SubagentFrameCmd）
- `_output_target` 移除未使用 `List`
- `useImperativeHandle` 未用 fiber 局部变量
- `_dispatcher` 重复 DisplayEvent TYPE_CHECKING 导入

---

## 第三轮（commit 61afbdb → 4b46934）

### useFocus 完善 react-ink 语义
- 参数兼容对象风格 `useFocus({isActive, autoFocus})`（react-ink API）与既有
  bool 参数；返回 `{"isFocused": bool}`（react-ink 语义，组件可据此条件渲染）。

### 技术债清理
- `color.py` 移除未使用 `math`
- `chat_config.py` 移除未使用 `Any`
- `message_display.py` 移除未使用颜色常量（DIM/RESET/CYAN/YELLOW/GREEN）

---

## 第四轮（commit d7088ed）

### `_canvas_row_to_line` 批量 append 优化（~7x 渲染提速）
- **问题**：画布 dict 行 → Line 转换逐字符 `Line.append`（每次做段合并检查 +
  StyledRun 重建），是渲染管线最大热点。
- **修复**：同 style 连续字符段累积后段级一次性 `Line.append`（段长受行宽
  约束，str += 拼接可接受）——基准 100 块历史文档（308 行）渲染
  **5.59ms → 0.81ms/帧**。
- **测试**：`test_components_paint.py::TestCanvasRowBatchAppend`（2 例输出一致性）。

---

## 总验证
- 全部测试通过：**1886 passed**（原 1850 + 新增 36）。
- 流式段落级实时显示验证（空行分隔段落 write 后立即可见）。
- 增量渲染验证：cpu/mem 变化只重写输入区分隔线（不重写已提交内容）。
- 头部动画只重写首行；离屏内容跳过重写（文档 > 屏幕时正确）。
- 窄屏工具卡 / 多行输入 / Ctrl+L 清屏 / 弹窗超屏防护 / session 生命周期正常。

---

## 第五轮（commit 待定）

### Bug 修复
| Bug | 问题 | 修复 |
|---|---|---|
| BUG-5 | `_should_render` force 重绘请求在窗口内丢失（force 置位但 dirty 未置位时下一拍不渲染） | force 置位同步置 dirty，请求保留到下一 10Hz 拍 |
| BUG-6 | input-area `_paint` 每帧逐字符 dict 合并（`_merge` 输入区热路径） | box.x==0 且行未命中时直接存 Line 对象（快路径 + diff 身份短路） |
| BUG-7 | memo 组件 props 未变但 children 变化被误跳过（React children 属 props） | `_memo_should_skip` 增加 children 值比较 + 记录 `_last_memo_children` |
| BUG-8 | 函数组件无法接收变参子级（`h(Comp, {}, child)` 的 child 丢失） | reconciler 将 `element.children` 经副本注入 `props["children"]` |
| BUG-9 | 每帧 DFS 全树查找 committed-chat fiber（大历史树 ~1500 fiber） | 查找结果缓存于 root fiber（删除/卸载自动失效） |
| BUG-10 | 渲染崩溃前 canvas 行 Line/dict 混合处理（历史遗留路径已统一） | input_area/committed-chat/TEXT 共用惰性行 + Line 快路径语义 |

### 完善 react ink（需求 #2）
- **`Transform`** 组件：react-ink `<Transform transform={fn}>` 等价物——字符串变换
  递归应用到 TEXT 叶子（uppercase/lowercase/截断/正则替换等）。
- **`Static`** 组件：react-ink `<Static>` 等价物——children 经 `use_memo(deps=())`
  首帧冻结，后续帧子树 fiber 复用 + 换行缓存 + diff 身份短路 → 静态内容零重渲染。
- **`useStdin`/`useStdout`/`useStderr`** hooks：session 注入惰性 std 流访问器
  （stdin=Input 实例 / stdout=渲染器流 / stderr=sys.__stderr__），react-ink 标准
   hooks 面完整。

### 性能 / 增量渲染（需求 #3、#8）
- input-area Line 快路径：`wcswidth_simple` 调用 62532 → 6394（10x 下降）；
  基准 100 块历史文档（706 行）渲染 **1.28ms → 0.65ms/帧（2x 提速）**。
- committed-chat fiber 缓存：消除每帧 DFS 全树搜索。
- 增量验证（新测试锁定）：仅 live 区变化 → 输出流只含 live 重写（committed 历史
  零重写）；历史增长 → 平移快路径仅写新增行；resize → 全量刷新（需求 #8）。

### 更多动效 / 呼吸效果（需求 #4、#5）
- **`_StreamingLine`**：内容流式期间显示 `⠋ 生成中` 动画块 + 青色呼吸（10Hz 推进）——
  对齐 Claude Code 生成中反馈；空闲/非内容阶段零高度不占行。
- **状态栏模型名呼吸**：流式期间模型名整体亮青 45→55 脉动（8s 周期），与分隔线
  呼吸同步；空闲保持静态强调色。

### 技术债清理（需求 #3）
- `message_editor.py` 移除未使用 `asyncio` 与 6 个颜色常量导入。
- `state/_collection.py` 移除未使用 `Any`/`List`。
- `ink/extra.py` 移除未使用 `Any`/`BOX`。

### 测试
- 新增 `tests/test_tui/ink/test_ink_round5.py`（18 例）：Transform / Static / memo
  children 比较 / std hooks / input-area Line 快路径 / 增量渲染（live-only 重写、
  历史增长、resize 全量）/ `_should_render` force 保留。
- 全部测试通过：**1904 passed**（原 1886 + 新增 18）。

---

## 第六轮（commit 待定）

### 完善 react ink（需求 #2，续）
- **`Newline`** 组件：react-ink `<Newline count={N}>` 等价物——渲染 N 个换行
  （经 `wrap_runs_by_width` 的 `\n` 强制拆行语义）。
- **`Fragment`** 组件：透明分组容器（函数组件形式，`h(Fragment, {}, ...)` 返回
  `fragment` host——不引入独立布局盒，子节点直接流入父容器）。

### 更多动效 / 呼吸效果（需求 #4、#5，续）
- **工具卡边框呼吸**（BEAUTY-10）：运行中开放工具卡顶边框从暗青 23 脉动到亮青
  45（8s 周期）——工具执行中的视觉提示；已关闭/提交卡保持静态（frozen 缓存
  不重算，零额外渲染成本）。

### 测试
- `test_ink_round5.py` 新增 5 例：Newline（2）/ Fragment（1）/ 工具卡边框呼吸（2）。
- 全部测试通过：**1909 passed**（原 1904 + 新增 5）。

---

## 第七轮（commit 待定）

### 修复：工具运行期间 TUI 其他部分冻结（无事件 → 渲染循环空闲跳过）

- **问题**：bash 等工具执行期间无实时输出（`execute()` 无 `publish_line_fn`）
  时无任何命令入队 → `_should_render` 空闲短路（`_dirty=False`）→ 渲染循环
  空转等待。时间基动画（工具卡边框呼吸/● 呼吸、状态栏模型名呼吸/spinner、
  输入区占位点）全部停摆——**工具卡开着但呼吸不动，TUI 像冻结**。
- **修复**：`InkSession._needs_animation()` 判定主 agent 侧活跃动画状态
  （`status.status_active` / `tool_boxes` 非空 / `parse_line` 存在）；任一活跃时
  `_should_render` 持续置脏 → 每 10Hz 拍渲染，时间基动画平滑推进。空闲
  （全部非活跃）保持跳过（CPU ~0，既有优化不回归）。
- **分工**：与 `_subagent_panel._needs_animation`（subagent 面板经
  SUBAGENT_FRAME 命令自行驱动）互补；本方法覆盖主 agent 侧（工具/流式/解析）。
- **验证**：快速脚本确认工具运行期间连续 3 拍 `_should_render(False)` 均为
  True，工具关闭 + 空闲后回退 False。

### 测试
- `test_ink_round5.py` 新增 9 例：`TestNeedsAnimation`（5：空闲 False / status_active /
  工具运行 / parse_line / model 缺失防御）+ `TestShouldRenderAnimationKeepAlive`
  （4：工具运行无事件持续渲染 / 窗口内等待拍 / 空闲仍跳过 / 工具关闭后回退）。
- 全部 TUI 测试通过：**1585 passed**（原 1576 + 新增 9）。

