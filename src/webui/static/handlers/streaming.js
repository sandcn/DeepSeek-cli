/* ═══════════════════════════════════════════════════════════════
   handlers/streaming.js — 流式文本增量渲染和消息处理器
   依赖:
     bubble.js   → bubbles, messagesEl, addBubble, scrollToBottom, _parallelBatchEl
     base.js     → _st, _addGenChars, _renderMarkdownFallback, _debouncedScrollToBottom
     utils.js    → postProcessMarkdown (window.postProcessMarkdown)
   从原 handlers.js 提取，所有函数导出到全局（index.html 传统 script 加载）
   ═══════════════════════════════════════════════════════════════ */

/* ── 流式文本缓冲区（key → 累积文本） ────────────────────── */
// 用于流式 chunk 的 O(1) 文本追加，避免每次更新 Preact store
const _streamingText = new Map();

// ── 增量渲染状态：每个 DOM 元素的已提交字符位置 ──
const _renderState = new WeakMap();

// ★ 持久代码 fence 状态（跨 flush 追踪，修复 2000 字符截断误判）
const _fenceState = {
  countBeforeSearchFrom: 0,  // fullText[0..searchFrom) 中的 fence 奇偶计数
  lastSearchFrom: 0,         // 上次扫描到的位置
};

/**
 * 在完整文本中查找安全的块级提交边界。
 * 安全边界 = \n\n 且不在代码 fence 内部。
 * 返回 boundary 后的字符位置，若无则返回 committedLen。
 *
 * ★ ═══ 性能优化 ═══ 限制扫描范围最多 2000 字符
 *   原实现每次 rAF 都扫描 fullText[committedLen..end] 整个剩余文本，
 *   随着文本增长 O(n) 越来越大。限制为最多往后看 2000 字符，
 *   绝大多数情况下 \n\n 在此范围内出现。
 *   超长无段落分隔的退化情况只影响 tail 大小，不影响正确性。
 */
