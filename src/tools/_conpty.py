"""Windows ConPTY（伪终端）封装 —— bash 工具 ``pty=true`` 执行路径。

为什么需要：Windows 上 bash 工具默认走 PIPE（stdout/stderr 各自独立收集），
子进程的 stdout 不是终端——C 运行时按全缓冲输出（日志/进度条不实时）、
交互式程序（python REPL、git 交互、``less`` / ``vim`` 等 TUI）检测不到 TTY、
``Ctrl+C`` 只是写进管道的普通字节、不会变成信号。``pty=true`` 改用
Windows 10 1809+ 的 ConPTY（``CreatePseudoConsole``），让子进程获得一个
真正的伪终端：行缓冲、彩色输出、进度条、stdin 按终端语义解析
（交互式程序可读、TUI 可用）。

已知平台限制（2026-10-20 在 Windows 11 26220 的 ConPTY 上复测，结论与旧版
描述不同）：向输入管道写入 ``\\x03`` 时，**部分程序会收到 Ctrl+C**——实测
``python -i -q`` 中运行中的 ``time.sleep(120)`` 被 ``KeyboardInterrupt``
中断；但并非所有程序都响应（``cmd`` 可能只是取消当前输入行，
``GenerateConsoleCtrlEvent`` 对伪控制台进程组发送 ``CTRL_C_EVENT`` 也不可靠）。
因此 Ctrl+C 只能「尽力而为」，不能保证中断所有程序；任务无响应时请用
``bash_opt op=kill``（杀进程树，带杀后校验），不要依赖 Ctrl+C。

另外，PTY **没有真正的 EOF 语义**：``bash_opt op=close_stdin`` 在 PTY 上只是
写入 EOT（ConPTY 为 ``\\x1a`` / POSIX 为 ``\\x04``）模拟按键，程序不一定会退出
（实测 ``python -i -q`` 收到 EOT 后仍继续运行）。依赖 stdin EOF 才输出 / 退出
的命令请用默认 PIPE 模式（``pty=false``）启动任务再 ``close_stdin``；需要结束
PTY 任务请用 ``op=kill``。

代价（由调用方在工具文档中说明）：PTY 只有一个输出流，stdout/stderr
**物理合并**（``stderr`` 恒为空）——这是终端的固有语义，无法在 ConPTY 层
分开；需要分离双流的命令应使用默认的 PIPE 模式。

实现要点：

  - ``CreatePipe`` 建两条匿名管道（输入 / 输出）；
  - ``CreatePseudoConsole`` 用「输入读端 + 输出写端」建立伪终端，得到 HPCON；
  - ``CreateProcessW`` 带 ``STARTUPINFOEX`` + ``PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE``
    启动子进程（``asyncio.create_subprocess_*`` 无法传该属性，因此本模块
    自行调用 Win32 API）；
  - 独立线程用阻塞 ``ReadFile`` 读输出，``loop.call_soon_threadsafe`` 喂给
    ``asyncio.StreamReader``（Windows 的 ProactorEventLoop 不支持把匿名管道
    直接交给 ``connect_read_pipe``）；
  - 独立线程 ``WaitForSingleObject`` 等进程退出 → ``ClosePseudoConsole``
    （关闭输出侧，让读线程收到 EOF）并记录退出码；
  - 写端经 ``msvcrt.open_osfhandle`` 转成 CRT fd，交给现有 ``os.write``
    路径（``bash_opt`` 的 stdin / keys 复用同一句柄）。

平台边界：只服务**原生 Windows**。非 Windows 或系统没有 ConPTY API
（Windows 10 1809 之前）时 :func:`is_available` 返回 False，调用方回退 PIPE。
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import subprocess
import threading

try:
    import msvcrt
except ImportError:  # pragma: no cover - 非原生 Windows（Cygwin/MSYS2 msys/POSIX）
    # ★ 不得在模块级硬导入：``bash`` 工具无条件 ``from . import _conpty``，
    #   没有 msvcrt 的解释器上整个工具包导入即失败，应用启动直接报
    #   「❌ 致命错误: No module named 'msvcrt'」。本模块的平台边界由
    #   ``_load_kernel32`` / ``is_available`` 判定，msvcrt 仅在原生 Windows
    #   的 spawn 路径（句柄转 CRT fd）使用。
    msvcrt = None

logger = logging.getLogger(__name__)

# ── Win32 常量 ──────────────────────────────────────────
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
_INFINITE = 0xFFFFFFFF

#: 读线程单次读取块大小（与 bash 工具 ``_READ_CHUNK_SIZE`` 同量级）
_READ_CHUNK = 64 * 1024


class COORD(ctypes.Structure):
    """``COORD``（ConPTY 的字符单元格尺寸）。"""

    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class STARTUPINFOW(ctypes.Structure):
    """``STARTUPINFOW``（``STARTUPINFOEXW`` 的内嵌部分）。"""

    _fields_ = [
        ("cb", ctypes.c_uint32),
        ("lpReserved", ctypes.c_wchar_p),
        ("lpDesktop", ctypes.c_wchar_p),
        ("lpTitle", ctypes.c_wchar_p),
        ("dwX", ctypes.c_uint32),
        ("dwY", ctypes.c_uint32),
        ("dwXSize", ctypes.c_uint32),
        ("dwYSize", ctypes.c_uint32),
        ("dwXCountChars", ctypes.c_uint32),
        ("dwYCountChars", ctypes.c_uint32),
        ("dwFillAttribute", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32),
        ("wShowWindow", ctypes.c_uint16),
        ("cbReserved2", ctypes.c_uint16),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", ctypes.c_void_p),
        ("hStdOutput", ctypes.c_void_p),
        ("hStdError", ctypes.c_void_p),
    ]


class STARTUPINFOEXW(ctypes.Structure):
    """``STARTUPINFOEXW``（``STARTUPINFO`` + 属性列表指针）。"""

    _fields_ = [
        ("StartupInfo", STARTUPINFOW),
        ("lpAttributeList", ctypes.c_void_p),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    """``PROCESS_INFORMATION``（子进程 / 线程句柄与 PID）。"""

    _fields_ = [
        ("hProcess", ctypes.c_void_p),
        ("hThread", ctypes.c_void_p),
        ("dwProcessId", ctypes.c_uint32),
        ("dwThreadId", ctypes.c_uint32),
    ]


class SECURITY_ATTRIBUTES(ctypes.Structure):
    """``SECURITY_ATTRIBUTES``（管道句柄需可继承，ConPTY 才能拿到两端）。"""

    _fields_ = [
        ("nLength", ctypes.c_uint32),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", ctypes.c_int),
    ]


#: ``SetHandleInformation`` 的「清除继承标志」（父进程侧句柄不可被子进程继承，
#: 否则读端被 conhost 持有 → 父进程永远等不到输出 EOF）
_HANDLE_FLAG_INHERIT = 0x00000001

#: ``PROC_THREAD_ATTRIBUTE_HANDLE_LIST``：限制 `bInheritHandles=TRUE` 时**哪些**
#: 句柄被继承。必需——否则父进程可继承的 stdin/stdout/stderr 也会传给子进程，
#: 子进程优先使用继承来的标准句柄，输出不进伪终端（实测：pytest 捕获 stdout
#: 时子进程把输出写回父进程管道，ConPTY 侧读到 0 字节）。
_PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002

#: ``STARTF_USESTDHANDLES``：显式声明标准句柄由 STARTUPINFO 指定
_STARTF_USESTDHANDLES = 0x00000100


#: kernel32 绑定缓存（懒加载；失败后不再重试）
_kernel32 = None
_load_failed = False


def _load_kernel32():
    """懒加载并绑定 ConPTY / 进程创建相关函数签名（不可用返回 None）。"""
    global _kernel32, _load_failed
    if _kernel32 is not None:
        return _kernel32
    if _load_failed or os.name != "nt":
        return None
    try:
        lib = ctypes.WinDLL("kernel32", use_last_error=True)
        lib.CreatePipe.argtypes = (
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p, ctypes.c_uint32,
        )
        lib.CreatePipe.restype = ctypes.c_int
        lib.CreatePseudoConsole.argtypes = (
            COORD, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p),
        )
        lib.CreatePseudoConsole.restype = ctypes.c_long  # HRESULT
        lib.ClosePseudoConsole.argtypes = (ctypes.c_void_p,)
        lib.ClosePseudoConsole.restype = None
        lib.ResizePseudoConsole.argtypes = (ctypes.c_void_p, COORD)
        lib.ResizePseudoConsole.restype = ctypes.c_long
        lib.InitializeProcThreadAttributeList.argtypes = (
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_size_t),
        )
        lib.InitializeProcThreadAttributeList.restype = ctypes.c_int
        lib.UpdateProcThreadAttribute.argtypes = (
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_size_t,
            ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_size_t),
        )
        lib.UpdateProcThreadAttribute.restype = ctypes.c_int
        lib.DeleteProcThreadAttributeList.argtypes = (ctypes.c_void_p,)
        lib.DeleteProcThreadAttributeList.restype = None
        lib.CreateProcessW.argtypes = (
            ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_int, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_wchar_p,
            ctypes.POINTER(STARTUPINFOEXW), ctypes.POINTER(PROCESS_INFORMATION),
        )
        lib.CreateProcessW.restype = ctypes.c_int
        lib.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        lib.WaitForSingleObject.restype = ctypes.c_uint32
        lib.GetExitCodeProcess.argtypes = (
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32),
        )
        lib.GetExitCodeProcess.restype = ctypes.c_int
        lib.TerminateProcess.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        lib.TerminateProcess.restype = ctypes.c_int
        lib.ReadFile.argtypes = (
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p,
        )
        lib.ReadFile.restype = ctypes.c_int
        lib.CloseHandle.argtypes = (ctypes.c_void_p,)
        lib.CloseHandle.restype = ctypes.c_int
        lib.SetHandleInformation.argtypes = (
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
        )
        lib.SetHandleInformation.restype = ctypes.c_int
    except (AttributeError, OSError):
        _load_failed = True
        logger.debug("ConPTY API 绑定失败（非 Windows 或系统过旧）", exc_info=True)
        return None
    _kernel32 = lib
    return lib


def is_available() -> bool:
    """当前环境是否支持 ConPTY（原生 Windows 10 1809+ 且 API 绑定成功）。"""
    return _load_kernel32() is not None


def _environment_block(env: dict | None):
    """把 ``{KEY: VALUE}`` 环境变量构建为 ``CreateProcessW`` 需要的环境块。

    格式：``KEY=VALUE\\0KEY=VALUE\\0...\\0``（UTF-16，双 NUL 结尾）。
    ``env is None`` 时返回 ``None``，子进程继承父进程环境。
    """
    if env is None:
        return None
    items = sorted(env.items(), key=lambda pair: str(pair[0]).upper())
    text = "".join(f"{key}={value}\0" for key, value in items) + "\0"
    return ctypes.create_unicode_buffer(text)


class ConPtyProcess:
    """ConPTY 中的子进程（供 bash 工具执行路径使用的最小进程封装）。

    只实现 bash 执行路径实际用到的接口：``pid`` / ``returncode`` /
    ``wait()`` / ``kill()``；输出通过 :attr:`reader`（``asyncio.StreamReader``）
    读取，输入通过 :attr:`write_fd`（CRT 文件描述符）写入。
    """

    def __init__(self, pid: int, process_handle: int, pcon_state: dict,
                 input_write_handle: int, output_read_handle: int,
                 reader: asyncio.StreamReader, done_event: threading.Event,
                 exit_code_box: dict):
        self._pid = int(pid)
        self._process_handle = int(process_handle)
        #: 伪终端句柄的**共享状态**（``{"handle": HPCON}``）：监督线程在子进程
        #: 退出时关闭它（触发读线程 EOF），close() 也可能关闭它——用同一个
        #: dict 保证只关一次（重复 ClosePseudoConsole 会造成堆损坏）。
        self._pcon_state = pcon_state
        self._input_write_handle = int(input_write_handle)
        self._output_read_handle = int(output_read_handle)
        self._reader = reader
        self._done = done_event
        self._exit_code_box = exit_code_box
        self._exit_code: int | None = None
        self._closed = False
        #: 输入写端（CRT fd；``os.write`` 可写，PTY 会把字节解释为按键）
        self.write_fd: int | None = None
        # msvcrt 只在原生 Windows 存在；spawn() 也只在那里可用（is_available）
        if msvcrt is not None:
            try:
                self.write_fd = msvcrt.open_osfhandle(
                    self._input_write_handle, os.O_WRONLY | os.O_BINARY)
            except OSError:  # pragma: no cover - 句柄异常（极罕见）
                logger.debug("ConPTY 输入句柄转 fd 失败", exc_info=True)
                self.write_fd = None

    @property
    def pid(self) -> int:
        """子进程 PID。"""
        return self._pid

    @property
    def returncode(self) -> int | None:
        """子进程退出码（尚未退出时为 ``None``）。"""
        return self._exit_code

    @property
    def reader(self) -> asyncio.StreamReader:
        """输出流（stdout/stderr 合并）。"""
        return self._reader

    def _read_exit_code(self) -> int | None:
        """直接查 ``GetExitCodeProcess``（监督线程未写入时兜底）。"""
        lib = _load_kernel32()
        if lib is None or not self._process_handle:
            return None
        code = ctypes.c_uint32(0)
        if lib.GetExitCodeProcess(ctypes.c_void_p(self._process_handle),
                                  ctypes.byref(code)):
            return int(code.value)
        return None

    async def wait(self) -> int | None:
        """等待子进程退出，返回退出码（可重复调用）。"""
        if self._exit_code is not None:
            return self._exit_code
        await asyncio.to_thread(self._done.wait)
        if self._exit_code is None:
            code = self._exit_code_box.get("code")
            if code is None:
                code = self._read_exit_code()
            self._exit_code = code
        return self._exit_code

    def kill(self) -> None:
        """强制终止子进程（``TerminateProcess``；已退出则忽略）。"""
        if self._exit_code is not None:
            return
        lib = _load_kernel32()
        if lib is None or not self._process_handle:
            return
        try:
            lib.TerminateProcess(ctypes.c_void_p(self._process_handle), 1)
        except OSError:  # pragma: no cover - 依赖系统调用
            logger.debug("ConPTY 终止进程失败", exc_info=True)

    def close(self) -> None:
        """释放资源（幂等）：关闭写端 fd / 伪终端 / 残留句柄。"""
        if self._closed:
            return
        self._closed = True
        if self.write_fd is not None:
            try:
                os.close(self.write_fd)
            except OSError:  # pragma: no cover - 已关闭
                pass
            self.write_fd = None
        self._close_pcon()
        lib = _load_kernel32()
        if lib is None:
            return
        for handle in (self._output_read_handle, self._process_handle):
            if handle:
                try:
                    lib.CloseHandle(ctypes.c_void_p(handle))
                except OSError:  # pragma: no cover - 依赖系统调用
                    pass
        self._output_read_handle = 0
        self._process_handle = 0

    def _close_pcon(self) -> None:
        """关闭伪终端（幂等，与监督线程共用同一状态，只关一次）。"""
        handle = int(self._pcon_state.get("handle") or 0)
        self._pcon_state["handle"] = 0
        if not handle:
            return
        lib = _load_kernel32()
        if lib is None:
            return
        try:
            lib.ClosePseudoConsole(ctypes.c_void_p(handle))
        except OSError:  # pragma: no cover - 依赖系统调用
            logger.debug("ClosePseudoConsole 失败", exc_info=True)


def spawn(argv: list[str], *, env: dict | None = None, cwd: str | None = None,
          cols: int = 120, rows: int = 30) -> ConPtyProcess:
    """在 ConPTY 中启动 ``argv``，返回 :class:`ConPtyProcess`。

    Args:
        argv: 完整命令行参数（``argv[0]`` 为可执行文件路径）。
        env: 子进程环境变量（``None`` = 继承父进程）。
        cwd: 工作目录（``None`` = 继承父进程当前目录）。
        cols / rows: 伪终端初始尺寸（字符单元格）。

    Raises:
        OSError: 当前平台不支持 ConPTY，或创建管道 / 伪终端 / 子进程失败。

    Returns:
        ConPtyProcess：``reader`` 为输出流，``write_fd`` 为输入写端。
    """
    lib = _load_kernel32()
    if lib is None:
        raise OSError("当前系统不支持 ConPTY（需要 Windows 10 1809+）")
    if not argv:
        raise OSError("ConPTY 启动命令为空")

    loop = asyncio.get_running_loop()
    # ── 1. 两条匿名管道：输入（我们写 → 子进程读）、输出（子进程写 → 我们读）
    # 句柄必须可继承：ConPTY 内部由 conhost 持有这两端，CreateProcess 以
    # bInheritHandles=True 把可继承句柄交给子进程。
    attributes = SECURITY_ATTRIBUTES()
    attributes.nLength = ctypes.sizeof(SECURITY_ATTRIBUTES)
    attributes.lpSecurityDescriptor = None
    attributes.bInheritHandle = True
    input_read = ctypes.c_void_p()
    input_write = ctypes.c_void_p()
    output_read = ctypes.c_void_p()
    output_write = ctypes.c_void_p()
    if not lib.CreatePipe(ctypes.byref(input_read), ctypes.byref(input_write),
                          ctypes.byref(attributes), 0):
        raise OSError(ctypes.get_last_error(), "CreatePipe（输入）失败")
    if not lib.CreatePipe(ctypes.byref(output_read), ctypes.byref(output_write),
                          ctypes.byref(attributes), 0):
        _close_handles(input_read, input_write)
        raise OSError(ctypes.get_last_error(), "CreatePipe（输出）失败")
    # 父进程自己保留的两端不参与继承：否则读端被 conhost/子进程持有，
    # 子进程退出后父进程永远等不到输出 EOF（读取循环会卡住）。
    if not lib.SetHandleInformation(input_write, _HANDLE_FLAG_INHERIT, 0):
        _close_handles(input_read, input_write, output_read, output_write)
        raise OSError(ctypes.get_last_error(), "SetHandleInformation（输入写端）失败")
    if not lib.SetHandleInformation(output_read, _HANDLE_FLAG_INHERIT, 0):
        _close_handles(input_read, input_write, output_read, output_write)
        raise OSError(ctypes.get_last_error(), "SetHandleInformation（输出读端）失败")

    # ── 2. 建立伪终端（ConPTY 自己持有输入读端与输出写端的副本）
    pcon = ctypes.c_void_p()
    result = lib.CreatePseudoConsole(
        COORD(cols, rows), input_read, output_write, 0, ctypes.byref(pcon),
    )
    if result != 0 or not pcon.value:
        _close_handles(input_read, input_write, output_read, output_write)
        raise OSError(f"CreatePseudoConsole 失败（HRESULT=0x{result & 0xFFFFFFFF:X}）")

    # ConPTY 持有两端副本；父进程侧在子进程启动后关闭（见下）。
    # ── 3. 用 STARTUPINFOEX + PSEUDOCONSOLE 属性启动子进程
    attribute_list, attribute_buffers = _build_attribute_list(
        pcon, [int(input_read.value), int(output_write.value)])
    if attribute_list is None:
        lib.ClosePseudoConsole(pcon)
        _close_handles(input_read, input_write, output_read, output_write)
        raise OSError("初始化进程属性列表失败（ConPTY 回调不可用）")

    startup = STARTUPINFOEXW()
    startup.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
    # 显式声明「标准句柄由我指定」且全部为 NULL：否则 CreateProcess 会把父进程
    # （可继承的）std 句柄复制给子进程，子进程优先写父进程的 stdout，输出不进
    # 伪终端（实测 pytest 用 os.dup2 捕获 fd 1 时，子进程的 print 落在父进程的
    # 捕获文件里，ConPTY 侧只能读到终端初始化序列）。hStd* = NULL 时子进程的
    # 标准句柄由 ConPTY（伪控制台）提供。
    startup.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
    startup.StartupInfo.hStdInput = None
    startup.StartupInfo.hStdOutput = None
    startup.StartupInfo.hStdError = None
    startup.lpAttributeList = attribute_list
    process_info = PROCESS_INFORMATION()
    command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
    env_block = _environment_block(env)
    try:
        created = lib.CreateProcessW(
            None,
            command_line,
            ctypes.byref(attributes),
            None,
            True,
            _EXTENDED_STARTUPINFO_PRESENT | _CREATE_UNICODE_ENVIRONMENT,
            ctypes.cast(env_block, ctypes.c_void_p) if env_block is not None else None,
            cwd,
            ctypes.byref(startup),
            ctypes.byref(process_info),
        )
        error = ctypes.get_last_error()
    finally:
        lib.DeleteProcThreadAttributeList(attribute_list)
        del attribute_buffers  # 属性列表用完后缓冲区可释放
    # 子进程已启动（无论成功与否）：ConPTY 已建立自己的引用，父进程侧句柄关闭
    _close_handles(input_read, output_write)
    if not created:
        lib.ClosePseudoConsole(pcon)
        _close_handles(input_write, output_read)
        raise OSError(error, f"CreateProcessW 失败: {argv[0]}")

    # 线程句柄不再需要（进程句柄保留用于等待 / 终止）
    _close_handles(process_info.hThread)

    pid = int(process_info.dwProcessId)
    process_handle = int(process_info.hProcess)
    pcon_value = int(pcon.value)
    # 伪终端句柄共享状态：监督线程与 close() 都可能关闭它，用同一 dict 保证只关一次
    pcon_state = {"handle": pcon_value}
    input_write_value = int(input_write.value)
    output_read_value = int(output_read.value)

    # ── 4. 读线程：阻塞 ReadFile → StreamReader（Proactor 不支持匿名管道直连）
    reader = asyncio.StreamReader()

    def _feed(data: bytes) -> None:
        try:
            reader.feed_data(data)
        except (RuntimeError, AssertionError):  # pragma: no cover - reader 已关闭
            pass

    def _feed_eof() -> None:
        try:
            reader.feed_eof()
        except (RuntimeError, AssertionError):  # pragma: no cover - 同上
            pass

    def _reader_loop() -> None:
        buffer = ctypes.create_string_buffer(_READ_CHUNK)
        read = ctypes.c_uint32(0)
        while True:
            ok = lib.ReadFile(ctypes.c_void_p(output_read_value), buffer,
                              _READ_CHUNK, ctypes.byref(read), None)
            if not ok or not read.value:
                if not ok:
                    logger.debug("ConPTY ReadFile 结束: error=%s",
                                 ctypes.get_last_error())
                break
            data = bytes(buffer.raw[: read.value])
            loop.call_soon_threadsafe(_feed, data)
        loop.call_soon_threadsafe(_feed_eof)

    threading.Thread(target=_reader_loop, name="conpty-reader", daemon=True).start()

    # ── 5. 监督线程：等进程退出 → 关伪终端（触发读线程 EOF）→ 记录退出码
    exit_code_box: dict = {}
    finished = threading.Event()

    def _supervise() -> None:
        try:
            lib.WaitForSingleObject(ctypes.c_void_p(process_handle), _INFINITE)
            code = ctypes.c_uint32(0)
            if lib.GetExitCodeProcess(ctypes.c_void_p(process_handle),
                                      ctypes.byref(code)):
                exit_code_box["code"] = int(code.value)
        finally:
            # 关闭伪终端 → 输出侧 EOF（读线程结束）；与 close() 共用状态，只关一次
            handle = int(pcon_state.get("handle") or 0)
            pcon_state["handle"] = 0
            if handle:
                try:
                    lib.ClosePseudoConsole(ctypes.c_void_p(handle))
                except OSError:  # pragma: no cover - 依赖系统调用
                    pass
            finished.set()

    threading.Thread(target=_supervise, name="conpty-supervisor",
                     daemon=True).start()

    return ConPtyProcess(
        pid=pid,
        process_handle=process_handle,
        pcon_state=pcon_state,
        input_write_handle=input_write_value,
        output_read_handle=output_read_value,
        reader=reader,
        done_event=finished,
        exit_code_box=exit_code_box,
    )


def _build_attribute_list(pcon, inheritable: list[int]):
    """构建进程属性列表：``PSEUDOCONSOLE`` + ``HANDLE_LIST``（白名单继承）。

    Args:
        pcon: ``CreatePseudoConsole`` 返回的 HPCON（c_void_p）。
        inheritable: 允许子进程继承的句柄值（ConPTY 的输入读端 / 输出写端）。

    Returns:
        ``(address, buffers)``；失败时 ``(None, None)``。调用方用完必须
        ``DeleteProcThreadAttributeList(address)`` 并保持 ``buffers`` 存活
        直到删除完成。
    """
    lib = _load_kernel32()
    if lib is None:
        return None, None
    attribute_count = 2
    size = ctypes.c_size_t(0)
    # 第一次调用只取所需缓冲区大小（预期失败：ERROR_INSUFFICIENT_BUFFER）
    lib.InitializeProcThreadAttributeList(None, attribute_count, 0,
                                         ctypes.byref(size))
    if size.value <= 0:
        return None, None
    buffer = ctypes.create_string_buffer(size.value)
    address = ctypes.cast(buffer, ctypes.c_void_p)
    if not lib.InitializeProcThreadAttributeList(address, attribute_count, 0,
                                                ctypes.byref(size)):
        return None, None
    # ★ PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE 的 lpValue 是 **HPCON 值本身**
    #   （不是 &HPCON）——Microsoft 官方示例即 `UpdateProcThreadAttribute(
    #   ..., PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE, hPC, sizeof(hPC), ...)`。
    #   传 &hPC 时属性值会被当成「指向 HPCON 的指针」，ConPTY 连接建立失败：
    #   子进程照常启动、退出码正常，但输出不进入伪终端（实测 ReadFile 立即
    #   ERROR_BROKEN_PIPE、读不到任何字节）。
    handle_value = int(pcon.value if hasattr(pcon, "value") else pcon)
    updated = lib.UpdateProcThreadAttribute(
        address, 0, _PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
        ctypes.c_void_p(handle_value), ctypes.sizeof(ctypes.c_void_p),
        None, None,
    )
    if not updated:
        lib.DeleteProcThreadAttributeList(address)
        return None, None
    handles = (ctypes.c_void_p * len(inheritable))(*[int(v) for v in inheritable])
    updated = lib.UpdateProcThreadAttribute(
        address, 0, _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
        handles, ctypes.sizeof(handles), None, None,
    )
    if not updated:
        lib.DeleteProcThreadAttributeList(address)
        return None, None
    return int(address.value), (buffer, handles)


def _close_handles(*handles) -> None:
    """关闭若干内核句柄（``None`` / 0 静默跳过）。"""
    lib = _load_kernel32()
    if lib is None:
        return
    for handle in handles:
        value = getattr(handle, "value", handle)
        if not value:
            continue
        try:
            lib.CloseHandle(ctypes.c_void_p(int(value)))
        except OSError:  # pragma: no cover - 依赖系统调用
            logger.debug("CloseHandle 失败: %s", value, exc_info=True)


__all__ = [
    "ConPtyProcess",
    "is_available",
    "spawn",
]
