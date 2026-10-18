"""
工具模块初始化文件
导出所有工具类
"""

from .base import Func
from .bash import BashFunc as Bash
from .bash_opt import BashOptFunc as BashOpt
from .read import ReadFunc as Read
from .read_image import ReadImageFunc as ReadImage
from .rm import RmFunc as Rm
from .cp import CpFunc as Cp
from .write import WriteFunc as Write
from .edit import EditFunc as Edit
from .mv import MvFunc as Mv
from .user_select import UserSelectFunc as UserSelect
from .grep import GrepFunc as Grep
from .glob import GlobFunc as Glob
from .ls import LsFunc as Ls
from .web_search import WebSearchFunc as WebSearch
from .web_fetch import WebFetchFunc as WebFetch
from .skill_tool import SkillFunc as Skill
from .subagent import SubagentFunc as Subagent
from .subagent_opt import SubagentOptFunc as SubagentOpt
from .mkdir import MkdirFunc as Mk
from .registry import get_tools, register_tool

# 注：类名导入与 __all__ 需同步维护（新增工具时两处都要加；
# 漏加 __all__ 不影响 registry 自动发现，但影响 ``from src.tools import *``）
__all__ = [
    'Func',
    'Bash', 'BashOpt', 'Read', 'ReadImage', 'Rm', 'Write', 'Edit', 'UserSelect', 'Grep', 'Glob', 'Ls', 'Mv', 'Cp', 'Mk', 'WebSearch', 'WebFetch', 'Subagent', 'SubagentOpt', 'Skill',
    'get_tools', 'register_tool'
]
