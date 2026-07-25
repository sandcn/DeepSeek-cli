#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""费用显示组件 — 显示每轮对话的 Token 消耗和费用。

将原 show_round_cost() 转为 TuiComponent 组件渲染逻辑。
组件化后可通过 TUI 框架的 OutputAdapter 统一输出，
同时保留 render() 方法供直接获取格式化字符串。
"""

from __future__ import annotations

import logging
import shutil

from ._base import TuiComponent
from ..core.cost import compute_round_cost_data
from ..core.ansi_utils import truncate_ansi_line
from ..core.style import Style
from ..render_buffer import RenderBuffer

_logger = logging.getLogger(__name__)



