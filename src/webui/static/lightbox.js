/* ═══════════════════════════════════════════════════════════════
   lightbox.js — 全屏查看器（图片/代码块 lightbox）
   风格: IIFE（自执行函数），vanilla JS，不依赖 ES module
   依赖: lightbox.css（需在 HTML 中先于本文件加载）
   ═══════════════════════════════════════════════════════════════ */

(function() {
  'use strict';

  /* ── DOM 引用 ── */
  var overlay = null;
  var closeBtn = null;
  var imgEl = null;
  var codeWrapper = null;
  var codeEl = null;
  var initialized = false;

  /* ── 缩放状态 ── */
  var currentImageScale = 1;       // 当前图片缩放值

  /* ── 双指 pinch 临时状态 ── */
  var _pinch = {
    initialDist: 0,     // 双指初始距离
    baseScale: 1,       // 手势开始时的 currentImageScale
    active: false
  };

  /* ── 下滑关闭临时状态 ── */
  var _swipe = {
    startY: 0,
    active: false
  };

  /* ── MutationObserver ── */
  var _preObserver = null;

  /* ═══════════════════════════════════════════════════════════
     1. 初始化
     ═══════════════════════════════════════════════════════════ */
  function init() {
    if (initialized) return;
    initialized = true;

    /* ── 创建 DOM 结构 ── */
    overlay = document.createElement('div');
    overlay.id = 'lightbox-overlay';
    overlay.className = 'lightbox-overlay hidden';

    closeBtn = document.createElement('div');
    closeBtn.className = 'lightbox-close-btn';
    closeBtn.textContent = '\u2715';
    closeBtn.setAttribute('role', 'button');
    closeBtn.setAttribute('aria-label', '关闭');
    closeBtn.tabIndex = 0;

    var content = document.createElement('div');
    content.className = 'lightbox-content';

    /* 代码包装器 */
    codeWrapper = document.createElement('div');
    codeWrapper.className = 'lightbox-code-wrapper';
    codeWrapper.style.display = 'none';
    var pre = document.createElement('pre');
    codeEl = document.createElement('code');
    codeEl.className = 'lightbox-code';
    pre.appendChild(codeEl);
    codeWrapper.appendChild(pre);

    /* 图片元素 */
    imgEl = document.createElement('img');
    imgEl.className = 'lightbox-image';
    imgEl.style.display = 'none';
    imgEl.alt = '';

    content.appendChild(codeWrapper);
    content.appendChild(imgEl);
    overlay.appendChild(closeBtn);
    overlay.appendChild(content);
    document.body.appendChild(overlay);

    /* ── 关闭：点击关闭按钮 ── */
    closeBtn.addEventListener('click', function(e) {
      e.stopPropagation();
      close();
    });

    /* ── 关闭：点击遮罩背景 ── */
    overlay.addEventListener('click', function(e) {
      if (e.target === overlay || e.target.classList.contains('lightbox-content')) {
        close();
      }
    });

    /* ── 关闭：按 ESC ── */
    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape' && overlay.classList.contains('open')) {
        close();
      }
    });

    /* ── 双指缩放 + 下滑关闭（图片模式） ── */
    overlay.addEventListener('touchstart', function(e) {
      var touches = e.touches;

      if (touches.length === 2) {
        /* 双指：记录初始距离和基准缩放 */
        var dx = touches[0].clientX - touches[1].clientX;
        var dy = touches[0].clientY - touches[1].clientY;
        _pinch.initialDist = Math.sqrt(dx * dx + dy * dy);
        _pinch.baseScale = currentImageScale;
        _pinch.active = true;
        _swipe.active = false;
      } else if (touches.length === 1 && imgEl.style.display !== 'none') {
        /* 单指：记录下滑起始 Y（仅在图片模式下） */
        _swipe.startY = touches[0].clientY;
        _swipe.active = true;
      }
    }, { passive: true });

    overlay.addEventListener('touchmove', function(e) {
      var touches = e.touches;

      if (touches.length === 2 && _pinch.active) {
        /* 双指缩放 */
        e.preventDefault();
        var dx = touches[0].clientX - touches[1].clientX;
        var dy = touches[0].clientY - touches[1].clientY;
        var dist = Math.sqrt(dx * dx + dy * dy);
        var ratio = dist / _pinch.initialDist;
        var scale = _pinch.baseScale * ratio;
        scale = Math.max(0.5, Math.min(5, scale));
        currentImageScale = scale;
        imgEl.style.transform = 'scale(' + scale + ')';
        _swipe.active = false;
      } else if (touches.length === 1 && _swipe.active && imgEl.style.display !== 'none') {
        /* 单指下滑关闭（仅 scale <= 1 时生效） */
        if (currentImageScale <= 1.01) {
          var deltaY = touches[0].clientY - _swipe.startY;
          if (deltaY > 80) {
            close();
            _swipe.active = false;
          }
        }
      }
    }, { passive: false });

    overlay.addEventListener('touchend', function() {
      _pinch.active = false;
      _swipe.active = false;
    }, { passive: true });

    /* ── 事件委托：图片点击（监听 #messages） ── */
    var messages = document.getElementById('messages');
    if (messages) {
      messages.addEventListener('click', function(e) {
        var target = e.target;
        if (target.tagName === 'IMG' && !target.closest('.lightbox-overlay')) {
          var src = target.getAttribute('src') || '';
          var alt = target.getAttribute('alt') || '';
          showImage(src, alt);
          e.preventDefault();
        }
      });
    }

    /* ── 事件委托：代码全屏按钮点击 ── */
    if (messages) {
      messages.addEventListener('click', function(e) {
        var target = e.target;
        if (target.classList.contains('code-fullscreen-btn')) {
          var pre = target.closest('pre');
          if (pre) {
            var code = pre.querySelector('code');
            if (code) {
              showCode(code);
            }
          }
          e.preventDefault();
          e.stopPropagation();
        }
      });
    }

    /* ── 扫描已有 pre + 监听新气泡 ── */
    addFullscreenButtonsToAllPres();
    startPreObserver();
  }

  /* ═══════════════════════════════════════════════════════════
     2. 显示图片
     ═══════════════════════════════════════════════════════════ */
  function showImage(src, alt) {
    if (!overlay || !imgEl || !codeWrapper) return;

    /* 切换为图片模式 */
    imgEl.style.display = '';
    codeWrapper.style.display = 'none';

    imgEl.src = src;
    imgEl.alt = alt || '';
    imgEl.style.transform = 'none';
    currentImageScale = 1;

    /* 打开遮罩（入场动画） */
    overlay.classList.remove('hidden', 'closing');
    overlay.classList.add('open');
    void overlay.offsetWidth;
    overlay.classList.add('show');
  }

  /* ═══════════════════════════════════════════════════════════
     3. 显示代码
     ═══════════════════════════════════════════════════════════ */
  function showCode(codeElement) {
    if (!overlay || !codeEl || !codeWrapper) return;

    /* 切换为代码模式 */
    imgEl.style.display = 'none';
    codeWrapper.style.display = '';

    /* 使用 textContent 读取纯文本，避免 HTML 注入 */
    codeEl.textContent = codeElement.textContent;

    /* 复制 class 以保留高亮主题样式（如 hljs, language-xxx） */
    codeEl.className = 'lightbox-code';
    if (codeElement.className) {
      codeEl.className += ' ' + codeElement.className;
    }

    /* 重新高亮 */
    if (typeof hljs !== 'undefined' && hljs.highlightElement) {
      try { hljs.highlightElement(codeEl); } catch (_) {}
    }

    /* 打开遮罩（入场动画） */
    overlay.classList.remove('hidden', 'closing');
    overlay.classList.add('open');
    void overlay.offsetWidth;
    overlay.classList.add('show');

    /* 滚动到代码顶部 */
    codeWrapper.scrollTop = 0;
  }

  /* ═══════════════════════════════════════════════════════════
     4. 关闭
     ═══════════════════════════════════════════════════════════ */
  function close() {
    if (!overlay) return;
    if (!overlay.classList.contains('open')) return;

    /* 渐出动画 */
    overlay.classList.remove('show');
    overlay.classList.add('closing');

    setTimeout(function() {
      overlay.classList.add('hidden');
      overlay.classList.remove('open', 'closing');

      /* 清理图片资源 */
      if (imgEl) {
        imgEl.src = '';
        imgEl.alt = '';
      }
    }, 150);
  }

  /* ═══════════════════════════════════════════════════════════
     5. 为 <pre> 添加全屏按钮
     ═══════════════════════════════════════════════════════════ */
  function addFullscreenButton(pre) {
    if (!pre || pre.querySelector('.code-fullscreen-btn')) return;

    var btn = document.createElement('button');
    btn.className = 'code-fullscreen-btn';
    btn.title = '全屏查看';
    btn.setAttribute('aria-label', '全屏查看代码');
    btn.textContent = '\u2B36'; /* ⛶ */

    /* 优先放入已有的 .code-btn-group（与复制按钮同行） */
    var btnGroup = pre.querySelector('.code-btn-group');
    if (btnGroup) {
      btnGroup.appendChild(btn);
    } else {
      /* 兜底：直接追加到 pre */
      pre.appendChild(btn);
    }
  }

  /* ── 扫描全部已有 pre ── */
  function addFullscreenButtonsToAllPres() {
    var pres = document.querySelectorAll('#messages .bubble pre');
    for (var i = 0; i < pres.length; i++) {
      addFullscreenButton(pres[i]);
    }
  }

  /* ═══════════════════════════════════════════════════════════
     6. MutationObserver：监听新气泡添加全屏按钮
     ═══════════════════════════════════════════════════════════ */
  function startPreObserver() {
    if (_preObserver) return;

    var el = document.getElementById('messages');
    if (!el) {
      setTimeout(startPreObserver, 100);
      return;
    }

    _preObserver = new MutationObserver(function(mutations) {
      for (var m = 0; m < mutations.length; m++) {
        var mut = mutations[m];
        if (mut.type !== 'childList') continue;

        for (var n = 0; n < mut.addedNodes.length; n++) {
          var node = mut.addedNodes[n];
          if (node.nodeType !== 1) continue;

          /* 查找新气泡中的 <pre> */
          var pres;
          if (node.classList && node.classList.contains('bubble')) {
            pres = node.querySelectorAll('pre');
          } else {
            pres = node.querySelectorAll('.bubble pre');
          }

          for (var p = 0; p < pres.length; p++) {
            addFullscreenButton(pres[p]);
          }
        }
      }
    });

    _preObserver.observe(el, { childList: true, subtree: false });
  }

  /* ═══════════════════════════════════════════════════════════
     7. 启动
     ═══════════════════════════════════════════════════════════ */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  /* ── 导出到全局供调试 ── */
  window.__lightbox = {
    showImage: showImage,
    showCode: showCode,
    close: close,
    overlay: function() { return overlay; }
  };
})();
