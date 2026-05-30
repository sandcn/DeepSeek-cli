/* ═══════════════════════════════════════════════════════════════
   markdown-renderer.js — Markdown 渲染（桥接层）
   
   本文件作为桥接层：
   1. 设置 fallback renderMarkdown（在 ESM 引擎加载前兜底）
   2. 保留 IncrementalMarkdownRenderer 流式增量渲染器
   3. 保留 _postProcessNewElements（后处理入口）
   
   核心渲染引擎已迁移至 md-engine.js（ES Module），
   通过 window.renderMarkdown 访问。
   ═══════════════════════════════════════════════════════════════ */

/* ── Fallback renderMarkdown（ESM 引擎加载前的兜底） ────────── */
window.renderMarkdown = window.renderMarkdown || function _fallbackRender(text) {
  if (!text) return '';
  console.warn('[md] 使用 fallback 渲染（ESM 引擎尚未就绪），文本前64字符:', (text || '').slice(0, 64));
  // 简单 HTML 转义 + 换行转 <br>
  const escaped = String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
  return escaped.replace(/\n/g, '<br>');
};


/* ═══════════════════════════════════════════════════════════════
   增量流式渲染器 — IncrementalMarkdownRenderer
   ═══════════════════════════════════════════════════════════════ */

/**
 * IncrementalMarkdownRenderer — 流式增量 Markdown 渲染器
 *
 * ★ 2026-05-14 性能优化（长代码块流式渲染不卡）：
 *   核心思路：检测到处于代码 fence (```/~~~) 内部时，
 *   跳过完整的 unified 管道 + highlight.js，仅做 HTML 转义后追加 DOM。
 *   finalize() 或代码块结束时再做一次完整高亮渲染。
 *
 * 使用方式:
 *   const renderer = new IncrementalMarkdownRenderer(containerEl);
 *   renderer.append(chunkText);
 *   renderer.finalize();
 */
class IncrementalMarkdownRenderer {
  /**
   * @param {HTMLElement} containerEl - 渲染目标的容器元素
   * @param {Object} [options]
   * @param {string} [options.className=''] - 容器的额外 CSS 类
   * @param {number} [options.flushInterval=400] - 自动 flush 间隔 (ms)，0=不自动flush
   */
  constructor(containerEl, options = {}) {
    if (!containerEl) throw new Error('IncrementalMarkdownRenderer: containerEl 不能为空');
    this._container = containerEl;
    this._buffer = '';
    this._completedBlocks = [];    // 已完成块的数据
    this._completedEls = [];       // 已完成块的 DOM 元素引用
    this._streamingBlock = null;   // 当前流式块 DOM 元素
    this._streamingText = '';      // 当前流式块的文本
    this._pendingFlush = false;    // flush 是否已调度
    this._lastFlushTime = 0;       // 上次 flush 的时间戳
    this._finalized = false;       // 是否已结束
    this._flushTimer = null;

    /* ── ★ [优化] 代码 fence 流式追踪 ── */
    this._inCodeFence = false;      // 是否在代码 fence 内
    this._codeFenceMarker = '';     // fence 字符（` 或 ~）
    this._codeLang = '';            // fence 语言标签
    this._codeBufferedHtml = '';    // 累计的轻量 HTML（仅当 _inCodeFence 时使用）
    this._codeHighlightTimer = null; // 后台高亮定时器

    const flushInterval = options.flushInterval !== undefined ? options.flushInterval : 400;

    this._container.classList.add('incremental-md-container');
    if (options.className) {
      this._container.classList.add(options.className);
    }

    if (flushInterval > 0) {
      this._flushTimer = setInterval(() => {
        if (this._buffer && !this._pendingFlush) {
          this._flush();
        }
      }, flushInterval);
    }
  }

  /**
   * 追加文本块
   * @param {string} text - 流式到达的文本片段
   */
  append(text) {
    if (this._finalized) return;
    if (!text) return;
    this._buffer += text;
    if (!this._pendingFlush) {
      this._pendingFlush = true;
      requestAnimationFrame(() => {
        if (this._pendingFlush) {
          this._flush();
        }
      });
    }
  }

