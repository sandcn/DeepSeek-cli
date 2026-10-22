判断是不是有没完成的东西
只输出一个 JSON 对象，格式：{"continue": true/false, "prompt": "..."}
规则：
- continue=true：有没完成的条目
- continue=false：用户要求已全部完成此时 prompt 必须为空字符串。
- 只输出 JSON：不要代码围栏、解释、前后缀或多余文本。
- prompt 使用与用户消息相同的语言，并给出没有完成的任务，让它继续完成
