/* ═══════════════════════════════════════════════════════════════
   AgentCard — Agent 卡片 Preact 组件
   从 store 响应式读取 agents 数据，渲染 Agent 气泡
   树形终端风格（Claude Code 样式）
   ═══════════════════════════════════════════════════════════════ */
import { h } from '../lib/preact.module.js';
import htm from '../lib/htm.module.js';
import { useState, useEffect, useRef } from '../lib/hooks.module.js';
import { getState, subscribe, getAgents } from '../state/store.js';
import { getPhaseText, getStatusIcon, getToolStatusIcon, getTokenParts, getAgentElapsed, renderAgentResult } from './agent-utils.js';

const html = htm.bind(h);

const escapeHtml = (s) => window.escapeHtml ? window.escapeHtml(s) : String(s);

/* ═══════════════════════════════════════════════════════════════
   Agent 卡片 — 单个 Agent 气泡渲染
   @param {object} agent - Agent 数据
   @param {boolean} inline - true=作为 dispatch_agent 子项渲染（树形紧凑样式）
   ═══════════════════════════════════════════════════════════════ */
export function AgentCard({ agent, inline }) {
  if (!agent) return null;

  const inlineStyle = inline ? true : false;

  // ── 实时计时器（所有模式） ──────────────────────────
  const [curTime, setCurTime] = useState(Date.now());

  useEffect(() => {
    // 未完成时启动实时计时（包括内联模式，确保子 Agent 耗时也能实时更新）
    if (agent.status !== 'done' && agent.status !== 'completed' && agent.status !== 'fail' && agent.status !== 'error') {
      const timer = setInterval(() => setCurTime(Date.now()), 200);
      return () => clearInterval(timer);
    }
  }, [agent.status, agent.label]);

  // ── 状态图标 ────────────────────────────────────────
  const statusIcon = getStatusIcon(agent);

  // ── 阶段文本 ────────────────────────────────────────
  const phaseText = getPhaseText(agent);

  // ── 工具记录 ────────────────────────────────────────
  const toolEntries = (agent.tools || []).slice(-3).reverse();

  // ── 实时耗时 ────────────────────────────────────────
  const elapsedStr = getAgentElapsed(agent, curTime);

  // ── token 行（紧凑格式：1.2k out · 12.3s） ──────────
  const tokParts = getTokenParts(agent);

  // ── 内联模式（dispatch_agent 子项 — 树形紧凑样式） ──
  if (inlineStyle) {
    return html`
      <div class="tree-node">
        <div class="agent-title-line">
          <span class="tree-connector">├</span>
          <span class="title-content">
            ${statusIcon}
            <span class="agent-name">${escapeHtml(agent.description || agent.label)}</span>
            ${tokParts.length > 0 ? html`<span class="token-info">${tokParts.join(' · ')}</span>` : ''}
            ${elapsedStr ? html`<span class="elapsed-info">${elapsedStr}</span>` : ''}
          </span>
        </div>
        ${agent.phase ? html`
          <div class="agent-phase-line">
            <span class="tree-connector">│</span>
            <span class="phase-content">${phaseText}</span>
          </div>
        ` : ''}
        ${toolEntries.map((trec) => {
          const tname = trec.tool_name;
          // Bug 2 修复：未完成的工具用 curTime 实时计算耗时，已完成的用静态 elapsed
          const toolElapsed = (trec.startTime && trec.status !== 'done')
            ? ((curTime - trec.startTime) / 1000).toFixed(1) + 's'
            : (trec.elapsed ? trec.elapsed + 's' : '');
          return html`
          <div class="tool-record-line">
            <span class="tree-connector">│</span>
            <span class="tool-content">
              ${getToolStatusIcon(trec)}
              ${tname === 'bash' ? html`<span class="cmd-icon">⚡</span>` : ''}
              <span class="tool-name">${escapeHtml(tname)}</span>
              ${toolElapsed ? html`<span class="elapsed-info">${toolElapsed}</span>` : ''}
            </span>
          </div>
        `;
        })}
        ${agent.error ? html`
          <div class="tool-record-line">
            <span class="tree-connector">│</span>
            <span class="error-content">错误: ${escapeHtml(agent.error)}</span>
          </div>
        ` : ''}
        ${agent.result ? html`
          <div class="agent-result" dangerouslySetInnerHTML=${{__html: renderAgentResult(agent.result)}} />
        ` : ''}
      </div>
    `;
  }

  // ── 完整模式（独立 Agent 气泡 — 树形终端风格） ────
  // 树形连接符：作为独立根节点使用 ├ 开头
  const conn = '├';
  const subConn = '│';

  // ★ Markdown 渲染结果（用于 standalone agent result）
  const mdResultHtml = renderAgentResult(agent.result);

  return html`
    <div class="tree-agent-card">
      <div class="agent-title-line">
        <span class="tree-connector">${conn}</span>
        <span class="title-content">
          ${agent.msg_index !== undefined ? html`<span class="msg-tag" style="font-size:9px;padding:0 4px;">#${agent.msg_index}</span>` : ''}
          ${statusIcon}
          <span class="agent-name">${escapeHtml(agent.description || agent.label)}</span>
          ${tokParts.length > 0 ? html`<span class="token-info">${tokParts.join(' · ')}</span>` : ''}
          ${elapsedStr ? html`<span class="elapsed-info">${elapsedStr}</span>` : ''}
        </span>
      </div>
      ${agent.phase ? html`
        <div class="agent-phase-line">
          <span class="tree-connector">${subConn}</span>
          <span class="phase-content">${phaseText}</span>
        </div>
      ` : ''}
      ${toolEntries.map((trec) => {
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
      ${agent.error ? html`
        <div class="tool-record-line">
          <span class="tree-connector">${subConn}</span>
          <span class="error-content">错误: ${escapeHtml(agent.error)}</span>
        </div>
      ` : ''}
      ${mdResultHtml ? html`
        <div class="agent-result" dangerouslySetInnerHTML=${{__html: mdResultHtml}} />
      ` : ''}
    </div>
  `;
}

/* ═══════════════════════════════════════════════════════════════
   Agent 卡片列表 — 订阅 store 并渲染全部 Agent
   只显示独立 Agent（dispatchLabel 为空），dispatch_agent 子项由 ToolRow 内联渲染
   ═══════════════════════════════════════════════════════════════ */
export function AgentCardList() {
  const [agents, setAgents] = useState({});

  useEffect(() => {
    setAgents(getAgents());

    const unsub = subscribe((state) => {
      setAgents({ ...state.agents });
    });
    return unsub;
  }, []);

  // 只显示独立 Agent（非 dispatch_agent 子项）
  const standaloneAgents = Object.values(agents).filter(a => !a.dispatchLabel);

  if (standaloneAgents.length === 0) return html`<div style="display:none;"></div>`;

  return html`
    <div class="preact-agents">
      ${standaloneAgents.map(a => html`<${AgentCard} agent=${a} inline=${false} key=${a.label} />`)}
    </div>
  `;
}
