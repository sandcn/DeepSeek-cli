"""
工具模块初始化文件
导出所有工具类
"""

from .base import Func
from .bash import BashFunc as Bash
from .bash_opt import BashOptFunc as BashOpt
from .read_file import ReadFileFunc as ReadFile
from .read_image import ReadImageFunc as ReadImage
from .rm import RmFunc as Rm
from .cp import CpFunc as Cp
from .write_file import WriteFileFunc as WriteFile
from .update_file import UpdateFileFunc as UpdateFile
from .mv import MvFunc as Mv
from .user_select import UserSelectFunc as UserSelect
from .search import SearchFunc as Search
from .find import FindFunc as Find
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
    'Bash', 'BashOpt', 'ReadFile', 'ReadImage', 'Rm', 'WriteFile', 'UpdateFile', 'UserSelect', 'Search', 'Find', 'Ls', 'Mv', 'Cp', 'Mk', 'WebSearch', 'WebFetch', 'Subagent', 'SubagentOpt', 'Skill',
    'get_tools', 'register_tool'
]