function _findSafeCommitEnd(fullText, committedLen) {
  const searchFrom = Math.max(committedLen, 0);
  if (searchFrom >= fullText.length) return committedLen;

  // ★ 限制扫描范围最多 2000 字符
  const maxLookahead = 2000;
  const searchEnd = Math.min(searchFrom + maxLookahead, fullText.length);
  const searchText = fullText.slice(searchFrom, searchEnd);

  // ★ 持久 fence 计数：扫描 searchFrom 到 lastSearchFrom 之间的增量 fence
  if (searchFrom > _fenceState.lastSearchFrom) {
    const deltaText = fullText.slice(_fenceState.lastSearchFrom, searchFrom);
    const fenceRe = /```/g;
    let m;
    while ((m = fenceRe.exec(deltaText)) !== null) {
      _fenceState.countBeforeSearchFrom++;
    }
  }
  _fenceState.lastSearchFrom = searchFrom;

  // 定位代码 fence 位置（相对于 searchText 的偏移）
  const fences = [];
  const fenceRe = /```/g;
  let m;
  while ((m = fenceRe.exec(searchText)) !== null) {
    fences.push(m.index);
  }

  function insideFence(pos) {
    let localCount = 0;
    for (const f of fences) {
      if (f > pos) break;
      localCount++;
    }
    // ★ 加上持久偏移：持久状态记录了 searchText 之前的 fence 奇偶
    return (_fenceState.countBeforeSearchFrom + localCount) % 2 === 1;
  }

  // 从末尾往前找最后一个安全的 \n\n（在限定的 searchText 范围内）
  let lastPos = -1;
  let i = 0;
  while (true) {
    const found = searchText.indexOf('\n\n', i);
    if (found === -1) break;
    if (!insideFence(found)) {
      lastPos = found;
    }
    i = found + 2;
  }

  if (lastPos >= 0) {
    return searchFrom + lastPos + 2; // +2 包含 \n\n
  }
  return committedLen;
}

/**
 * 增量渲染调度 — 已提交块只渲染一次，流式尾块整体渲染
 *
 * ★ 2026-05-14 重构：流式尾块改为「完整 tail 文本整体渲染」策略
 *   之前是「增量追加」—— 每个 chunk 独立渲染后追加到 tailDiv，
 *   导致 <span><p>...</p></span> 无效嵌套（之前的修复）以及跨 chunk 的
 *   行内元素（粗体/行内代码/公式等）被拆成多个独立 <p> 段落的视觉碎片。
 *
 *   新策略：每当有新字符到达时，将完整 tail 文本（committedLen 之后的所有内容）
 *   整体丢给 markdown 引擎渲染，替换 tailDiv.innerHTML。
 *   - tail 文本通常 ≤2000 字符（_findSafeCommitEnd 的 maxLookahead），性能可接受
 *   - markdown 引擎看到完整上下文，跨 chunk 的行内元素正确合并为一个 <p>
 *   - 已提交块（\n\n 边界之前的完整段落）仍保持「渲染一次、追加到 DOM」的 O(1) 策略
 *
 *   streaming 尾块跳过 postProcessMarkdown（KaTeX/Mermaid 留给 phase_done 处理）
 */
const _pendingRenders = new Map();
let _rafId = null;

function _scheduleIncrementalRender(sectionEl, fullText) {
  // 读取用户设置的字幕速度（打字机效果延迟）
  // 默认 0（即时渲染），由 settings.js 的 typingSpeed 控制
  var _typingDelay = (typeof window.__typingSpeed !== 'undefined') ? window.__typingSpeed : 0;
  _pendingRenders.set(sectionEl, fullText);
  if (!_rafId) {
    // ★ 记录当前用户是否在底部，后续 rAF 回调据此决定是否自动滚动
    const el = typeof messagesEl !== 'undefined' ? messagesEl : document.getElementById('messages');
    const wasAtBottom = el ? (el.scrollHeight - el.scrollTop - el.clientHeight) < 150 : true;
    // ★ 修复：浏览器后台标签页时 requestAnimationFrame 完全暂停，
    //   改用 setTimeout(fn, 0) 确保后台时仍能正常渲染消息界面。
    //   setTimeout 在后台标签页会被节流到 ~1s 间隔但仍会执行。
    _rafId = setTimeout(() => {
      _rafId = null;
      // ★ 先 snapshot 再 clear，防止 _doIncrementalRender 执行期间
      //   _scheduleIncrementalRender 被新消息触发并添加新条目到 _pendingRenders，
      //   然后 clear() 误清新条目导致数据静默丢失。
      const entries = [..._pendingRenders];
      _pendingRenders.clear();
      for (const [el, text] of entries) {
        _doIncrementalRender(el, text);
      }
      // 渲染前在底部 → 渲染后滚动到底部
      // 额外用宽阈值兜底：用户刚手动滚回底部附近时也触发滚动
      if (wasAtBottom || (messagesEl && (messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight) < 150)) {
        scrollToBottom();
      }
    }, _typingDelay);
  }
}

function _doIncrementalRender(el, fullText) {
  let state = _renderState.get(el);
  if (!state) {
    state = { committedLen: 0, renderedLen: 0 };
    _renderState.set(el, state);
  }

  // 文本被截断或清空 → 全量重渲染
  if (fullText.length < state.committedLen || fullText.length === 0) {
    state.committedLen = 0;
    state.renderedLen = 0;
    el.innerHTML = '';
    if (!fullText) return;
  }

  // ── 找安全边界，提交新完成的块（渲染一次，追加到 DOM） ──
  const safeEnd = _findSafeCommitEnd(fullText, state.committedLen);

  if (safeEnd > state.committedLen) {
    const commitText = fullText.slice(state.committedLen, safeEnd);
    const commitHtml = (typeof window._renderMarkdownFallback === 'function'
      ? window._renderMarkdownFallback(commitText)
      : commitText.replace(/\n/g, '<br>'));
    let committedDiv = el.querySelector('._md-committed');
    if (!committedDiv) {
      committedDiv = document.createElement('div');
      committedDiv.className = '_md-committed';
      el.insertBefore(committedDiv, el.firstChild);
    }
    const batchDiv = document.createElement('div');
    batchDiv.className = '_md-batch';
    batchDiv.innerHTML = commitHtml;
    committedDiv.appendChild(batchDiv);
    // ★ 已提交块做完整后处理（KaTeX / Mermaid / 复制按钮等）
    if (typeof window.postProcessMarkdown === 'function') {
      window.postProcessMarkdown(batchDiv);
    }
    state.committedLen = safeEnd;
    // 提交新块后，旧的 streaming 容器被删除（因为尾文本变了），renderedLen 重置
    state.renderedLen = safeEnd;
    const oldTail = el.querySelector('._md-streaming');
    if (oldTail) oldTail.remove();
  }

  // ── 流式尾块渲染：完整 tail 文本整体渲染 ───────────────
  const tailText = fullText.slice(state.committedLen);
  if (tailText) {
    let tailDiv = el.querySelector('._md-streaming');
    if (!tailDiv) {
      tailDiv = document.createElement('div');
      tailDiv.className = '_md-streaming';
      el.appendChild(tailDiv);
    }

    // ★ 2026-05-14 重写：完整 tail 文本整体渲染，而非增量片段追加
    //   markdown 引擎看到完整上下文，跨 chunk 的行内元素（粗体/行内代码/公式）
    //   被正确合并为一个 <p> 段落，不再碎成多个独立段落。
    //   性能：tail 文本因 _findSafeCommitEnd 的 maxLookahead=2000 限制，通常很小。
    if (state.renderedLen < fullText.length) {
      const fullTailHtml = (typeof window._renderMarkdownFallback === 'function'
        ? window._renderMarkdownFallback(tailText)
        : tailText.replace(/\n/g, '<br>'));
      tailDiv.innerHTML = fullTailHtml;
      state.renderedLen = fullText.length;
      // ★ 流式尾块跳过 postProcessMarkdown（KaTeX/Mermaid 留给 phase_done 处理）
    }
  } else {
    // 没有尾块 → 移除 streaming 容器
    const tailDiv = el.querySelector('._md-streaming');
    if (tailDiv) tailDiv.remove();
    state.renderedLen = state.committedLen;
  }
}

/**
 * 流式文本追加 — 增量渲染，每帧只渲染未提交的尾段
 * 复杂度：O(1) per frame（尾段通常 <100 字符）
 */
function _appendStreamingText(key, idx, text, section) {
  // 累积文本到缓冲区
  const bufKey = key + '-' + section;
  const prev = _streamingText.get(bufKey) || '';
  const full = prev + text;
  _streamingText.set(bufKey, full);

  // 获取或创建气泡 DOM
  let el = bubbles.get(key);
  if (!el) {
    el = addBubble('answer');
    const header = document.createElement('div');
    header.className = 'header';
    header.innerHTML = '<span class="msg-tag">#' + idx + ' 🤖</span>';
    el.appendChild(header);

    const thinkEl = document.createElement('div');
    thinkEl.className = 'think-section';
    el.appendChild(thinkEl);

    const answerEl = document.createElement('div');
    answerEl.className = 'answer-section';
    el.appendChild(answerEl);

    bubbles.set(key, el);
  }

  // 增量渲染调度
  const sectionEl = el.querySelector(section === 'think' ? '.think-section' : '.answer-section');
  if (sectionEl) {
    const fullText = (section === 'think')
      ? (_streamingText.get(key + '-think') || '')
      : (_streamingText.get(key + '-content') || '');
    if (fullText) {
      // ★ 性能优化：文本长度未变化则跳过 RAF 调度（减少无谓的 markdown 重解析）
      const rs = _renderState.get(sectionEl);
      if (rs && fullText.length === rs.renderedLen && fullText.length === rs.committedLen) return;
      _scheduleIncrementalRender(sectionEl, fullText);
    }
  }
}

/* ── Assistant 气泡创建/更新辅助（vanilla DOM 兜底，仅在 Preact 不可用时调用） ── */
function _ensureAssistantBubble(idx, text, section) {
  const key = 'assistant-' + idx;
  let el = bubbles.get(key);
  if (!el) {
    el = addBubble('answer');
    const header = document.createElement('div');
    header.className = 'header';
    header.innerHTML = '<span class="msg-tag">#' + idx + ' 🤖</span>';
    el.appendChild(header);
    const thinkEl = document.createElement('div');
    thinkEl.className = 'think-section';
    el.appendChild(thinkEl);
    const answerEl = document.createElement('div');
    answerEl.className = 'answer-section';
    el.appendChild(answerEl);
    bubbles.set(key, el);
  }
  const sectionEl = el.querySelector(section === 'think' ? '.think-section' : '.answer-section');
  if (sectionEl) {
    const fullKey = section === 'think' ? '_thinkFull' : '_answerFull';
    const prev = el.dataset[fullKey] || '';
    const full = prev + text;
    el.dataset[fullKey] = full;
    const md = (typeof window._renderMarkdownFallback === 'function'
      ? window._renderMarkdownFallback(full || '')
      : (full || '').replace(/\n/g, '<br>'));
    sectionEl.innerHTML = md;
  }
  scrollToBottom();
}

/* ═══════════════════════════════════════════════════════════════
   消息处理器
   ═══════════════════════════════════════════════════════════════ */

function handleUserMessage(data) {
  const idx = data.msg_index;
  // ★ 重置并行批处理气泡引用（新用户消息开始新的工具序列）
  if (typeof _parallelBatchEl !== 'undefined') {
    _parallelBatchEl = null;
  }

  const key = 'user-' + idx;
  // ★ 始终创建 vanilla DOM 气泡，消息列表完全由 vanilla DOM 管理
  //   （Preact MessageList 已禁用，避免双容器导致的顺序错乱）
  if (!bubbles.has(key)) {
    const el = addBubble('user');
    const header = document.createElement('div');
    header.className = 'header';
    header.innerHTML = '<span class="msg-tag">#' + idx + '</span>';
    el.appendChild(header);
    const contentEl = document.createElement('div');
    contentEl.className = 'bubble-content';
    contentEl.textContent = data.content || '';
    el.appendChild(contentEl);
    const tsEl = document.createElement('div');
    tsEl.className = 'timestamp';
    tsEl.textContent = new Date().toLocaleTimeString();
    el.appendChild(tsEl);
    bubbles.set(key, el);
  }

  // 同步更新 store（供 editmsg 等非渲染功能使用）
  const st = typeof window._st === 'function' ? window._st() : (window.__store || null);
  if (st && typeof st.addMessage === 'function') {
    st.addMessage(key, {
      type: 'user', msgIndex: idx, content: data.content,
      timestamp: new Date().toLocaleTimeString(),
    });
  }

  // ★ 修复：用户消息发送后立即滚动到底部，确保 scroll-sentinel 在末尾
  scrollToBottom();
  // ★ 重置 fence 状态，防止跨对话污染
  _fenceState.countBeforeSearchFrom = 0;
  _fenceState.lastSearchFrom = 0;
}

function handleReasoningChunk(data) {
  console.log('[DEBUG] reasoning_chunk received:', data.msg_index, data.text.substring(0, 50));
  // 后台未读计数 + 页面标题更新
  if (window._bgTitleManager) window._bgTitleManager.notifyNewMessage();
  // 更新生成状态字符计数
  if (typeof window._addGenChars === 'function') {
    window._addGenChars(data.text.length);
  }
  const idx = data.msg_index;
  const key = 'assistant-' + idx;

  // ★ 流式渲染优化：直接操作 vanilla DOM 实现 O(1) 文本追加
  //    不更新 Preact store — 避免全量消息列表重渲染 + marked.js 解析
  //    phase_done 时一次更新 store 触发 Preact 最终渲染
  const st = typeof window._st === 'function' ? window._st() : null;
  if (st) {
    _appendStreamingText(key, idx, data.text, 'think');
  } else {
    _ensureAssistantBubble(idx, data.text, 'think');
  }
}

function handleContentChunk(data) {
  console.log('[DEBUG] content_chunk received:', data.msg_index, data.text.substring(0, 50));
  // 后台未读计数 + 页面标题更新
  if (window._bgTitleManager) window._bgTitleManager.notifyNewMessage();
  // 更新生成状态字符计数
  if (typeof window._addGenChars === 'function') {
    window._addGenChars(data.text.length);
  }
  const idx = data.msg_index;
  const key = 'assistant-' + idx;

  // ★ 同 handleReasoningChunk — O(1) 文本追加，不触发 Preact
  const st = typeof window._st === 'function' ? window._st() : null;
  if (st) {
    _appendStreamingText(key, idx, data.text, 'content');
  } else {
    _ensureAssistantBubble(idx, data.text, 'content');
  }
}

/**
 * 取消指定 DOM 元素的待处理增量渲染，防止 setTimeout 回电在
 * phase_done 设置 final HTML 后覆盖完整内容。
 * 返回 true 表示有渲染被取消，false 表示无待处理渲染。
 */
function _cancelPendingRender(el) {
  if (!el) return false;
  const had = _pendingRenders.delete(el);
  // 如果所有待处理渲染都已取消，清理 rafId 定时器
  if (_rafId && _pendingRenders.size === 0) {
    clearTimeout(_rafId);
    _rafId = null;
  }
  return had;
}

function handlePhaseDone(data) {
  const idx = data.msg_index;
  const key = 'assistant-' + idx;

  if (data.phase === 'reasoning') {
    // reasoning 阶段结束：清理 think-section 的待处理渲染
    const el = bubbles.get(key);
    if (el) {
      const thinkSection = el.querySelector('.think-section');
      _cancelPendingRender(thinkSection);
    }
    return;
  }

  if (data.phase === 'content') {
    // 从 streaming buffer 中取完整的累积文本
    const fullAnswer = _streamingText.get(key + '-content') || '';
    console.log('[handlers] phase_done content:', { idx, fullAnswerLen: fullAnswer.length });

    // 用 markdown 引擎渲染完整 Markdown 到流式气泡中
    if (fullAnswer) {
      const el = bubbles.get(key);
      if (el) {
        // 替换 answer-section 内容
        let answerSection = el.querySelector('.answer-section');
        if (!answerSection) {
          answerSection = document.createElement('div');
          answerSection.className = 'answer-section';
          el.appendChild(answerSection);
        }

        // ★★★ 关键修复：取消所有待处理的增量渲染，防止 setTimeout 回电
        //     用流式不完整文本覆盖 phase_done 设置的完整 markdown ★★★
        _cancelPendingRender(answerSection);
        // 同时也取消 think-section 的待处理渲染
        const thinkSection = el.querySelector('.think-section');
        _cancelPendingRender(thinkSection);

        // ★ 重置增量渲染状态，确保后续不会有陈旧渲染覆盖
        _renderState.delete(answerSection);

        const md = (typeof window._renderMarkdownFallback === 'function'
          ? window._renderMarkdownFallback(fullAnswer)
          : fullAnswer.replace(/\n/g, '<br>'));
        answerSection.innerHTML = md;
        if (typeof window.postProcessMarkdown === 'function') {
          // ★ 修复：后台标签页时 requestAnimationFrame 暂停，
          //   改用 setTimeout 确保 phase_done 后正常执行后处理
          setTimeout(() => {
            window.postProcessMarkdown(answerSection);
            // ★ postProcessMarkdown 可能扩展 KaTeX/Mermaid 高度 → 完成后滚动到底部
            if (typeof window._debouncedScrollToBottom === 'function') {
              window._debouncedScrollToBottom();
            }
          }, 0);
        }
        // 更新标题时间戳
        const header = el.querySelector('.header');
        if (header) {
          const oldTime = header.querySelector('.timestamp');
          if (oldTime) oldTime.remove();
          const timeSpan = document.createElement('span');
          timeSpan.className = 'timestamp';
          timeSpan.textContent = new Date().toLocaleTimeString();
          header.appendChild(timeSpan);
        }
      }
    }

    // 同步更新 store（供 editmsg 等非渲染功能使用）
    const st = typeof window._st === 'function' ? window._st() : null;
    if (st && fullAnswer) {
      const fullThink = _streamingText.get(key + '-think') || '';
      st.addMessage(key, {
        type: 'assistant', msgIndex: idx,
        thinkRaw: fullThink, answerRaw: fullAnswer,
        timestamp: new Date().toLocaleTimeString(),
      });
    }

    // ★ phase_done 渲染完整 Markdown 后立即滚动到底部（内容可能比流式尾段更高）
    if (typeof window._debouncedScrollToBottom === 'function') {
      window._debouncedScrollToBottom();
    }

    // ★ 清理 streaming buffer：消息已完成，不再需要流式缓冲
    _streamingText.delete(key + '-think');
    _streamingText.delete(key + '-content');

    // ★ 重置 fence 状态，防止跨对话污染
    _fenceState.countBeforeSearchFrom = 0;
    _fenceState.lastSearchFrom = 0;

  }
}

/* ═══════════════════════════════════════════════════════════════
   导出到全局（供 index.html 传统 script 加载使用）
   ═══════════════════════════════════════════════════════════════ */

window._streamingText = _streamingText;
window._renderState = _renderState;
window._fenceState = _fenceState;
window._findSafeCommitEnd = _findSafeCommitEnd;
window._scheduleIncrementalRender = _scheduleIncrementalRender;
window._doIncrementalRender = _doIncrementalRender;
window._appendStreamingText = _appendStreamingText;
window._ensureAssistantBubble = _ensureAssistantBubble;
window.handleUserMessage = handleUserMessage;
window.handleReasoningChunk = handleReasoningChunk;
window.handleContentChunk = handleContentChunk;
window.handlePhaseDone = handlePhaseDone;

/* ── 导出到 __streaming 命名空间（供其他 handler 文件使用） ── */
window.__streaming = {
  _streamingText, _renderState, _fenceState,
  _findSafeCommitEnd, _scheduleIncrementalRender, _doIncrementalRender,
  _appendStreamingText, _ensureAssistantBubble,
  handleUserMessage, handleReasoningChunk, handleContentChunk, handlePhaseDone,
};
