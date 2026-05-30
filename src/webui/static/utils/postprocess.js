/* ═══════════════════════════════════════════════════════════════
   utils/postprocess.js — Markdown 后处理（代码块复制按钮 + 标题锚点）

   此文件挂载 window.postProcessMarkdown，在 Markdown 渲染完成后调用。
   提供两个核心功能：
   1. 代码块「一键复制」按钮 — 每个 <pre><code> 右上角加复制按钮
   2. 标题锚点链接 — <h1~h6 id="xxx"> 右侧加 # 跳转链接

   被以下模块调用（均通过 `typeof window.postProcessMarkdown === 'function'` 守卫）：
   - handlers/streaming.js  — phase_done 后处理
   - handlers/rebuild.js     — 工具输出渲染
   - handlers/agents.js      — Agent 结果渲染
   - handlers.js              — 页面可见性切换后重渲染
   - bubble.js               — 消息气泡 DOM 变更后
   - components/Bubble.js    — Preact 组件生命周期
   - markdown-renderer.js    — IncrementalMarkdownRenderer finalize
   - md-engine.js            — ReactMarkdownPreact useEffect

   幂等性：二次调用已处理过的容器不会重复添加按钮。
   ═══════════════════════════════════════════════════════════════ */

(function() {
  'use strict';

  // ── 复制按钮 SVG 图标（clipboard + checkmark） ───────────
  var _ICON_CLIPBOARD = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>';
  var _ICON_CHECK = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>';

  // ── 避免重复处理的标记属性 ──
  var _PROCESSED_ATTR = 'data-copy-processed';

  /**
   * 主入口 — 对容器内的所有 <pre><code> 添加复制按钮 + 标题锚点
   *
   * @param {Element|string} container - 容器 DOM 元素或选择器字符串
   */
  function postProcessMarkdown(container) {
    if (!container) return;

    // 支持字符串选择器
    var el = (typeof container === 'string')
      ? document.querySelector(container)
      : container;
    if (!el) return;

    // ── 1. 代码块复制按钮 ─────────────────────────────
    _addCopyButtons(el);

    // ── 2. 标题锚点链接 ───────────────────────────────
    _addHeadingAnchors(el);
  }

  /* ═══════════════════════════════════════════════════════════
     1. 代码块复制按钮
     为每个未处理的 <pre><code> 添加右上角复制按钮组。
     ═══════════════════════════════════════════════════════════ */

  function _addCopyButtons(root) {
    var pres = root.querySelectorAll('pre');
    for (var i = 0; i < pres.length; i++) {
      var pre = pres[i];

      // 跳过已处理的和不含 <code> 的
      if (pre.hasAttribute(_PROCESSED_ATTR)) continue;
      var code = pre.querySelector('code');
      if (!code) continue;

      // 标记已处理
      pre.setAttribute(_PROCESSED_ATTR, '1');

      // 创建按钮组容器
      var btnGroup = document.createElement('div');
      btnGroup.className = 'code-btn-group';

      // 创建复制按钮
      var copyBtn = document.createElement('button');
      copyBtn.className = 'copy-btn';
      copyBtn.title = '复制代码';
      copyBtn.setAttribute('aria-label', '复制代码');
      copyBtn.innerHTML = _ICON_CLIPBOARD;

      // 点击处理
      _attachCopyHandler(copyBtn, pre, code);

      btnGroup.appendChild(copyBtn);
      pre.appendChild(btnGroup);
    }
  }

  /**
   * 为复制按钮绑定点击事件
   * 从 <pre> 中提取所有非 .code-btn-group 的代码文本
   */
  function _attachCopyHandler(btn, pre, code) {
    btn.addEventListener('click', function(e) {
      e.stopPropagation();

      // 提取代码文本：排除按钮组本身的文本
      var codeText = _getCodeText(pre, code);

      // 剪贴板 API
      _copyToClipboard(codeText).then(function(success) {
        if (success) {
          _showCopiedFeedback(btn);
        } else {
          // 兜底：选中代码块文本
          _fallbackSelect(pre, code);
        }
      });
    });
  }

  /**
   * 提取 <pre> 中的纯代码文本
   * 跳过按钮组容器，避免图标文字污染
   */
  function _getCodeText(pre, code) {
    // 优先从 <code> 元素提取
    var text = code.textContent || '';

    // 如果 code.textContent 为空或仅空白，从整体 pre 提取（含行号场景）
    if (!text.trim()) {
      // 深度克隆 pre，移除 .code-btn-group 后提取文本
      var clone = pre.cloneNode(true);
      var btnGroupClone = clone.querySelector('.code-btn-group');
      if (btnGroupClone) btnGroupClone.remove();
      text = clone.textContent || '';
    }

    return text;
  }

  /**
   * 剪贴板写入（带权限降级）
   */
  function _copyToClipboard(text) {
    if (!navigator.clipboard || !navigator.clipboard.writeText) {
      return Promise.resolve(false);
    }
    return navigator.clipboard.writeText(text).then(function() {
      return true;
    }).catch(function() {
      return false;
    });
  }

  /**
   * 兜底：选中代码块的文本（旧浏览器不支持 clipboard API）
   */
  function _fallbackSelect(pre, code) {
    try {
      var range = document.createRange();
      range.selectNodeContents(code);
      var selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    } catch (e) {
      // 降级失败时忽略
    }
  }

  /**
   * 显示「已复制」反馈，500ms 后恢复
   */
  function _showCopiedFeedback(btn) {
    var oldTitle = btn.title;
    var oldHtml = btn.innerHTML;

    btn.title = '已复制';
    btn.innerHTML = _ICON_CHECK;

    // 用 className 触发 CSS 颜色变化（.copy-btn[title="已复制"] 已有绿色样式）
    btn.setAttribute('title', '已复制');

    // 500ms 后恢复
    clearTimeout(btn._copyTimer);
    btn._copyTimer = setTimeout(function() {
      btn.title = oldTitle;
      btn.innerHTML = oldHtml;
    }, 500);
  }

  /* ═══════════════════════════════════════════════════════════
     2. 标题锚点链接
     为所有带 id 的 h1~h6 添加 # 跳转链接（与原 core.js 中
     _addHeadingAnchors 一致，内嵌于此避免模块间耦合）
     ═══════════════════════════════════════════════════════════ */

  var _ANCHOR_HEADINGS = ['h1', 'h2', 'h3', 'h4', 'h5', 'h6'];

  function _addHeadingAnchors(root) {
    for (var i = 0; i < _ANCHOR_HEADINGS.length; i++) {
      var tag = _ANCHOR_HEADINGS[i];
      var headings = root.querySelectorAll(tag + '[id]');
      for (var j = 0; j < headings.length; j++) {
        var heading = headings[j];
        // 跳过已添加锚点的标题
        if (heading.querySelector('.heading-anchor')) continue;
        var anchor = document.createElement('a');
        anchor.className = 'heading-anchor';
        anchor.href = '#' + heading.id;
        anchor.textContent = '#';
        anchor.setAttribute('aria-label', '跳转到此段落');
        anchor.title = '跳转到此段落';
        heading.appendChild(anchor);
      }
    }
  }

  /* ═══════════════════════════════════════════════════════════
     导出
     ═══════════════════════════════════════════════════════════ */

  window.postProcessMarkdown = postProcessMarkdown;

  // 调试：加载确认
  // console.log('[postprocess] postProcessMarkdown 已加载');
})();
