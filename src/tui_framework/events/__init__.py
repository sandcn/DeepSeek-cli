"""Events 模块 — 事件系统。

子模块列表：

| 子模块 | 说明 |
|--------|------|
| event_types.py | 事件类型定义：DisplayEvent 基类 + KeyPressEvent/MouseEvent/ResizeEvent/FocusEvent + ALL_EVENT_TYPES |
| event_bus.py | EventBus — 线程安全的事件发布/订阅（基于 queue.Queue + threading.RLock） |
| event_pool.py | 事件池 — 事件对象复用池（减少 GC 压力） |
| input_reader.py | InputReader — 终端输入读取器，封装 Blessed inkey()，解析 ANSI 转义序列 |

新事件类型（框架级）：
  - KeyPressEvent(key, ctrl, alt, shift) — 键盘事件
  - MouseEvent(x, y, button, action) — 鼠标事件
  - ResizeEvent(width, height) — 终端尺寸变化
  - FocusEvent(widget_id, gained) — 焦点切换事件
"""
