"""诊断：Windows Job 是否覆盖 PowerShell 启动的孙进程。"""

from __future__ import annotations

import subprocess
import sys
import time

from src.tools._bash_support import (
    create_process_job,
    job_active_processes,
    terminate_process_job,
)
from src.tools._win_proc import is_process_alive

PS = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"


def run(code: str, *, job: bool) -> None:
    print(f"\n--- {'Job' if job else '无 Job'} ---")
    proc = subprocess.Popen(
        [PS, "-NoLogo", "-NoProfile", "-Command", code],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    handle = create_process_job(proc.pid) if job else None
    print("job:", handle, "job_active(immediately):", job_active_processes(handle))
    out, err = proc.communicate(timeout=30)
    print("stdout:", out.strip(), "stderr:", err.strip()[:200])
    time.sleep(0.5)
    print("root_alive:", is_process_alive(proc.pid),
          "job_active:", job_active_processes(handle))
    if out.strip().isdigit():
        grandchild = int(out.strip())
        print("grandchild:", grandchild, "alive:", is_process_alive(grandchild))
    print("terminate:", terminate_process_job(handle))
    time.sleep(0.3)
    print("job_active after:", job_active_processes(handle))
    if out.strip().isdigit():
        print("grandchild alive after:",
              is_process_alive(int(out.strip())))


GRANDCHILD = (
    "import subprocess,sys; "
    "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],"
    "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); print(p.pid)"
)

if __name__ == "__main__":
    run(f'python -c "{GRANDCHILD}"', job=True)
    run(f'python -c "{GRANDCHILD}"', job=False)
