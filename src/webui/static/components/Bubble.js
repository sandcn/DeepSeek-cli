/* ═══════════════════════════════════════════════════════════════
   Bubble — 消息气泡 Preact 组件
   纯展示组件（Dumb Component），通过 props 接收数据
   支持 user / assistant / tool / agent 四种气泡类型
   
   Markdown 渲染：使用 renderMarkdownToHtml 通过 innerHTML 注入
   避免额外 wrapper div，保持与 vanilla DOM 气泡结构一致
   ═══════════════════════════════════════════════════════════════ */
import { h } from '../lib/preact.module.js';
import htm from '../lib/htm.module.js';
import { useEffect, useRef } from '../lib/hooks.module.js';
import { renderMarkdownToHtml } from '../md-engine.js';

const html = htm.bind(h);

// ── 增量渲染跟踪 ────────────────────────────────────────
// WeakMap: DOM元素 → { committedLen, hasTail }
// 跟踪每个元素已「提交」到 DOM 的字符位置，避免每次全量重渲染
const _renderTracker = new WeakMap();

/**
 * 在完整 markdown 文本中查找安全的块级提交边界。
 * 安全的边界 = \n\n 且不在代码 fence 内部。
 * 返回边界后的字符位置（包含 \n\n），若无则返回 committedLen。
 */
