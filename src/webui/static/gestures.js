/* ═══════════════════════════════════════════════════════════════
   gestures.js — Web UI 移动端手势支持
   提供：下拉刷新（重新连接 WebSocket）、单指右滑返回上级
   依赖: ws-client.js (window.ws)
   ═══════════════════════════════════════════════════════════════ */

(function() {
  'use strict';

  /**
   * 初始化移动端手势控制
   * 在 DOMContentLoaded 就绪后自动执行
   */
  function _initGestures() {
    /* ── 1. 注入手势 Toast 样式（通过 JS，不修改现有 CSS） ── */
    var style = document.createElement('style');
    style.textContent =
      '#gesture-toast{' +
        'position:fixed;top:0;left:50%;transform:translateX(-50%);' +
        'z-index:9999;padding:10px 24px;border-radius:20px;' +
        'background:rgba(0,0,0,0.7);color:#fff;font-size:14px;' +
        'font-weight:500;pointer-events:none;user-select:none;' +
        'opacity:0;transition:opacity 0.3s ease;' +
      '}' +
      '#gesture-toast.pull{opacity:1;}' +
      '#gesture-toast.release{opacity:1;background:rgba(0,0,0,0.85);}' +
      '#gesture-toast.refreshing{opacity:1;background:rgba(33,150,243,0.85);}';
    document.head.appendChild(style);

    /* ── 2. 创建 Toast 元素 ── */
    var toast = document.createElement('div');
    toast.id = 'gesture-toast';
    toast.textContent = '下拉刷新…';
    document.body.appendChild(toast);

    /* ── 3. 触控状态 ── */
    var startY = 0;
    var startX = 0;
    var isPulling = false;
    var isRefreshing = false;
    var pullDistance = 0;

    var messagesEl = document.getElementById('messages');
    if (!messagesEl) return;

    var PULL_THRESHOLD = 80;
    var SWIPE_THRESHOLD = 80;

    /** 更新 Toast 文本和状态类 */
    function _updateToast(text, className) {
      toast.textContent = text;
      toast.className = className || '';
    }

    /** 触发下拉刷新 */
    function _startRefresh() {
      if (isRefreshing) return;
      isRefreshing = true;
      _updateToast('刷新中…', 'refreshing');

      // 触觉反馈（可选，失败时静默忽略）
      try { navigator.vibrate(50); } catch (_) { /* ignore */ }

      // 发送重新获取完整状态请求
      if (window.ws && typeof window.ws.send === 'function') {
        window.ws.send({ type: 'get_full_state' });
      }

      // 2 秒后自动隐藏 Toast
      setTimeout(function() {
        _updateToast('', '');
        isRefreshing = false;
        isPulling = false;
        pullDistance = 0;
      }, 2000);
    }

    /* ── 4. touchstart：记录起始位置 ── */
    messagesEl.addEventListener('touchstart', function(e) {
      if (isRefreshing) return;
      var touch = e.touches[0];
      startY = touch.clientY;
      startX = touch.clientX;
      isPulling = false;
      pullDistance = 0;
    }, { passive: true });

    /* ── 5. touchmove：下拉跟踪 ── */
    messagesEl.addEventListener('touchmove', function(e) {
      if (isRefreshing) return;
      var touch = e.touches[0];
      var deltaY = touch.clientY - startY;

      // ★ 仅当滚动到顶部（scrollTop === 0）时才启用下拉手势，
      //    避免与页面原有纵向滚动冲突。
      if (messagesEl.scrollTop === 0 && deltaY > 0) {
        isPulling = true;
        pullDistance = deltaY;
        _updateToast(
          deltaY >= PULL_THRESHOLD ? '释放刷新' : '下拉刷新…',
          deltaY >= PULL_THRESHOLD ? 'release' : 'pull'
        );
      }
    }, { passive: true });

    /* ── 6. touchend：触发刷新 / 右滑返回 ── */
    messagesEl.addEventListener('touchend', function(e) {
      if (isRefreshing) return;

      // ── 下拉刷新判定 ──
      if (isPulling && pullDistance >= PULL_THRESHOLD) {
        _startRefresh();
        return;
      }

      // ── 单指右滑返回上级 ──
      var touch = e.changedTouches[0];
      var deltaX = touch.clientX - startX;
      var deltaY = touch.clientY - startY;

      // 水平右滑超过阈值，且垂直偏移在 ±50px 以内（避免误触）
      if (deltaX >= SWIPE_THRESHOLD && Math.abs(deltaY) < 50) {
        try { navigator.vibrate(30); } catch (_) { /* ignore */ }
        window.history.back();
        return;
      }

      // ── 未达阈值，重置状态 ──
      if (isPulling) {
        _updateToast('', '');
        isPulling = false;
        pullDistance = 0;
      }
    }, { passive: true });
  }

  // ── DOM 就绪后自动执行 ──
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _initGestures);
  } else {
    _initGestures();
  }
})();
