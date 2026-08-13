"""
bash_task — 按 task_id 操作后台 bash 任务

配合 bash 工具 background=True 模式使用。bash 后台启动后返回
{"task_id": "bg-xxx", "status": "running", "command": "..."}，
大模型可据此用 bash_task 工具按 task_id 操作：

- op=wait   等待任务执行完成并获取命令输出（JSON：task_id/command/status/output）
- op=kill   杀死后台命令的所有进程树（killpg + /proc 递归补杀后代）
- op=stdin  向后台命令的 stdin 发送文本输入（text 参数，newline 可选是否追加换行）
- op=keys   向后台命令发送光标/键盘消息（跨平台 ANSI/VT100 转义序列）

键盘消息跨平台说明：VT100/ANSI 转义序列是终端输入的标准语义，被 Linux/
macOS/Android(Termux) 的 PTY 与 Windows 的 ConPTY/Windows Terminal 统一
接受。按键名（如 up/down/ctrl_c）映射为对应字节序列，经 PTY master 或
stdin 管道写入后台进程，不依赖平台特定 API。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from .base import Func, tool_metadata
from .bash import kill_process_tree

logger = logging.getLogger(__name__)

# ── 键盘消息映射表（跨平台 ANSI/VT100） ───────────────────
# VT100/ANSI 转义序列是终端输入的标准语义（ECMA-48 / xterm），
# 在 Linux/macOS/Android(Termux) 的 PTY 和 Windows 的 ConPTY/
# Windows Terminal 中都被统一接受，不依赖平台特定 API。
_KEY_SEQUENCES: dict[str, str] = {
    # 光标键
    "up": "\x1b[A",
    "down": "\x1b[B",
    "right": "\x1b[C",
    "left": "\x1b[D",
    # 编辑键
    "home": "\x1b[H",
    "end": "\x1b[F",
    "page_up": "\x1b[5~",
    "page_down": "\x1b[6~",
    "insert": "\x1b[2~",
    "delete": "\x1b[3~",
    "backspace": "\x7f",   # DEL（多数终端 Backspace 发送 DEL）
    "tab": "\t",
    "enter": "\r",
    "escape": "\x1b",
    "space": " ",
    # 功能键（F1-F4 用 SS3 前缀，F5-F12 用 CSI 前缀）
    "f1": "\x1bOP",
    "f2": "\x1bOQ",
    "f3": "\x1bOR",
    "f4": "\x1bOS",
    "f5": "\x1b[15~",
    "f6": "\x1b[17~",
    "f7": "\x1b[18~",
    "f8": "\x1b[19~",
    "f9": "\x1b[20~",
    "f10": "\x1b[21~",
    "f11": "\x1b[23~",
    "f12": "\x1b[24~",
}

# 常用控制组合（ctrl_a..ctrl_z = 0x01..0x1A，其余程序化生成）
_CTRL_KEYS: dict[str, str] = {
    "ctrl_c": "\x03",   # 中断（SIGINT）
    "ctrl_d": "\x04",   # EOF（退出输入）
    "ctrl_z": "\x1a",   # 挂起（SIGTSTP）
    "ctrl_l": "\x0c",   # 清屏（clear）
    "ctrl_r": "\x12",   # 反向搜索历史
    "ctrl_u": "\x15",   # 删除光标到行首
    "ctrl_w": "\x17",   # 删除前一个词
}


def _resolve_key(key: str) -> str | None:
    """将按键名解析为终端输入字节序列（ANSI/VT100，跨平台）。

    支持：
      - 光标键：up / down / left / right
      - 编辑键：home / end / page_up / page_down / insert / delete /
        backspace / tab / enter / escape / space
      - 功能键：f1 - f12
      - 控制组合：ctrl_a .. ctrl_z、ctrl_c / ctrl_d / ctrl_z 等

    按键名不区分大小写，下划线与连字符等价（ctrl_c == ctrl-c）。
    未知按键返回 None。
    """
    normalized = key.strip().lower().replace("-", "_")
    if normalized in _KEY_SEQUENCES:
        return _KEY_SEQUENCES[normalized]
    if normalized in _CTRL_KEYS:
        return _CTRL_KEYS[normalized]
    # 程序化生成 ctrl_<letter>（0x01..0x1A）
    if normalized.startswith("ctrl_"):
        letter = normalized[len("ctrl_"):]
        if len(letter) == 1 and "a" <= letter <= "z":
            return chr(ord(letter) - ord("a") + 1)
    return None


async def _write_pty_all(fd: int, data: bytes) -> None:
    """向 PTY master 写入全部数据，处理非阻塞 EAGAIN（缓冲区满时短暂重试）。

    PTY master 被包装进 asyncio 读管道后处于非阻塞模式；子进程不读取时
    写缓冲区可能短暂占满，os.write 抛 BlockingIOError，这里等待后重试
    直至写完。写入失败（fd 关闭 / EIO 等）抛 OSError 由调用方处理。
    """
    view = memoryview(data)
    total = 0
    while total < len(view):
        try:
            written = os.write(fd, view[total:])
        except BlockingIOError:
            await asyncio.sleep(0.01)
            continue
        total += written


@tool_metadata(
    parallel_safe=False,
    requires_network=False,
    requires_terminal=False,
    timeout_estimate=0,
    category="general",
    priority=30,
    tool_category="bash",
    description="操作后台bash任务",
)
class BashTaskFunc(Func):
    """按 task_id 操作后台 bash 任务（bash background=True 启动）。"""

    name = "bash_task"
    _DEFAULT_WAIT_TIMEOUT: int = 300

    @classmethod
    def to_tool_schema(cls):
        return {
            "type": "function",
            "function": {
                "name": "bash_task",
                "description": (
                    "按 task_id 操作后台 bash 任务（由 bash background=true 启动）。"
                    "op：wait（等待完成取输出，timeout 秒，默认 300/0 无限）、"
                    "kill（杀进程树）、stdin（发文本，需 text）、keys（发按键，需 key）。"
                    "task_id 必须是当前对话 bash 后台返回的 bg-xxx。返回：操作结果 JSON 或输出；失败以 ( 开头。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": (
                                "后台 bash 任务的 task_id（bash background=True 返回的 "
                                "'bg-xxx' 格式 ID）。"
                            ),
                        },
                        "op": {
                            "type": "string",
                            "enum": ["wait", "kill", "stdin", "keys"],
                            "description": (
                                "要执行的操作："
                                "\n- wait：等待任务完成并获取命令输出"
                                "\n- kill：杀死任务所有进程树"
                                "\n- stdin：向任务 stdin 发送文本输入（需 text）"
                                "\n- keys：向任务发送光标/键盘消息（需 key）"
                            ),
                        },
                        "timeout": {
                            "type": "number",
                            "description": (
                                "仅 wait 操作生效：等待完成的超时秒数（默认 300；"
                                "传 0 表示无限等待）。支持小数（如 0.5）。"
                                "超时后任务继续运行，可再次等待或 kill。"
                            ),
                        },
                        "text": {
                            "type": "string",
                            "description": (
                                "仅 stdin 操作必填：要发送到后台命令 stdin 的文本内容，"
                                "按 UTF-8 编码发送（不经过 shell 解释）。"
                            ),
                        },
                        "newline": {
                            "type": "boolean",
                            "description": (
                                "仅 stdin 操作：是否在文本末尾追加换行（默认 true，"
                                "即按「输入一行」语义发送）。传 false 发送原始文本不加换行。"
                            ),
                            "default": True,
                        },
                        "key": {
                            "type": "string",
                            "description": (
                                "仅 keys 操作必填：要发送的按键名（跨平台 ANSI/VT100 序列）。"
                                "支持：up/down/left/right、home/end/page_up/page_down/"
                                "insert/delete/backspace/tab/enter/escape/space、"
                                "f1-f12、ctrl_a-ctrl_z（含 ctrl_c/ctrl_d/ctrl_z/ctrl_l 等）。"
                            ),
                        },
                    },
                    "required": ["task_id", "op"],
                },
            },
        }

    @classmethod
    def display_params(cls, arguments: dict, max_len: int = 80) -> str:
        task_id = arguments.get("task_id", "")
        op = arguments.get("op", "")
        extra = ""
        if op == "stdin":
            extra = str(arguments.get("text", ""))
        elif op == "keys":
            extra = str(arguments.get("key", ""))
        display = f"{op} {task_id}"
        if extra:
            display += f" {cls._sanitize_display(extra)}"
        return f"'{display}'"

    def __init__(self, task_id: str, op: str, timeout=None,
                 text: str | None = None, newline: bool = True,
                 key: str | None = None):
        super().__init__()
        self.task_id = task_id
        self.op = op
        # timeout 仅对 wait 生效：省略/None → 300s；<=0 → 无限等待
        # 使用 float 保留小数（如 0.5 秒短超时），避免 int() 截断
        if timeout is None:
            self.timeout = self._DEFAULT_WAIT_TIMEOUT
        else:
            try:
                timeout = float(timeout)
            except (TypeError, ValueError):
                timeout = self._DEFAULT_WAIT_TIMEOUT
            self.timeout = None if timeout <= 0 else timeout
        self.text = text
        self.newline = bool(newline)
        self.key = key

    # ── execute ──────────────────────────────────────────

    async def execute(self) -> str:
        """按 task_id 和 op 操作后台 bash 任务，返回结果字符串。"""
        agent = getattr(self, 'agent', None)
        if agent is None or not hasattr(agent, '_background_tasks'):
            return "(后台任务操作需要关联 Agent 上下文，当前未关联)"

        rec = agent._background_tasks.get(self.task_id)
        if rec is None:
            return (f"(后台任务不存在: {self.task_id}。"
                    f"请先用 bash background=True 启动后台任务获取 task_id)")

        # ★ 标记为 bash_task 管理：该任务的结果由大模型通过本工具主动获取
        #   （wait 拿到输出 / kill 终止 / stdin / keys 交互），后续
        #   _process_background_tasks 不再把结果作为用户消息自动插入，
        #   也不自动等待其完成（避免交互任务阻塞对话轮次）。
        rec["managed_by_tool"] = True

        if self.op == "wait":
            return await self._op_wait(agent, rec)
        if self.op == "kill":
            return await self._op_kill(agent, rec)
        if self.op == "stdin":
            return await self._op_stdin(rec)
        if self.op == "keys":
            return await self._op_keys(rec)
        return f"(未知操作: {self.op}。支持: wait/kill/stdin/keys)"

    # ── op=wait ──────────────────────────────────────────

    async def _op_wait(self, agent, rec: dict) -> str:
        """等待任务完成并返回输出（JSON：task_id/command/status/output）。

        完成（或已完成后）把任务记录从 tasklist 移除——大模型已通过本工具
        拿到输出，避免 _process_background_tasks 再以用户消息重复插入。

        ★ 使用 asyncio.wait 而非 wait_for：wait_for 超时会 cancel 后台任务
        本身（任务被误杀），wait 只观察不干预，超时后任务继续运行。
        """
        task = rec.get("task")
        if not rec.get("done") and task is not None:
            try:
                if self.timeout:
                    done, _pending = await asyncio.wait({task}, timeout=self.timeout)
                    if not done:
                        return (f"(等待后台任务 {self.task_id} 超时（{self.timeout} 秒），"
                                f"任务仍在运行。可再次 wait 或 op=kill 终止)")
                else:
                    await task
            except asyncio.CancelledError:
                return f"(等待后台任务 {self.task_id} 被取消)"
            except Exception as e:
                logger.debug("后台任务 wait 异常: %s", e)

        # 读取最终结果（任务完成后由 _run_background_task 写入 rec）
        result = rec.get("result", "")
        status = rec.get("status", "completed")
        payload = {
            "task_id": self.task_id,
            "command": rec.get("command", ""),
            "status": status,
            "output": result,
        }
        # 移除任务记录（避免 _process_background_tasks 重复插入用户消息）
        if hasattr(agent, "_remove_background_task"):
            agent._remove_background_task(self.task_id)
        else:
            agent._background_tasks.pop(self.task_id, None)
        return json.dumps(payload, ensure_ascii=False)

    # ── op=kill ──────────────────────────────────────────

    async def _op_kill(self, agent, rec: dict) -> str:
        """杀死后台任务的所有进程树并取消后台任务，从 tasklist 移除。"""
        pid = rec.get("pid")
        process = rec.get("process")
        task = rec.get("task")

        # 1. 杀死进程树（killpg 进程组 + /proc 递归补杀后代）
        if pid is not None:
            try:
                kill_process_tree(pid)
            except Exception as e:
                logger.debug("kill 进程树异常: %s", e)
        elif process is not None:
            try:
                process.kill()
            except ProcessLookupError:
                pass  # 进程已退出
            except Exception as e:
                logger.debug("process.kill 异常: %s", e)

        # 2. 取消 asyncio 后台任务（若仍在运行）
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait({task}, timeout=2.0)
            except Exception:
                pass  # 任务取消过程异常忽略

        # 3. 移除任务记录并更新 TUI 计数
        if hasattr(agent, "_remove_background_task"):
            agent._remove_background_task(self.task_id)
        else:
            agent._background_tasks.pop(self.task_id, None)
        return f"(已杀死后台任务 {self.task_id} 及其所有进程树)"

    # ── op=stdin ─────────────────────────────────────────

    async def _op_stdin(self, rec: dict) -> str:
        """向后台任务 stdin 发送文本输入。"""
        if self.text is None:
            return "(stdin 操作需要 text 参数指定要发送的文本)"
        data = self.text
        if self.newline:
            data += "\n"
        ok, err = await self._write_to_task(rec, data.encode("utf-8"))
        if not ok:
            return err
        return f"(已向后台任务 {self.task_id} 发送 stdin 输入: {self._sanitize_display(self.text)})"

    # ── op=keys ──────────────────────────────────────────

    async def _op_keys(self, rec: dict) -> str:
        """向后台任务发送光标/键盘消息（跨平台 ANSI/VT100 转义序列）。"""
        if self.key is None:
            return "(keys 操作需要 key 参数指定按键，如 key='up' / key='ctrl_c')"
        seq = _resolve_key(self.key)
        if seq is None:
            supported = sorted(_KEY_SEQUENCES.keys()) + ["ctrl_a..ctrl_z"]
            return (f"(未知按键: {self.key}。支持: {', '.join(supported)})")
        ok, err = await self._write_to_task(rec, seq.encode("utf-8"))
        if not ok:
            return err
        return f"(已向后台任务 {self.task_id} 发送按键: {self.key})"

    # ── 写入辅助 ─────────────────────────────────────────

    async def _write_to_task(self, rec: dict, data: bytes) -> tuple[bool, str]:
        """向后台任务写入字节（PTY master 或 PIPE stdin），返回 (成功, 错误消息)。"""
        mode = rec.get("mode")
        if mode is None:
            return (False,
                    f"(后台任务 {self.task_id} 尚未就绪（进程句柄未建立），"
                    f"请稍后重试)")
        lock = rec.get("io_lock")
        if lock is not None:
            async with lock:
                return await self._write_unlocked(rec, data, mode)
        return await self._write_unlocked(rec, data, mode)

    async def _write_unlocked(self, rec: dict, data: bytes, mode: str) -> tuple[bool, str]:
        """在 io_lock 保护下实际写入字节。"""
        try:
            if mode == "pty":
                master_fd = rec.get("master_fd")
                if master_fd is None:
                    return (False,
                            f"(后台任务 {self.task_id} 无 PTY 句柄，进程可能已结束)")
                await _write_pty_all(master_fd, data)
            elif mode == "pipe":
                stdin = rec.get("stdin_writer")
                if stdin is None:
                    return (False,
                            f"(后台任务 {self.task_id} 无 stdin 管道，进程可能已结束)")
                stdin.write(data)
                try:
                    await stdin.drain()
                except (ConnectionResetError, BrokenPipeError):
                    return (False,
                            f"(后台任务 {self.task_id} 的 stdin 管道已关闭，进程可能已结束)")
            else:
                return (False, f"(后台任务 {self.task_id} 写入模式异常: {mode})")
        except (OSError, ValueError, RuntimeError) as e:
            return (False, f"(写入后台任务 {self.task_id} 失败: {e})")
        return (True, "")
