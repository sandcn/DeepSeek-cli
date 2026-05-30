/* ═══════════════════════════════════════════════════════════════
   ToolRow — 工具行 Preact 组件
   从 store 响应式读取 tools 数据，渲染工具行气泡
   逐步替代 handlers.js 中的 DOM 操作
   ═══════════════════════════════════════════════════════════════ */
import { h } from '../lib/preact.module.js';
import htm from '../lib/htm.module.js';
import { useState, useEffect, useRef } from '../lib/hooks.module.js';
import { getState, subscribe, getTools, getAgents } from '../state/store.js';
import { getPhaseText, getStatusIcon, getToolStatusIcon, getTokenParts, getAgentElapsed } from './agent-utils.js';

const html = htm.bind(h);

/* ── 通过 window 访问传统脚本中的工具函数 ────────────────── */
const escapeHtml = (s) => window.escapeHtml ? window.escapeHtml(s) : String(s);

/* ═══════════════════════════════════════════════════════════════
   工具行 — 单个工具气泡渲染
   ═══════════════════════════════════════════════════════════════ */
export function ToolRow({ tool }) {
  const outputRef = useRef(null);
  const [curTime, setCurTime] = useState(Date.now());

  // ── 计时器：解析/执行阶段每秒更新耗时 ──────────────
  useEffect(() => {
    if (!tool || tool.phase === 'done') return;
    const timer = setInterval(() => setCurTime(Date.now()), 200);
    return () => clearInterval(timer);
  }, [tool?.phase, tool?.label]);

  // ── 特殊渲染（readFile/diff）通过 ref 操作 DOM ──────
  useEffect(() => {
    if (!outputRef.current) return;
    const el = outputRef.current;

    // done 阶段的 output_preview 特殊渲染
    if (tool.phase === 'done' && (tool.output_preview || tool.tool_name === 'write_file' || tool.tool_name === 'update_file')) {
      el.innerHTML = '';
      el.style.display = 'block';
      if (tool.tool_name === 'read_file' && tool.success && window.renderReadFileOutput) {
        el.style.maxHeight = 'none';
        el.style.overflowY = 'visible';
        window.renderReadFileOutput(el, tool.output_preview);
      } else if (tool.tool_name === 'write_file' || tool.tool_name === 'update_file') {
        el.style.maxHeight = 'none';
        el.style.overflowY = 'visible';
        if (tool.metadata?.diff_data && window.renderWebDiff) {
          el.innerHTML = '';
          window.renderWebDiff(el, tool.metadata.diff_data);
        } else if (window.renderAnsiDiff) {
          window.renderAnsiDiff(el, tool.output_preview);
        }
      } else if (tool.tool_name === 'bash') {
        const cmdLine = tool.cmdLine || '$ ' + escapeHtml(tool.tool_name || 'bash');
        el.innerHTML = '<span class="cmd-line">' + escapeHtml(cmdLine) + '</span>\n'
          + '<span class="cmd-output">' + escapeHtml(tool.output_preview) + '</span>';
      } else {
        el.textContent = tool.output_preview;
      }
      return;
    }

    // executing 阶段显示 cmdLine
    if (tool.phase === 'executing') {
      if (tool.tool_name === 'bash' && tool.cmdLine) {
        el.innerHTML = '<span class="cmd-line">' + escapeHtml(tool.cmdLine) + '</span>\n';
        el.style.display = 'block';
      }
      return;
    }
  }, [tool.phase, tool.label, tool.output_preview, tool.arguments, tool.cmdLine, tool.tool_name, tool.success]);

  if (!tool) return null;

  const isDispatch = tool.tool_name === 'dispatch_agent';
  const startTs = tool.execStart || tool.parsingStart || Date.now();
  const elapsedStr = ((curTime - startTs) / 1000).toFixed(1);

  // ── 阶段状态行 ──────────────────────────────────────
  let phaseContent;
  if (tool.phase === 'parsing') {
    const argTokens = window._estimateTokens ? window._estimateTokens(tool.arguments || '') : Math.round((tool.arguments||'').length / 4);
    phaseContent = html`<span class="spinner"></span> 接收参数中 ${argTokens}T ${elapsedStr}s`;
  } else if (tool.phase === 'executing') {
    phaseContent = html`<span class="spinner"></span> 执行中 ${escapeHtml(tool.tool_name || '工具')} ${elapsedStr}s`;
  } else {
    // done（显示最终耗时）
    phaseContent = tool.success
      ? html`<span class="tick">✓</span> ${escapeHtml(tool.tool_name || '工具')} 完成 <span class="tool-duration">${elapsedStr}s</span>`
      : html`<span class="cross">✗</span> ${escapeHtml(tool.tool_name || '工具')} 失败 <span class="tool-duration">${elapsedStr}s</span>`;
  }

  // ── 元数据显示 ──────────────────────────────────────
  const metaParts = [];
  if (tool.metadata) {
    if (tool.metadata['参数']) metaParts.push('参数: ' + tool.metadata['参数']);
    if (tool.metadata['输出']) metaParts.push('输出: ' + tool.metadata['输出']);
    if (tool.metadata['行数']) metaParts.push('行数: ' + tool.metadata['行数']);
  }

  // ── dispatch_agent 子 Agent 列表 ───────────────────
  let childAgents = [];
  if (isDispatch) {
    const allAgents = getAgents();
    childAgents = Object.values(allAgents).filter(a => a.dispatchLabel === tool.label);
  }

  // ── 是否显示 output 区域 ────────────────────────────
  const isWriteOrUpdateFile = tool.tool_name === 'write_file' || tool.tool_name === 'update_file';
  const showOutput = isDispatch
    || (tool.phase === 'parsing' && tool.arguments)
    || (tool.phase === 'executing' && (tool.cmdLine || tool._hasStreamedOutput))
    || (tool.phase === 'done' && (tool.output_preview || isWriteOrUpdateFile));

  return html`
    <div class="tool-single-row">
      <div class="tool-header">
        ${tool.msg_index !== undefined ? html`<span class="msg-tag">#${tool.msg_index}</span>` : ''}
        <span class="icon small">${isDispatch ? '⚙' : '🔧'}</span>
        ${escapeHtml(tool.tool_name || '工具')}
      </div>
      <div class="tool-phase">${phaseContent}</div>
      ${showOutput ? html`
        <div class="tool-output" style="display:block;${isDispatch ? 'max-height:none;overflow-y:visible;font-family:var(--font-mono);white-space:normal;font-size:13px;padding:0;background:transparent;' : ''}"
             ref=${outputRef}>
          ${isDispatch ? html`
            <div class="dispatch-agent-container tree-view">
              ${childAgents.map((a, idx) => {
                const isLast = idx === childAgents.length - 1;
                const conn = isLast ? '└' : '├';
                const subConn = isLast ? ' ' : '│';

                // ── 状态图标 ──
                const statusIcon = getStatusIcon(a);

                // ── token 用量 ──
                const tokParts = getTokenParts(a);
                const tokStr = tokParts.length ? tokParts.join(' · ') : '';

                // ── 耗时 ──
                const agentElapsed = getAgentElapsed(a, curTime);

                // ── 阶段描述 ──
                const phaseDesc = getPhaseText(a);

                return html`
                  <div class="tree-node">
                    <div class="agent-title-line">
                      <span class="tree-connector">${conn}</span>
                      <span class="title-content">
                        ${statusIcon}
                        <span class="agent-name">${escapeHtml(a.description || a.label)}</span>
                        ${tokStr ? html`<span class="token-info">${tokStr}</span>` : ''}
                        ${agentElapsed ? html`<span class="elapsed-info">${agentElapsed}</span>` : ''}
                      </span>
                    </div>
                    ${a.phase ? html`
                      <div class="agent-phase-line">
                        <span class="tree-connector">${subConn}</span>
                        <span class="phase-content">${phaseDesc}</span>
                      </div>
                    ` : ''}
                    ${(a.tools || []).slice(-3).reverse().map((trec) => {
                      const tname = trec.tool_name;
                      // Bug 2 修复：未完成的工具用 curTime 实时计算耗时，已完成的用静态 elapsed
                      const toolElapsed = (trec.startTime && trec.status !== 'done')
                        ? ((curTime - trec.startTime) / 1000).toFixed(1) + 's'
                        : (trec.elapsed ? trec.elapsed + 's' : '');
                      return html`
                        <div class="tool-record-line">
                          <span class="tree-connector">${subConn}</span>
                          <span class="tool-content">
                            ${getToolStatusIcon(trec)}
                            ${tname === 'bash' ? html`<span class="cmd-icon">⚡</span>` : ''}
                            <span class="tool-name">${escapeHtml(tname)}</span>
                            ${toolElapsed ? html`<span class="elapsed-info">${toolElapsed}</span>` : ''}
                          </span>
                        </div>
                      `;
                    })}
                    ${a.error ? html`
                      <div class="tool-record-line">
                        <span class="tree-connector">${subConn}</span>
                        <span class="error-content">错误: ${escapeHtml(a.error)}</span>
                      </div>
                    ` : ''}
                    ${a.result ? html`
                      <div class="agent-result">${a.result}</div>
                    ` : ''}
                  </div>
                `;
              })}
            </div>
          ` : ''}
        </div>
      ` : ''}
      ${metaParts.length > 0 ? html`<div class="tool-meta">${metaParts.join(' | ')}</div>` : ''}
    </div>
  `;
}

/* ═══════════════════════════════════════════════════════════════
   工具行列表 — 订阅 store 并渲染全部工具
   ═══════════════════════════════════════════════════════════════ */
export function ToolRowList() {
  const [tools, setTools] = useState({});

  useEffect(() => {
    // 初始化
    setTools(getTools());

    // 订阅 store 变化
    const unsub = subscribe((state) => {
      setTools({ ...state.tools });
    });
    return unsub;
  }, []);

  const toolValues = Object.values(tools);

  // 没有工具时隐藏容器
  if (toolValues.length === 0) return html`<div style="display:none;"></div>`;

  return html`
    <div class="preact-tools">
      ${toolValues.map(t => html`<${ToolRow} tool=${t} key=${t.label} />`)}
    </div>
  `;
}
