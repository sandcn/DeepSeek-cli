判断是不是有没完成的东西
只输出一个 JSON 对象，格式：{"continue": true/false, "prompt": "...", "compact": true/false}
规则：
- continue=true：有没完成的条目
- continue=false：用户要求已全部完成此时 prompt 必须为空字符串。
- compact=true：上述数据中最后一次回答出现「上下文不够 / 上下文不足 / token 不够 / 上下文快满了 / 需要压缩上下文 / 超出上下文窗口」等以上下文不足为借口推脱、中止或无法继续完成任务的说法
- compact=false：其它情况
- 三个字段都必须输出，compact 必须是布尔值 true 或 false
- 只输出 JSON：不要代码围栏、解释、前后缀或多余文本。
- prompt 使用与用户消息相同的语言，并给出没有完成的任务，让它继续完成
        格式
        ```
        ##强制完成所有，不能有借口
        1.完成xx
        2.完成xx
        ```
        不要有多余的其它东西