  /**
   * ── flush 核心 ──────────────────────────────────────────
   * ★ [优化] 检测代码 fence 状态，分流：
   *   在代码块内 → 轻量 HTML 转义，跳过 unified 管道
   *   在代码块外 → 原有完整渲染流程
   */
  _flush() {
    this._pendingFlush = false;
    if (!this._buffer) return;

    const text = this._buffer;
    this._buffer = '';

    // ── ① 更新代码 fence 状态 ─────────────────────────────
    const fenceChanged = this._updateFenceState(text);

    // ── ② 根据 fence 状态分流 ─────────────────────────────
    if (this._inCodeFence) {
      if (fenceChanged.justEntered) {
        // 本次 flush 中打开了 fence → 需要截断，前面的文本走正常渲染
        this._handleFenceOpenInBuffer(text);
      } else {
        // 在代码块内（跨多次 flush）：轻量处理 → 跳过 unified 管道
        this._handleLightCodeFlush(text);
      }
    } else if (fenceChanged.justExited) {
      // ★ 刚从代码块退出：这次 flush 包含了关闭 fence, 用完整管道渲染一次
      this._handleExitingCodeFlush(text);
    } else {
      // 正常（非代码块）flush
      this._handleNormalFlush(text);
    }
  }

  /**
   * ★ 检测并更新代码 fence 状态（跨 flush 持久追踪）
   * @param {string} text - 当前 buffer 文本
   * @returns {{ justEntered: boolean, justExited: boolean }}
   */
  _updateFenceState(text) {
    const result = { justEntered: false, justExited: false };

    // 行级扫描 fence 标记
    const lines = text.split('\n');
    for (const line of lines) {
      if (this._inCodeFence) {
        // 在代码块内 → 检查是否遇到关闭 fence
        const trimmed = line.trim();
        if (trimmed.startsWith(this._codeFenceMarker) &&
            /^[~`]{3,}$/.test(trimmed)) {
          this._inCodeFence = false;
          result.justExited = true;
        }
      } else {
        // 不在代码块内 → 检查是否遇到开启 fence
        const trimmed = line.trim();
        const fenceMatch = trimmed.match(/^(```|~~~)(\S*)/);
        if (fenceMatch) {
          const rawMarker = trimmed[0]; // 第一个字符 ` 或 ~
          const matches = trimmed.match(new RegExp(`^${rawMarker}{3,}`));
          if (matches && !trimmed.slice(matches[0].length).includes(rawMarker)) {
            this._inCodeFence = true;
            this._codeFenceMarker = rawMarker;
            this._codeLang = trimmed.slice(matches[0].length).trim();
            this._codeBufferedHtml = ''; // 重置代码累积 HTML
            result.justEntered = true;
          }
        }
      }
    }

    return result;
  }

