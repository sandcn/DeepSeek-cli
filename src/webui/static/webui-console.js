/* ═══════════════════════════════════════════════════════════════
   webui-console.js — 调试控制台
   拦截 console.log/warn/error/info，在浮层面板中展示，
   支持复制全部日志和清空。
   ═══════════════════════════════════════════════════════════════ */

(function() {
  'use strict';

  /* ── 配置 ──────────────────────────────────────────────── */
  const MAX_LOGS = 5000;        // 最多保留条数，防止内存爆
  const LEVEL_STYLES = {
    log:   { badge: 'LOG',   cls: 'log',   color: '#8ab4f8' },
    info:  { badge: 'INFO',  cls: 'info',  color: '#8ab4f8' },
    warn:  { badge: 'WARN',  cls: 'warn',  color: '#fdd835' },
    error: { badge: 'ERROR', cls: 'error', color: '#f28b82' },
    debug: { badge: 'DEBUG', cls: 'debug', color: '#81c995' },
  };

  /* ── 拦截原始 console 方法 ────────────────────────────── */
  const _orig = {
    log:   console.log.bind(console),
    info:  console.info.bind(console),
    warn:  console.warn.bind(console),
    error: console.error.bind(console),
    debug: console.debug.bind(console),
  };

  /** 日志存储 */
  const _logs = [];

  /** DOM 元素缓存（找到后设置） */
  let _panelEl = null;
  let _logAreaEl = null;
  let _btnEl = null;
  let _isVisible = false;
  let _renderPending = false;

  /** 判断是否在控制台初始化之前已有的日志（history），
   *  初始化完成后设为 false，新日志才实时渲染。 */
  let _initComplete = false;

  /* ── 添加一条日志 ────────────────────────────────────── */
  function _addLog(level, args) {
    const now = new Date();
    const time = now.toLocaleTimeString('zh-CN', { hour12: false })
      + '.' + String(now.getMilliseconds()).padStart(3, '0');

    // 序列化参数
    const text = Array.from(args).map(arg => {
      try {
        if (arg === null) return 'null';
        if (arg === undefined) return 'undefined';
        if (typeof arg === 'string') return arg;
        if (arg instanceof Error) return arg.stack || arg.message || String(arg);
        if (typeof arg === 'object') {
          const s = JSON.stringify(arg, null, 2);
          return s === '{}' && arg.constructor && arg.constructor.name !== 'Object'
            ? String(arg)
            : (s.length > 500 ? s.slice(0, 500) + '...' : s);
        }
        return String(arg);
      } catch (_) { return String(arg); }
    }).join(' ');

    const entry = { time, level, text };
    _logs.push(entry);

    // 裁剪
    if (_logs.length > MAX_LOGS) {
      _logs.splice(0, _logs.length - MAX_LOGS);
    }

    // 初始化完成后才实时追加到 DOM
    if (_initComplete) {
      _appendLogEntry(entry);
    }
  }

  /* ── 在 DOM 中追加一条日志行 ──────────────────────────── */
  function _appendLogEntry(entry) {
    if (!_logAreaEl) return;
    const style = LEVEL_STYLES[entry.level] || LEVEL_STYLES.log;
    const row = document.createElement('div');
    row.className = 'console-line ' + style.cls;

    const timeSpan = document.createElement('span');
    timeSpan.className = 'console-time';
    timeSpan.textContent = entry.time;

    const badgeSpan = document.createElement('span');
    badgeSpan.className = 'console-badge';
    badgeSpan.textContent = style.badge;
    badgeSpan.style.color = style.color;
    badgeSpan.style.borderColor = style.color;

    const textSpan = document.createElement('span');
    textSpan.className = 'console-text';
    textSpan.textContent = entry.text;

    row.appendChild(timeSpan);
    row.appendChild(badgeSpan);
    row.appendChild(textSpan);
    _logAreaEl.appendChild(row);

    // 自动滚到底部
    _logAreaEl.scrollTop = _logAreaEl.scrollHeight;
  }

  /* ── 批量渲染所有已有日志（初始化时调用） ─────────────── */
  function _renderAllLogs() {
    if (!_logAreaEl) return;
    // 清空占位提示
    _logAreaEl.innerHTML = '';
    for (const entry of _logs) {
      _appendLogEntry(entry);
    }
    _initComplete = true;
  }

  /* ── 复制全部日志 ────────────────────────────────────── */
  function _copyAll() {
    const lines = _logs.map(e => `[${e.time}] [${e.level.toUpperCase()}] ${e.text}`);
    const text = lines.join('\n');
    if (!text) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).catch(() => _fallbackCopy(text));
    } else {
      _fallbackCopy(text);
    }
  }

  function _fallbackCopy(text) {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch (_) {}
    document.body.removeChild(ta);
  }

  /* ── 清空日志 ────────────────────────────────────────── */
  function _clearAll() {
    _logs.length = 0;
    if (_logAreaEl) {
      _logAreaEl.innerHTML = '<div class="console-empty">暂无日志</div>';
    }
  }

  /* ── 面板打开/关闭 ───────────────────────────────────── */
  function _togglePanel() {
    _isVisible = !_isVisible;
    if (_panelEl) {
      _panelEl.style.display = _isVisible ? '' : 'none';
      _panelEl.classList.toggle('hidden', !_isVisible);
    }
    if (_isVisible && _btnEl) {
      _btnEl.classList.add('active');
      // 打开时重新渲染所有日志（可能之前因为面板关闭没渲染）
      _renderAllLogs();
    } else if (_btnEl) {
      _btnEl.classList.remove('active');
    }
  }

  /* ── 初始化：查找 DOM 元素并绑定事件 ──────────────────── */
  function _init() {
    _panelEl = document.getElementById('console-panel');
    _logAreaEl = document.getElementById('console-log-area');
    _btnEl = document.getElementById('console-btn');

    if (!_panelEl || !_logAreaEl || !_btnEl) {
      // DOM 还没渲染好，重试
      setTimeout(_init, 100);
      return;
    }

    // 绑定按钮事件
    _btnEl.addEventListener('click', _togglePanel);

    const closeBtn = document.getElementById('console-close-btn');
    if (closeBtn) closeBtn.addEventListener('click', _togglePanel);

    const copyBtn = document.getElementById('console-copy-btn');
    if (copyBtn) copyBtn.addEventListener('click', _copyAll);

    const clearBtn = document.getElementById('console-clear-btn');
    if (clearBtn) clearBtn.addEventListener('click', _clearAll);

    // 初始隐藏（style + class 双重保障）
    _panelEl.style.display = 'none';
    _panelEl.classList.add('hidden');

    // 渲染初始化之前积压的日志
    _renderAllLogs();
  }

  /* ── 覆盖 console 方法 ────────────────────────────────── */
  console.log   = function() { _addLog('log',   arguments); _orig.log.apply(console, arguments); };
  console.info  = function() { _addLog('info',  arguments); _orig.info.apply(console, arguments); };
  console.warn  = function() { _addLog('warn',  arguments); _orig.warn.apply(console, arguments); };
  console.error = function() { _addLog('error', arguments); _orig.error.apply(console, arguments); };
  console.debug = function() { _addLog('debug', arguments); _orig.debug.apply(console, arguments); };

  // ── DOMContentLoaded 时初始化 ────────────────────────────
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _init);
  } else {
    _init();
  }
})();
