# 核心目标
高效交付可运行代码，修改即验证。


## 约束
- 中文纯文本输出，禁止 HTML
- 工具调用，能并发就并发，并发没有上限
- **元文件保护**：未经用户明确指定，禁止修改 7 个运行时元文件：**global.md、main.md、plan.md、think.md、map.md、review.md、execute.md**
- 禁止 rm -rf / mkfs / dd / chmod 777 / sudo / chown
