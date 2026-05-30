/* ═══════════════════════════════════════════════════════════════
   md-engine.js — Markdown 渲染引擎（ES Module）
   
   基于 unified/remark/rehype 管线（react-markdown 底层生态）：
   - renderMarkdownToHtml(text)   — 同步 Markdown → HTML 字符串
   - ReactMarkdownPreact          — Preact 兼容的 Markdown 渲染组件（innerHTML 模式）
   - setGlobalRenderMarkdown()    — 将 renderMarkdownToHtml 挂到 window.renderMarkdown
   
   底层引擎: lib/md-engine.bundle.js（自包含，无外部依赖）
   ═══════════════════════════════════════════════════════════════ */

import { h } from './lib/preact.module.js';
import { useEffect, useRef } from './lib/hooks.module.js';
import { renderMarkdownToHtml as _renderHtml } from './lib/md-engine.bundle.js';

/**
 * 同步 Markdown → HTML 字符串
 * @param {string} text - Markdown 文本
 * @returns {string} HTML 字符串
 */
function renderMarkdownToHtml(text) {
  if (!text) return '';
  try {
    const result = _renderHtml(text);
    const safe = _sanitizeHtml(result);
    if (safe && (safe.includes('<') || safe === text.replace(/\n/g, '<br>'))) {
      return safe;
    }
    return safe || '';
  } catch (e) {
    console.warn('[md-engine:wrap] 渲染失败:', e.message);
    return _escapeHtml(text).replace(/\n/g, '<br>');
  }
}

/**
 * 同步渲染并消毒 Markdown → HTML（供外部安全调用）
 * @param {string} text - Markdown 文本
 * @returns {string} 已消毒的 HTML 字符串
 */
function renderSafeMarkdown(text) {
  return _sanitizeHtml(renderMarkdownToHtml(text));
}

function _escapeHtml(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/**
 * 将 renderMarkdownToHtml 挂载到 window.renderMarkdown
 * 供 handlers.js / bubble.js 等非 module 脚本使用
 */
function setGlobalRenderMarkdown() {
  if (typeof window !== 'undefined') {
    if (window.renderMarkdown !== renderSafeMarkdown) {
      window.renderMarkdown = renderSafeMarkdown;
      console.log('[md-engine] window.renderMarkdown 已设置为 react-markdown 引擎');
    }
  }
}

/* ═══════════════════════════════════════════════════════════════
   DOMPurify 辅助 — 安全净化 HTML
   ═══════════════════════════════════════════════════════════════ */

function _sanitizeHtml(html) {
  if (typeof DOMPurify !== 'undefined' && typeof DOMPurify.sanitize === 'function') {
    return DOMPurify.sanitize(html, {
      ALLOWED_TAGS: [
        'p', 'br', 'b', 'i', 'em', 'strong', 'a', 'img',
        'ul', 'ol', 'li', 'hr', 'blockquote', 'pre', 'code',
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'table', 'thead', 'tbody', 'tr', 'th', 'td',
        'del', 'ins', 'sub', 'sup', 'details', 'summary',
        'div', 'span', 'input', 'dl', 'dt', 'dd',
        'figure', 'figcaption', 'mark', 'small', 'kbd',
      ],
      ALLOWED_ATTR: [
        'href', 'target', 'rel', 'src', 'alt', 'class',
        'id', 'width', 'height', 'loading', 'title',
        'type', 'checked', 'disabled', 'start', 'reversed',
        'data-lang', 'lang', 'style',
      ],
    });
  }
  // 回退消毒：DOMPurify 不可用时，至少剥离危险标签和事件属性
  // 虽然不如 DOMPurify 白名单严格，但可拦截最常见的 XSS 攻击向量
  return _basicSanitize(html);
}

/**
 * 最小回退消毒器 — 不依赖外部库
 * 移除危险标签及内容、事件处理属性、javascript: URI
 * @param {string} html
 * @returns {string}
 */
function _basicSanitize(html) {
  // 1. 移除危险标签及其内容
  html = html.replace(/<script[\s\S]*?<\/script>/gi, '');
  html = html.replace(/<iframe[\s\S]*?<\/iframe>/gi, '');
  html = html.replace(/<object[\s\S]*?<\/object>/gi, '');
  html = html.replace(/<embed[\s\S]*?<\/embed>/gi, '');
  html = html.replace(/<style[\s\S]*?<\/style>/gi, '');
  html = html.replace(/<svg[\s\S]*?<\/svg>/gi, '');
  html = html.replace(/<template[\s\S]*?<\/template>/gi, '');
  // 2. 移除所有 on* 事件属性（onclick, onerror, onload, onmouseover 等）
  html = html.replace(/\s+on\w+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi, '');
  // 3. 移除 href/src/action 中的 javascript: 和 data: URI
  html = html.replace(
    /(href|src|action|formaction)\s*=\s*(?:"javascript:[^"]*"|'javascript:[^']*'|javascript:[^\s>]+)/gi,
    '$1=""'
  );
  html = html.replace(
    /(href|src|action|formaction)\s*=\s*(?:"data:[^"]*"|'data:[^']*'|data:[^\s>]+)/gi,
    '$1=""'
  );
  return html;
}

/* ═══════════════════════════════════════════════════════════════
   ReactMarkdownPreact — Preact 兼容的 Markdown 渲染组件
   使用 react-markdown 引擎渲染 HTML 后通过 innerHTML 注入
   保持与现有 Bubble 组件的兼容性
   ═══════════════════════════════════════════════════════════════ */

function ReactMarkdownPreact(props) {
  const rootRef = useRef(null);
  const { children, className, ...rest } = props;

  useEffect(() => {
    if (!rootRef.current) return;
    const text = children || '';
    if (!text) {
      rootRef.current.innerHTML = '';
      return;
    }
    const html = renderMarkdownToHtml(text);
    if (rootRef.current.innerHTML !== html) {
      rootRef.current.innerHTML = html;
    }
    if (typeof window.postProcessMarkdown === 'function') {
      requestAnimationFrame(() => {
        window.postProcessMarkdown(rootRef.current);
      });
    }
  }, [children]);

  return h('div', { ref: rootRef, class: className || '', ...rest });
}

export {
  renderMarkdownToHtml,
  renderSafeMarkdown,
  setGlobalRenderMarkdown,
  ReactMarkdownPreact,
  _sanitizeHtml,
};
