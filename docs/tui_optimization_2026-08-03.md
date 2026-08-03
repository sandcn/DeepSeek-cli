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


---

## 第八轮（commit 待定）

### Bug 修复
| Bug | 问题 | 修复 |
|---|---|---|
| BUG-11 | `commit_open_block` 用 `blocks.index(block)` 定位块索引——ChatBlock 为 dataclass，默认 `__eq__` 是**值比较**：字段相同的开放块（如共享 lines 引用的两个空/同内容 content 块）会互相相等，`index()` 恒返回第一个 → 连续窗口判断错误（第二个块被错误允许/阻断增量提交） | 遍历 + `b is block` 身份查找（O(n) 仅增量提交时低频触发） |
| BUG-12 | `request_exit` 在渲染线程内仅置位不调 `stop()`（防 join 自身死锁）→ 线程退出后 `_render_running` 恒 True，`start()` 判 True 直接 return（无法重启，状态不一致） | `_render` 循环 `_exit_requested` 分支同步置 `_render_running = False`（exit 后渲染状态与「线程已停止」一致） |
| BUG-13 | `input-area._measure` 对畸形 `width` prop 直接 `int()` 抛异常 → 经 layout_tree 传播中断整帧渲染（其他布局解析均有 try/except 兜底，此处缺失） | 与内置布局一致加 try/except 兜底回退可用宽度 |

### 性能优化（ioctl → TTL 缓存）
- `_completion_item_rows`：修复前每次调用直接 `_get_terminal_size()`（fcntl.ioctl），
  补全弹窗可见时 `_completion_height` 在 `_measure` 与 `_position_cursor` 每帧各调一次
  → 每帧 2 次系统调用；改走 `TerminalWidthCache.get_height()`（TTL 缓存）。
- `_subagent_render._terminal_max_lines/_terminal_max_width`：修复前每次渲染卡片直接
  `_get_terminal_size()`（subagent 面板 10Hz 刷新每帧 2 次 ioctl）；改走
  `TerminalWidthCache`（TTL 缓存）。

### 完善 react ink（需求 #2）
- **`Text` 组件 `align`**（left/right/center）：react-ink `<Text align>` 等价物——
  换行后按布局宽度调整行内容（前导空格），多行各自对齐。对齐结果随
  `_wrap_cache` 缓存（align 入缓存键——align 变化重算，同 align 跨帧命中返回
  对齐行对象，diff 身份短路保持）。

### 更多动效 / 呼吸效果（需求 #4、#5）
- **子代理组卡边框呼吸**（BEAUTY-11）：运行中子代理组卡顶/主体/底边框从暗青 23
  脉动到亮青 45（8s 周期，与工具卡边框呼吸同步）——子代理执行中的视觉提示；
  全部完成（closed）保持静态 `_C_BORDER`（不调用 time_glow，零额外成本）。
- **状态栏模型名渐显重置**（BEAUTY-1 完善）：fade 键含 `model_name`——切换模型
  （Ctrl+N）后新模型名重新渐显（修复前旧 fade 状态残留，新名直接以呼吸色显示）。

### 界面美化（需求 #4）
- **启动品牌屏美化**（BEAUTY-12）：splash 增加 `✦` 图标 + 版本号/模型名
  （`  ✦ DeepSeek CLI · v2.x.x` 或 `· {model_name}`），对齐 TopHeader 渐变标题视觉。

### 技术债清理（需求 #3）
- `_consumer.py` TYPE_CHECKING 块移除纯未使用符号（AppModel/事件类型类——字符串
  注解仅引用 InkSession/InkBridge/EventDispatcher/Input/_CmplHandler 五个框架类型）。

### 测试
- `test_render_integration.py`：BUG-11 身份比较（2 断言：b2 阻止 + b1 允许）。
- `test_session.py`：BUG-12 渲染线程 exit 后 `_render_running` 置 False。
- `test_input_area.py`：畸形 width 兜底。
- `test_layout.py`：Text align 6 例（默认/right/center/多行/缓存身份/缓存失效）。
- `test_subagent_panel.py`：组卡边框呼吸 3 例（running 调 time_glow / closed 不调 / 呼吸色范围）。
- `test_status_bar.py`：model_name 切换渐显重置（dot 回起始暗色 238）。
- 全部测试通过：**1948 passed**（原 1935 + 新增 13）。

---

## 第九轮（commit 待定）— review agent 深度审查 + 14 项 bug 修复

### 背景
派发 2 个并行 review agent 深度审查 ink 框架核心（layout/reconciler/renderer/
hooks/session）与输入/命令/渲染流程（model/apply/input_area/dispatcher/
_stdout_tracker/subagent/event_bus），共报告 23 项发现，经验证修复 14 项真实
bug（其余为测试时序误用/防御性改进/保留设计）。