function _findSafeCommitEnd(fullText, committedLen) {
  const searchFrom = Math.max(committedLen, 0);
  if (searchFrom >= fullText.length) return committedLen;

  const searchText = fullText.slice(searchFrom);

  // 定位代码 fence 位置
  const fences = [];
  const fenceRe = /```/g;
  let m;
  while ((m = fenceRe.exec(searchText)) !== null) {
    fences.push(m.index);
  }

  function insideFence(pos) {
    let count = 0;
    for (const f of fences) {
      if (f > pos) break;
      count++;
    }
    return count % 2 === 1;
  }

  // 从末尾往前找最后一个安全的 \n\n
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
  return committedLen; // 无安全边界 → 全部视为 streaming
}

/**
 * 增量渲染：只渲染新增块，追加到 DOM 而非替换 innerHTML。
 *
 * 复杂度分析：
 *   - 旧方案（_doRenderMd）：每 chunk 渲染全量文本 → O(n²)
 *   - 新方案（_incrementalRender）：每个块只渲染 1 次 → O(n)
 *
 * 算法：
 *   ① 从 committedLen 往后找安全边界（\n\n 不在 fence 内）
 *   ② 边界前 → 已完整块，渲染 1 次即 append 到 DOM
 *   ③ 边界后 → streaming 尾块，每次更新重渲染（通常很短）
 */
function _incrementalRender(el, fullText, postProcess = true) {
  if (!el) return;

  let state = _renderTracker.get(el);
  if (!state) {
    state = { committedLen: 0 };
    _renderTracker.set(el, state);
  }

  // 文本被截断或清空 → 全量重渲染
  if (fullText.length < state.committedLen || fullText.length === 0) {
    state.committedLen = 0;
    el.innerHTML = '';
    if (!fullText) return;
  }

  // 首次渲染、或 committedLen 未初始化 → 全量渲染
  if (state.committedLen === 0) {
    const md = renderMarkdownToHtml(fullText);
    el.innerHTML = md;
    state.committedLen = fullText.length;
    if (postProcess && typeof window.postProcessMarkdown === 'function') {
      requestAnimationFrame(() => window.postProcessMarkdown(el));
    }
    return;
  }

  // ── 增量路径：文本变长了 ────────────────────────────
  const safeEnd = _findSafeCommitEnd(fullText, state.committedLen);

  // 提交新完成的块
  if (safeEnd > state.committedLen) {
    const commitText = fullText.slice(state.committedLen, safeEnd);
    const commitHtml = renderMarkdownToHtml(commitText);
    const wrapper = document.createElement('div');
    wrapper.innerHTML = commitHtml;
    const newNodes = [];
    while (wrapper.firstChild) {
      el.appendChild(wrapper.firstChild);
      newNodes.push(el.lastChild);
    }
    state.committedLen = safeEnd;
    if (postProcess && typeof window.postProcessMarkdown === 'function' && newNodes.length > 0) {
      // ★ 优化：只对新追加的子节点做后处理，不扫描整个 el
      const batchContainer = document.createElement('div');
      for (const node of newNodes) batchContainer.appendChild(node.cloneNode(true));
      requestAnimationFrame(() => window.postProcessMarkdown(batchContainer));
    }
  }

  // ★ 打印机效果已移除：不渲染 streaming 尾块
  // 未完成的文本（最后一个安全边界之后的内容）暂不显示，
  // 等到累积到完整块边界时再一次性提交渲染。
  // 这样内容只以段落级粒度出现，无逐字逐句的流式效果。
}

// ── RAF 合并调度 ──────────────────────────────────────────
// 收集待更新的 ref → content 映射，在下一帧合并执行
const _pendingUpdates = new Map();
let _rafScheduled = false;

/**
 * 调度一次增量渲染（RAF 合并版本）
 * 同一帧内对同一 el 的多次更新会被合并，只执行最后一次
 */
function _scheduleMdUpdate(el, text, postProcess = true) {
  _pendingUpdates.set(el, { text, postProcess });
  if (!_rafScheduled) {
    _rafScheduled = true;
    requestAnimationFrame(() => {
      _rafScheduled = false;
      for (const [target, { text, postProcess }] of _pendingUpdates) {
        _incrementalRender(target, text, postProcess);
      }
      _pendingUpdates.clear();
    });
  }
}

/**
 * Bubble — 消息气泡组件
 *
 * Props:
 *   type        - 'user' | 'assistant' | 'tool' | 'agent'
 *   msgIndex    - 消息序号
 *   content     - Markdown 内容（user 气泡用）
 *   thinkRaw    - reasoning 内容（assistant 气泡用）
 *   answerRaw   - answer 内容（assistant 气泡用）
 *   timestamp   - 时间戳文本
 *   toolName    - 工具名称
 *   toolStatus  - 工具阶段文本
 *   toolOutput  - 工具输出 Markdown 文本
 *   toolMeta    - 元信息文本
 *   agentDesc   - Agent 描述
 *   agentStatus - Agent 状态
 *   agentPhase  - Agent 阶段
 *   agentTools  - Agent 工具列表
 *   agentUsage  - Agent 用量信息
 *   agentResult - Agent 执行结果 Markdown 文本
 *   agentError  - Agent 错误信息 Markdown 文本
 *   children    - 子元素
 */
export function Bubble(props) {
  const {
    type, msgIndex, content, thinkRaw, answerRaw,
    timestamp, toolName, toolStatus, toolOutput, toolMeta,
    agentDesc, agentStatus, agentPhase, agentTools, agentUsage,
    agentResult, agentError,
    children,
  } = props;

  const rootRef = useRef(null);
  const contentRef = useRef(null);
  const thinkRef = useRef(null);
  const answerRef = useRef(null);
  const toolOutputRef = useRef(null);
  const agentResultRef = useRef(null);
  const agentErrorRef = useRef(null);

  // ── 各区域的 Markdown 渲染（RAF 合并模式）───────────────
  useEffect(() => { _scheduleMdUpdate(contentRef.current, content); }, [content]);
  useEffect(() => { _scheduleMdUpdate(thinkRef.current, thinkRaw); }, [thinkRaw]);
  useEffect(() => { _scheduleMdUpdate(answerRef.current, answerRaw); }, [answerRaw]);
  useEffect(() => { _scheduleMdUpdate(toolOutputRef.current, toolOutput); }, [toolOutput]);
  useEffect(() => { _scheduleMdUpdate(agentResultRef.current, agentResult); }, [agentResult]);
  useEffect(() => { _scheduleMdUpdate(agentErrorRef.current, agentError); }, [agentError]);

  // ── User 气泡 ──────────────────────────────────────────
  if (type === 'user') {
    return html`
      <div ref=${rootRef} class="bubble user">
        <div class="header">
          <span class="msg-tag">#${msgIndex}</span>
        </div>
        <div ref=${contentRef} class="bubble-content"></div>
        ${timestamp ? html`<div class="timestamp">${timestamp}</div>` : ''}
      </div>
    `;
  }

  // ── Assistant 气泡 ─────────────────────────────────────
  if (type === 'assistant') {
    return html`
      <div ref=${rootRef} class="bubble answer">
        <div class="header">
          <span class="msg-tag">#${msgIndex} 🤖</span>
        </div>
        ${thinkRaw
          ? html`<div ref=${thinkRef} class="think-section"></div>`
          : ''}
        ${answerRaw
          ? html`<div ref=${answerRef} class="answer-section"></div>`
          : ''}
        ${timestamp ? html`<div class="timestamp">${timestamp}</div>` : ''}
      </div>
    `;
  }

  // ── Tool 气泡 ──────────────────────────────────────────
  if (type === 'tool') {
    return html`
      <div ref=${rootRef} class="bubble tool">
        <div class="tool-header">
          <span class="icon">🔧</span>
          ${toolName || ''}
        </div>
        ${toolStatus
          ? html`<div class="tool-phase">${toolStatus}</div>`
          : ''}
        ${toolOutput
          ? html`<div ref=${toolOutputRef} class="tool-output"></div>`
          : ''}
        ${toolMeta
          ? html`<div class="tool-meta">${toolMeta}</div>`
          : ''}
        ${children ? html`<div class="tool-children">${children}</div>` : ''}
        ${timestamp ? html`<div class="timestamp">${timestamp}</div>` : ''}
      </div>
    `;
  }

  // ── Agent 气泡 ─────────────────────────────────────────
  if (type === 'agent') {
    return html`
      <div ref=${rootRef} class="bubble agent">
        <div class="agent-header">
          <span class="icon">🤖</span>
          ${agentDesc
            ? html`<span class="agent-desc">${agentDesc}</span>`
            : ''}
          ${agentStatus
            ? html`<span class="agent-status-badge">${agentStatus}</span>`
            : ''}
        </div>
        ${agentPhase
          ? html`<div class="agent-phase">${agentPhase}</div>`
          : ''}
        ${agentTools && agentTools.length > 0
          ? html`
              <div class="agent-tools">
                ${agentTools.map(
                  (tool, i) => html`<span key=${i} class="agent-tool-tag">${tool}</span>`
                )}
              </div>
            `
          : ''}
        ${agentUsage
          ? html`<div class="agent-usage">${agentUsage}</div>`
          : ''}
        ${agentResult
          ? html`<div ref=${agentResultRef} class="agent-result"></div>`
          : ''}
        ${agentError
          ? html`<div ref=${agentErrorRef} class="agent-error"></div>`
          : ''}
        ${children ? html`<div class="agent-children">${children}</div>` : ''}
        ${timestamp ? html`<div class="timestamp">${timestamp}</div>` : ''}
      </div>
    `;
  }

  return null;
}