  /**
   * ★ [优化] fence 在本 buffer 中被打开
   *   前面的文本走正常渲染，fence 行及之后的内容走轻量代码处理
   */
  _handleFenceOpenInBuffer(text) {
    const lines = text.split('\n');
    let fenceLineIdx = -1;
    let fenceChar = '`';

    // 找到第一个 fence 行
    for (let i = 0; i < lines.length; i++) {
      const trimmed = lines[i].trim();
      const match = trimmed.match(/^(```|~~~)/);
      if (match) {
        fenceLineIdx = i;
        fenceChar = match[1][0];
        break;
      }
    }

    // 如果没有找到 fence 行（理论上不会发生），降级走正常渲染
    if (fenceLineIdx === -1) {
      this._handleNormalFlush(text);
      return;
    }

    // ★ 部分 A：fence 行之前的文本 → 走正常渲染
    if (fenceLineIdx > 0) {
      const beforeText = lines.slice(0, fenceLineIdx).join('\n');
      if (beforeText.trim()) {
        this._handleNormalFlush(beforeText);
      }
    }

    // ★ 部分 B：fence 行之后的内容才是代码 → 轻量处理
    //   fence 行本身（如 ```python）不追加到代码内容中
    const codeContent = lines.slice(fenceLineIdx + 1).join('\n');
    //  即使代码为空行, 也创建一个空的流式块, 保持 UI 连贯
    this._handleLightCodeFlush(codeContent || '');
  }

  /**
   * ★ [优化] 代码块内轻量 flush — 跳过 unified 管道
   *   直接 HTML 转义后更新流式 DOM
   */
  _handleLightCodeFlush(text) {
    // 对文本做 HTML 转义（保留空格/换行）
    const escaped = String(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');

    this._codeBufferedHtml += escaped;

    // 用轻量 HTML 包裹
    const lang = this._codeLang;
    const langAttr = lang ? ` data-lang="${this._escapeAttr(lang)}"` : '';
    const langClass = lang ? `language-${this._escapeAttr(lang)}` : '';
    const preHtml = `<pre${langAttr}><code class="${langClass}">${this._codeBufferedHtml}</code></pre>`;

    // 更新流式块
    this._updateStreamingLight(preHtml, this._codeBufferedHtml);

    // ★ 当代码块行数≥5行时，安排后台高亮（最终 finalize 时还会做一次完整高亮）
    const lineCount = this._codeBufferedHtml.split('\n').length;
    if (lineCount >= 5 && !this._codeHighlightTimer) {
      this._scheduleFullRender();
    }

    // 滚动到底部
    this._scrollIntoView();
  }

  /**
   * ★ 代码块结束退出时的完整渲染
   */
  _handleExitingCodeFlush(text) {
    // 取消后台高亮定时器
    if (this._codeHighlightTimer) {
      clearTimeout(this._codeHighlightTimer);
      this._codeHighlightTimer = null;
    }

    // ★ 先保存 fence 状态到局部变量，再重置
    const fence = this._codeFenceMarker.repeat(3);
    const lang = this._codeLang ? ` ${this._codeLang}` : '';
    const codeContent = this._codeBufferedHtml || '';
    this._codeBufferedHtml = '';
    this._codeLang = '';
    this._codeFenceMarker = '';

    // ★ 重建完整的 fence 包裹文本
    const fullCodeText = `${fence}${lang}\n${codeContent}\n${fence}`;

    // 用完整管道渲染（包含 highlight.js）
    const md = typeof window.renderMarkdown === 'function'
      ? window.renderMarkdown(fullCodeText)
      : this._escapeCodeFallback(fullCodeText);

    // 如果当前有流式块，转为已完成块
    if (this._streamingBlock) {
      this._streamingBlock.classList.remove('md-streaming-block');
      this._streamingBlock.classList.add('md-completed-block');
      this._completedBlocks.push(this._streamingText || text);
      this._completedEls.push(this._streamingBlock);
      _postProcessNewElements(this._streamingBlock);
      this._streamingBlock = null;
    }

    // 创建已完成块
    const div = document.createElement('div');
    div.className = 'md-completed-block';
    div.innerHTML = md;
    this._container.appendChild(div);
    this._completedBlocks.push(md);
    this._completedEls.push(div);
    _postProcessNewElements(div);
  }

  /**
   * ★ 在代码块流式渲染一段时间后，在后台做一次完整 high light 更新
   */
  _scheduleFullRender() {
    this._codeHighlightTimer = setTimeout(() => {
      this._codeHighlightTimer = null;
      if (this._finalized || !this._inCodeFence) return;

      // 重建代码 fence 包裹的文本
      const fence = this._codeFenceMarker.repeat(3);
      const lang = this._codeLang ? ` ${this._codeLang}` : '';
      const fullText = `${fence}${lang}\n${this._codeBufferedHtml}\n${fence}`;

      try {
        const rendered = typeof window.renderMarkdown === 'function'
          ? window.renderMarkdown(fullText)
          : this._escapeCodeFallback(fullText);

        if (this._streamingBlock) {
          // 仅更新流式块的 HTML（保留 DOM 元素）
          this._streamingBlock.innerHTML = rendered;
        }
      } catch (e) {
        // 后台高亮失败不阻塞主流程
        if (window._mdDebug) console.warn('[md] 后台代码高亮失败:', e.message);
      }
    }, 3000); // 3 秒后做一次
  }

  /**
   * 更新流式块 - 轻量版（不重建 DOM 元素）
   */
  _updateStreamingLight(html, rawText) {
    this._streamingText = rawText;

    if (this._streamingBlock) {
      // 复用已有流式块 DOM，仅更新 innerHTML
      this._streamingBlock.innerHTML = html;
    } else {
      const div = document.createElement('div');
      div.className = 'md-streaming-block';
      div.innerHTML = html;
      this._container.appendChild(div);
      this._streamingBlock = div;
    }
  }

  /**
   * 正常（非代码块）flush
   */
  _handleNormalFlush(text) {
    // 内容量太少且距上次 flush 太近 → 推迟
    if (text.length < 50 && this._lastFlushTime) {
      const now = Date.now();
      if (now - this._lastFlushTime < 200) {
        this._pendingFlush = true;
        requestAnimationFrame(() => {
          if (this._pendingFlush) this._flush();
        });
        return;
      }
    }
    this._lastFlushTime = Date.now();

    const blocks = this._parseBlocks(text);
    if (blocks.length === 0) return;

    for (let i = 0; i < blocks.length - 1; i++) {
      this._appendCompletedBlock(blocks[i]);
    }
    this._setStreamingBlock(blocks[blocks.length - 1]);
  }

  /**
   * 将文本解析为块级 HTML 片段
   */
  _parseBlocks(text) {
    // 使用 window.renderMarkdown 渲染（由 md-engine.js 提供）
    const md = typeof window.renderMarkdown === 'function'
      ? window.renderMarkdown(text)
      : text;

    // 按块级 HTML 标签分割
    const blockPattern = /(<(?:p|pre|h[1-6]|ul|ol|blockquote|hr|table|div)(?:\s[^>]*)?>[\s\S]*?<\/(?:p|pre|h[1-6]|ul|ol|blockquote|hr|table|div)>)/gi;
    const parts = [];
    let lastIndex = 0;
    let match;

    while ((match = blockPattern.exec(md)) !== null) {
      if (match.index > lastIndex) {
        const before = md.slice(lastIndex, match.index).trim();
        if (before) parts.push(before);
      }
      parts.push(match[1]);
      lastIndex = match.index + match[0].length;
    }
    if (lastIndex < md.length) {
      const remaining = md.slice(lastIndex).trim();
      if (remaining) parts.push(remaining);
    }

    return parts.length > 0 ? parts : [md];
  }

  _appendCompletedBlock(html) {
    const div = document.createElement('div');
    div.className = 'md-completed-block';
    div.innerHTML = html;
    this._container.appendChild(div);
    this._completedBlocks.push(html);
    this._completedEls.push(div);
    _postProcessNewElements(div);
  }

  _setStreamingBlock(html) {
    if (this._streamingBlock) {
      this._streamingBlock.classList.remove('md-streaming-block');
      this._streamingBlock.classList.add('md-completed-block');
      this._completedBlocks.push(this._streamingText);
      this._completedEls.push(this._streamingBlock);
      _postProcessNewElements(this._streamingBlock);
      this._streamingBlock = null;
    }

    this._streamingText = html;
    const div = document.createElement('div');
    div.className = 'md-streaming-block';
    div.innerHTML = html;
    this._container.appendChild(div);
    this._streamingBlock = div;
    this._scrollIntoView();
  }

  /**
   * 完成渲染
   */
  finalize(onDone) {
    if (this._finalized) return;
    this._finalized = true;

    if (this._flushTimer) {
      clearInterval(this._flushTimer);
      this._flushTimer = null;
    }
    if (this._codeHighlightTimer) {
      clearTimeout(this._codeHighlightTimer);
      this._codeHighlightTimer = null;
    }

    // ── 处理剩余 buffer ──────────────────────────────
    if (this._buffer) {
      const text = this._buffer;
      this._buffer = '';

      if (this._inCodeFence) {
        // 代码块未关闭 → 用完整管道渲染一次
        const fence = this._codeFenceMarker.repeat(3);
        const lang = this._codeLang ? ` ${this._codeLang}` : '';
        const fullText = `${fence}${lang}\n${this._codeBufferedHtml || ''}\n${fence}`;
        const md = typeof window.renderMarkdown === 'function'
          ? window.renderMarkdown(fullText)
          : this._escapeCodeFallback(fullText);

        this._finalizeCodeBlock(md);
      } else {
        const blocks = this._parseBlocks(text);
        if (blocks.length > 0) {
          for (let i = 0; i < blocks.length - 1; i++) {
            this._appendCompletedBlock(blocks[i]);
          }
          this._setStreamingBlock(blocks[blocks.length - 1]);
        }
      }
    }

    // ── 处理流式块 → 已完成块 ─────────────────────────
    if (this._streamingBlock) {
      this._streamingBlock.classList.remove('md-streaming-block');
      this._streamingBlock.classList.add('md-completed-block');
      this._completedBlocks.push(this._streamingText);
      this._completedEls.push(this._streamingBlock);
      _postProcessNewElements(this._streamingBlock);
      this._streamingBlock = null;
    }

    if (this._container.classList.contains('md-streaming')) {
      this._container.classList.remove('md-streaming');
    }

    this._resetFenceState();

    if (onDone) onDone();
  }

  /**
   * 将流式代码块升级为已完成块（使用完整渲染 HTML）
   */
  _finalizeCodeBlock(fullMd) {
    if (this._streamingBlock) {
      this._streamingBlock.classList.remove('md-streaming-block');
      this._streamingBlock.classList.add('md-completed-block');
      this._completedBlocks.push(this._streamingText || fullMd);
      this._completedEls.push(this._streamingBlock);
      // 用完整渲染结果替换 innerHTML
      this._streamingBlock.innerHTML = fullMd;
      _postProcessNewElements(this._streamingBlock);
      this._streamingBlock = null;
    } else {
      const div = document.createElement('div');
      div.className = 'md-completed-block';
      div.innerHTML = fullMd;
      this._container.appendChild(div);
      this._completedBlocks.push(fullMd);
      this._completedEls.push(div);
      _postProcessNewElements(div);
    }

    this._resetFenceState();
  }

  _resetFenceState() {
    this._inCodeFence = false;
    this._codeFenceMarker = '';
    this._codeLang = '';
    this._codeBufferedHtml = '';
  }

  destroy() {
    if (this._flushTimer) {
      clearInterval(this._flushTimer);
      this._flushTimer = null;
    }
    if (this._codeHighlightTimer) {
      clearTimeout(this._codeHighlightTimer);
      this._codeHighlightTimer = null;
    }
    this._buffer = '';
    this._streamingText = '';
    this._streamingBlock = null;
    this._completedBlocks = [];
    this._completedEls = [];
    this._finalized = true;
    this._resetFenceState();
  }

  _scrollIntoView() {
    const messagesEl = document.getElementById('messages');
    if (messagesEl) {
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }
  }

  _escapeAttr(value) {
    return String(value).replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  _escapeCodeFallback(text) {
    const escaped = String(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
    return `<pre><code>${escaped}</code></pre>`;
  }
}

/**
 * 对新添加的 DOM 元素进行后处理（KaTeX、Mermaid、复制按钮、标题锚点）
 */
function _postProcessNewElements(container) {
  if (typeof window.postProcessMarkdown === 'function') {
    requestAnimationFrame(() => {
      window.postProcessMarkdown(container);
    });
  }
}


/* ═══════════════════════════════════════════════════════════════
   全局导出
   ═══════════════════════════════════════════════════════════════ */

window.IncrementalMarkdownRenderer = IncrementalMarkdownRenderer;
