"""Core 模块 — 零业务依赖的内核组件。

子模块列表：

| 子模块 | 文件 | 说明 |
|--------|------|------|
| animator.py | AnimatorContext / BreathPalette | 全局帧号推进器与呼吸调色板 |
| ansi_utils.py | 视觉宽度/截断/ANSI 解析 | ANSI 转义序列处理工具 |
| color.py | Color / StyleSheet | 256 色调色板与样式系统 |
| effects.py | 动效原语统一入口 | 纯函数动效，已按类别拆分到 _wave/_sparkle/_train/_compose 子模块 |
| formatter.py | ANSI 格式化 | 文本样式格式化 |
| gradient.py | gradient_range / gradient_* | 渐变调色板生成 |
| palettes.py | 预定义调色板常量 | GRADIENT_AURORA 等 |
| state.py | TUIStateTree / UISessionState / InputState / StreamingState | 统一状态容器 |
| style.py | Style 数据类 | 样式属性集合 |
| text_utils.py | truncate / build_* 系列 | 纯文本工具与 ANSI 构建器 |
| theme.py | Theme 层次化主题类 | 支持链式继承与控件级覆盖 |
| theme_loader.py | 用户主题加载 | YAML 解析与主题实例化 |
| time_format.py | 时间格式化工具 | 时间显示格式转换 |
| ttl_cache.py | TTL 缓存 | 带过期时间的缓存装饰器 |

依赖方向：core 层不依赖 widgets/layout/terminal/animation/events 上层模块。
"""
