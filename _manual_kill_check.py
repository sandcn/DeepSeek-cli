"""手工实操：bash_opt op=kill 在原生 Windows 上杀整棵进程树（本次修复验收）。

真实调用链：BashFunc(background=True) → 后台任务记录（pid / pid_create_time）
→ BashOptFunc(op=kill) → kill_process_tree（Toolhelp32 收集 + TerminateProcess
+ 杀后校验）。两个场景：

  A. 根 shell 已退出、其启动的子进程仍存活（孤儿）——修复前 taskkill 找不到
     根进程，后代全部残留（用户报障场景）；
  B. 后台任务仍在运行（root → cmd → ping 三层）——全部被杀且校验无残留。
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import subprocess
import sys
import time

from src.core.base_agent import _parse_bash_result_fields
from src.tools import _win_proc
from src.tools._bash_support import _collect_tree_pids, _pid_alive
from src.tools.bash import BashFunc
from src.tools.bash_opt import BashOptFunc


def job_selftest():
    """Job 对象自检：创建 → Assign → 活跃计数 → Terminate。"""
    print("=== Job 自检 ===")
    current = _win_proc._load_kernel32()
    in_job = ctypes.c_int(0)
    current.IsProcessInJob(ctypes.c_void_p(-1), None, ctypes.byref(in_job))
    print("current_process_in_job:", in_job.value)
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    job = _win_proc.create_job()
    print("job:", job)
    assigned = _win_proc.assign_process_to_job(job, proc.pid)
    print("assign:", assigned, "last_error:", ctypes.get_last_error())
    time.sleep(0.3)
    print("active:", _win_proc.job_active_processes(job))
    print("terminate:", _win_proc.terminate_job(job))
    time.sleep(0.3)
    print("active_after:", _win_proc.job_active_processes(job),
          "alive:", _win_proc.is_process_alive(proc.pid))
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    _win_proc.close_handle(job)



class _Agent:
    """最小 Agent 桩：提供 bash 后台任务表与注册 / 完成 / 移除方法。"""

    def __init__(self):
        self._background_tasks = {}

    def _publish_background_task_event(self):
        pass

    def _register_background_task(self, tid, rec):
        self._background_tasks[tid] = rec

    def _complete_background_task(self, tid, result):
        rec = self._background_tasks.get(tid)
        if rec is None:
            return
        rec["result"] = result
        rec["stdout"], rec["stderr"], rec["returncode"] = _parse_bash_result_fields(result)
        rec["status"] = "completed"
        rec["done"] = True

    def _remove_background_task(self, tid):
        return self._background_tasks.pop(tid, None)


async def _wait_done(agent, tid, timeout=15.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        rec = agent._background_tasks.get(tid) or {}
        if rec.get("done"):
            return rec
        await asyncio.sleep(0.1)
    return agent._background_tasks.get(tid) or {}


async def _kill(agent, tid):
    opt = BashOptFunc(task_id=tid, op="kill")
    opt.set_agent(agent)
    return await opt.execute()


async def scenario_a():
    print("\n=== 场景 A：根 shell 已退出、子进程仍存活（修复前失效） ===")
    agent = _Agent()
    # 根 Python 进程启动子进程（孙进程 sleep 60）后立即退出：
    #   - 子进程 stdout/stderr 指向 DEVNULL，不继承后台任务的输出管道
    #     （否则管道写端被孙进程持有，后台任务要等 60s 才见到 EOF）；
    #   - 根退出后孙进程成为孤儿，Toolhelp32 仍按其 PPID（= 根 pid）定位。
    command = (
        "python -c \"import subprocess,sys; "
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); print(p.pid)\""
    )
    f = BashFunc(command=command, background=True)
    f.set_agent(agent)
    tid = json.loads(await f.execute())["task_id"]
    rec = await _wait_done(agent, tid, timeout=30.0)
    root_pid = rec.get("pid")
    create_time = rec.get("pid_create_time")
    print(f"task={tid} status={rec.get('status')} root_pid={root_pid} "
          f"pid_create_time={create_time}")
    print(f"stdout={rec.get('stdout')!r} stderr={rec.get('stderr')!r} "
          f"rc={rec.get('returncode')}")
    assert rec.get("done"), f"后台任务未完成: {rec.get('status')}"
    child_pid = int((rec.get("stdout") or "").strip().splitlines()[-1])
    print(f"root_alive={_pid_alive(root_pid)} child_pid={child_pid} "
          f"child_alive={_pid_alive(child_pid)}")
    print(f"job_handle={rec.get('job_handle')} "
          f"job_active={_win_proc.job_active_processes(rec.get('job_handle'))}")
    print(f"collected_tree={_collect_tree_pids(root_pid)}")
    report = await _kill(agent, tid)
    print("kill ->", report)
    print(f"after: root_alive={_pid_alive(root_pid)} child_alive={_pid_alive(child_pid)}")
    assert _pid_alive(child_pid) is False, "孤儿子进程未被杀死"
    assert "无残留进程" in report, report


async def scenario_b():
    print("\n=== 场景 B：后台任务仍在运行（root → cmd → ping 三层） ===")
    agent = _Agent()
    f = BashFunc(command="cmd /c ping -n 60 127.0.0.1", background=True)
    f.set_agent(agent)
    tid = json.loads(await f.execute())["task_id"]
    await asyncio.sleep(1.5)
    rec = agent._background_tasks.get(tid) or {}
    root_pid = rec.get("pid")
    tree = _collect_tree_pids(root_pid)
    print(f"task={tid} root_pid={root_pid} pid_create_time={rec.get('pid_create_time')}")
    print(f"tree={tree}")
    assert len(tree) >= 3, f"应收集到 root + cmd + ping 三层: {tree}"
    report = await _kill(agent, tid)
    print("kill ->", report)
    alive = [pid for pid in tree if _pid_alive(pid)]
    print(f"after: alive={alive}")
    assert not alive, f"仍有进程存活: {alive}"
    assert "无残留进程" in report, report
    print("wait ->", (await asyncio.wait_for(_wait_finished(agent, tid), 5))[:120])


async def _wait_finished(agent, tid):
    opt = BashOptFunc(task_id=tid, op="read")
    opt.set_agent(agent)
    return await opt.execute()


async def main():
    job_selftest()
    await scenario_a()
    await scenario_b()
    print("\n全部实操断言通过")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
