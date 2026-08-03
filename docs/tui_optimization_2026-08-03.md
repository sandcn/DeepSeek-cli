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
