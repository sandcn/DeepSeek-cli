"""Termux/Android 浏览器交互工具模块。

提供 Termux 环境下的自动打开/关闭浏览器功能。
仅在 TERMUX_VERSION 环境变量存在时生效。
"""

from __future__ import annotations

import asyncio
import logging
import os

_logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# 浏览器包名常量
# ═══════════════════════════════════════════════════════════════

# ★ 预置浏览器包名列表（主用）：覆盖主流浏览器
_BROWSER_PKGS = [
    'com.android.chrome',          # Chrome
    'org.mozilla.firefox',         # Firefox
    'org.mozilla.firefox_beta',    # Firefox Beta
    'com.brave.browser',           # Brave
    'com.opera.browser',           # Opera
    'com.opera.mini.native',       # Opera Mini
    'com.microsoft.emmx',          # Edge
    'com.kiwibrowser.browser',     # Kiwi
    'com.vivaldi.browser',         # Vivaldi
    'com.android.browser',         # 系统浏览器（旧；Android 12+ 已废弃，保留以兼容旧设备）
    'com.google.bbagent',          # 部分 Lenovo/华为默认浏览器
]
# ★ 浏览器包名发现：预置列表未关闭任何浏览器时，用 `pm list packages`
#   动态搜索含 browser/chrome/firefox 等关键字的应用包。
# ★ 浏览器包名动态发现关键词：仅保留浏览器/引擎相关关键词，
#   移除 'internet', 'huawei', 'miui', 'samsung', 'oppo', 'vivo', 'xiaomi'
#   等品牌/通用关键词，避免误杀非浏览器应用（如系统设置、主题商店等）。
_BROWSER_KEYWORDS = frozenset(['browser', 'chrome', 'firefox', 'webview'])

# ★ `am force-stop` 每个子进程的超时时间（秒），防止被系统调用阻塞
_AM_TIMEOUT = 5.0


# ═══════════════════════════════════════════════════════════════
# 私有辅助
# ═══════════════════════════════════════════════════════════════

async def _discover_browser_packages() -> list[str] | None:
    """通过 `pm list packages` 动态发现已安装的浏览器包。

    仅在预置列表全部无法关闭时调用，作为降级方案。
    """
    if not os.environ.get('TERMUX_VERSION'):
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            'pm', 'list', 'packages',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        lines = stdout.decode().splitlines()
        discovered = []
        for line in lines:
            pkg = line.replace('package:', '', 1).strip()
            pkg_lower = pkg.lower()
            if any(kw in pkg_lower for kw in _BROWSER_KEYWORDS):
                discovered.append(pkg)
        return discovered if discovered else None
    except Exception:
        _logger.debug("动态发现浏览器包失败", exc_info=True)
        return None


# ═══════════════════════════════════════════════════════════════
# 公开 API
# ═══════════════════════════════════════════════════════════════

async def auto_open_browser(url: str) -> None:
    """在 Termux 环境中自动用浏览器打开 URL。

    等待 5 秒确保 WebSocket 服务器就绪后，通过 termux-open-url 打开。
    非 Termux 环境直接返回，无操作。

    Args:
        url: 要打开的完整 URL（如 http://localhost:8080）
    """
    if not os.environ.get('TERMUX_VERSION'):
        return
    try:
        await asyncio.sleep(5)
        proc = await asyncio.create_subprocess_exec(
            'termux-open-url', url,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        _logger.info("已在浏览器中打开: %s", url)
    except FileNotFoundError:
        _logger.warning("termux-open-url 命令不可用，请手动打开浏览器访问 %s", url)
    except Exception as e:
        _logger.warning("自动打开浏览器失败: %s", e)


async def close_browsers() -> None:
    """在 Termux 环境中关闭所有已打开的浏览器。

    先尝试预置包名列表（_BROWSER_PKGS），全部失败时通过
    pm list packages 动态发现浏览器包。非 Termux 环境直接返回。

    使用 `am force-stop` 逐个关闭，单个超时 _AM_TIMEOUT 秒。
    """
    if not os.environ.get('TERMUX_VERSION'):
        return

    _closed_any = False
    for pkg in _BROWSER_PKGS:
        try:
            proc = await asyncio.create_subprocess_exec(
                'am', 'force-stop', pkg,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            rc = await asyncio.wait_for(proc.wait(), timeout=_AM_TIMEOUT)
            if rc == 0:
                _logger.info("已关闭浏览器: %s", pkg)
                _closed_any = True
                break
        except asyncio.TimeoutError:
            _logger.warning("关闭浏览器 %s 超时，继续下一项", pkg)
            try:
                proc.kill()
            except Exception:
                _logger.debug("关闭浏览器 %s 的 proc.kill 失败（非关键）", pkg)
        except FileNotFoundError:
            _logger.debug("am force-stop %s 命令不可用（非关键）", pkg)
        except Exception:
            _logger.debug("关闭浏览器 %s 异常（非关键）", pkg, exc_info=True)

    if not _closed_any:
        discovered = await _discover_browser_packages()
        if discovered:
            for pkg in discovered:
                try:
                    proc = await asyncio.create_subprocess_exec(
                        'am', 'force-stop', pkg,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await asyncio.wait_for(proc.wait(), timeout=_AM_TIMEOUT)
                    _logger.info("已关闭浏览器(动态发现): %s", pkg)
                except asyncio.TimeoutError:
                    _logger.warning("关闭浏览器(动态发现) %s 超时，继续", pkg)
                    try:
                        proc.kill()
                    except Exception:
                        _logger.debug("关闭浏览器(动态发现) %s 的 proc.kill 失败（非关键）", pkg)
                except Exception:
                    _logger.debug("关闭浏览器(动态发现) %s 异常（非关键）", pkg, exc_info=True)
