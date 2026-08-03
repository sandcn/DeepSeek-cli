# 核心目标
高效交付可运行代码


## 约束
- 推理跟回答纯中文输出（思考/推理/回答均为中文纯文本）
- 工具调用，能并发就并发，并发没有上限
- 碰到失败，分析原因，在试多几次
- 少用subagent，
- **元文件保护**：未经用户明确指定，禁止修改 7 个运行时元文件：**global.md、main.md、plan.md、think.md、map.md、review.md、execute.md**
- 禁止 rm -rf / mkfs / dd / chmod 777 / sudo / chown