### 布局层「坐标后处理」系统性缺陷修复（P0/P1，最严重）
| Bug | 问题 | 修复 |
|---|---|---|
| BUG-14 | `_translate_subtree_x/y` 只递归 `fiber.child`（首子链），嵌套容器内第 2+ 个子节点（child.sibling）不随动 → alignItems/alignSelf/探针复用偏移后**文本与边框错位**（确定性渲染错误） | 递归遍历 `child + child.sibling`（不遍历子树根自身 sibling，防调用方循环重复偏移） |
| BUG-15 | flexGrow / justifyContent（column+row）/ row flexGrow / `_reflow_row_justify` 只改直接子节点 x/y，不平移后代 → 嵌套容器内文本停留旧坐标 | 各偏移/重排路径改经 `_translate_subtree_x/y`（相对偏移，防双重平移）+ flexGrow 后 `_reflow_subtree` 递归重排孙节点（与 flexShrink 对称） |

### 调和/React 语义缺陷修复
| Bug | 问题 | 修复 |
|---|---|---|
| BUG-16 | memo 组件 + use_context：Provider 值变化被 `_memo_should_skip` 短路跳过 → 陈旧输出（React 语义：context 变更强制重渲染消费者） | `_clear_context_cache_subtree` 对被清缓存的 fiber 置 `_context_dirty`；`_memo_should_skip` 校验（精确逐 fiber，无关 Provider 不误伤）；`use_context` 消费后清除 |
| BUG-17 | 零宽 row 子 TEXT（w=0,h=0）仍被绘制 → 文本溢出容器（宽 3 的 row 内 "abc"+"def" 渲染出 "abcdef"） | `_paint` TEXT 分支加 `box.w<=0 or box.h<=0` 守卫 |
| BUG-18 | `_cleanup_contexts` 卸载时 pop 注册表 → Provider 重挂载失效（use_context 回退 default） | 注册表条目（Context 对象）生命周期与挂载解耦——不再 pop；卸载回退 default 由 return_ 链查找自然实现 |

### 数据完整性修复（stdout tracker）
| Bug | 问题 | 修复 |
|---|---|---|
| BUG-19 | `_flush_buffered_lines` 先取空缓冲再加锁，flock 失败 return → 缓冲行永久丢失 | 加锁失败/OSError 时把 buf 放回 `_output_buffer` 头部（后续刷盘重试） |
| BUG-20 | 定时刷盘 `_timer_flush_callback` 不检查 `_flush_in_progress` → 与 worker 并发写文件行序颠倒 | worker 在途时仅置 `_pending_flush`（worker finally 统一处理）；worker finally 对 `_pending_flush` + 缓冲非空继续刷盘 |

### 渲染/内存/健壮性修复
| Bug | 问题 | 修复 |
|---|---|---|
| BUG-21 | close_reasoning/close_content 全量冻结 `_block_to_ink_lines(block, 0)`（已增量提交行重复存储）→ 大响应关闭后内存约翻倍 | 仅冻结未提交尾（`committed_line_count` 起），与 close_tool_box 一致 |
| BUG-22 | open_tool_box 复用已增量提交 box 只更新块内标题，committed_lines 顶边框标题陈旧 | 复用且 `committed_line_count>0` 时重建 committed_lines `_first_committed_offset` 处顶边框行 |
| BUG-23 | `_build_lines` 缓存命中前无条件 tuple 化全部补全项（大列表每帧 O(n) 分配） | 快照改轻量指纹（id/len/selected，show_completions 每次新建列表 id 变 → 重建；原地修改罕见不处理） |
| BUG-24 | subagent 组卡边框 fill `max(2,...)` → 标题满宽时行超 1 列 | 改 `max(0,...)`（fill=0 时标题直接衔接右角） |
| BUG-25 | `wcswidth_simple` 与 `cjk_display_width` 零宽字符集不一致（BOM/软连字符/零宽空格等）→ committed 行 wrap 判断与渲染宽度不一致（含 BOM 行超宽） | `_ZERO_WIDTH_RANGES` 补齐 0x00AD/200B/200E/200F/2060-2064/FEFF（对齐 cjk/wcwidth 语义） |
| BUG-26 | 极端窄屏（width<5）工具卡边框固定前缀撑破终端宽度 | width<5 时降级为无边框裸行（标题/主体/状态均截断至 width） |
| BUG-27 | `_build_lines` 与 `_completion_height` 对 selected 越界处理不一致（弹窗底部多空白行） | 绘制侧统一钳制 `min(selected, len(descs)-1)` |

### 测试
- 新增 `test_review_bug_fixes.py`（BUG-15 嵌套 flexGrow/justify + BUG-17 零宽）+ 各模块测试
  （BUG-14 平移 sibling / BUG-16 memo×context / BUG-18 重挂载 / BUG-19 flock 放回 /
  BUG-20 定时器在途 / BUG-21 冻结尾 / BUG-22 复用标题 / BUG-23 快照指纹 /
  BUG-24 边框 fill / BUG-25 BOM 宽度 / BUG-26 窄屏 / BUG-27 selected 越界）。
- 另含提示符提亮（补全弹窗打开时 45-100 vs 空闲 32-81）+ 测试。
- 全部测试通过：**1970 passed**（原 1949 + 新增 21）。
