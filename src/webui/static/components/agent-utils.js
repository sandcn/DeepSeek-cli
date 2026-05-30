/* ═══════════════════════════════════════════════════════════════
   agent-utils.js — Agent 树形渲染共享工具函数
   提取 AgentCard.js 和 ToolRow.js 中的重复逻辑：
   - getPhaseText(agent) — 阶段文本映射
   - getStatusIcon(agent) — 状态图标 Preact 模板
   - getTokenParts(agent) — token 用量数组
   - getToolStatusIcon(trec) — 工具记录状态图标
   - getAgentElapsed(agent, curTime) — 实时耗时
   ═══════════════════════════════════════════════════════════════ */
import { h } from '../lib/preact.module.js';
import htm from '../lib/htm.module.js';
const html = htm.bind(h);

const escapeHtml = (s) => window.escapeHtml ? window.escapeHtml(s) : String(s);

/**
 * 获取 Agent 阶段文本描述
 * @param {object} agent - Agent 数据对象
 * @returns {string} 阶段文本
 */
export function getPhaseText(agent) {
  if (!agent || !agent.phase) return '';
  switch (agent.phase) {
    case 'thinking': return '...思考中';
    case 'answering': return '✍️ 生成中...';
    case 'parsing': return '🔍 解析中' + (agent.phaseInfo ? ' ' + agent.phaseInfo : '');
    case 'batch': return '📦 批量执行' + (agent.phaseInfo ? ' ' + agent.phaseInfo : '');
    case 'error': return '❌ ' + (agent.phaseInfo || '');
    default: return agent.phase + (agent.phaseInfo ? ' ' + agent.phaseInfo : '');
  }
}

/**
 * 获取 Agent 状态图标（Preact 模板）
 * @param {object} agent - Agent 数据对象
 * @returns {object} Preact 模板
 */
export function getStatusIcon(agent) {
  if (!agent) return null;
  if (agent.status === 'done' || agent.status === 'completed') {
    return html`<span class="tick">✔</span>`;
  } else if (agent.status === 'fail' || agent.status === 'error') {
    return html`<span class="cross">✗</span>`;
  }
  return html`<span class="status-dot running" style="width:6px;height:6px;display:inline-block;border-radius:50%;background:var(--warning);animation:spin 1s linear infinite;"></span>`;
}

/**
 * 获取工具记录状态图标（Preact 模板）
 * @param {object} trec - 工具记录对象
 * @returns {object} Preact 模板
 */
export function getToolStatusIcon(trec) {
  if (!trec) return null;
  if (trec.status === 'done') {
    return trec.success
      ? html`<span class="tick">✔</span>`
      : html`<span class="cross">✗</span>`;
  }
  if (trec.status === 'started') {
    return html`<span style="color:var(--warning);">●</span>`;
  }
  return html`<span style="color:var(--text-dim);">⏳</span>`;
}

/**
 * 获取 Agent token 用量数组（如 ["1.2k out", "0.5k in"]）
 * @param {object} agent - Agent 数据对象
 * @returns {string[]} token 用量字符串数组
 */
export function getTokenParts(agent) {
  const parts = [];
  if (agent && agent.usage) {
    if (agent.usage.output !== undefined) {
      const outStr = window._formatGenTokens ? window._formatGenTokens(agent.usage.output) : String(agent.usage.output);
      parts.push(outStr + ' out');
    }
    if (agent.usage.input !== undefined) {
      const inStr = window._formatGenTokens ? window._formatGenTokens(agent.usage.input) : String(agent.usage.input);
      parts.push(inStr + ' in');
    }
  }
  return parts;
}

/**
 * 计算 Agent 实时耗时
 * @param {object} agent - Agent 数据对象
 * @param {number} curTime - 当前时间戳
 * @returns {string} 耗时字符串（如 "12.3s"）
 */
export function getAgentElapsed(agent, curTime) {
  if (!agent) return '';
  // 有 startTime 时，用 curTime - startTime（无论是否已完成都能反映真实运行时长）
  if (agent.startTime) {
    const endTime = agent._completedAt || curTime;
    return ((endTime - agent.startTime) / 1000).toFixed(1) + 's';
  }
  // 无 startTime 时，使用最早工具 startTime
  const toolRecs = (agent.tools || []);
  if (toolRecs.length > 0) {
    const startTimes = toolRecs.filter(t => t.startTime).map(t => t.startTime);
    if (startTimes.length > 0) {
      const minStart = Math.min(...startTimes);
      return ((curTime - minStart) / 1000).toFixed(1) + 's';
    }
  }
  return '';
}

/**
 * 渲染 Agent 结果 Markdown
 * @param {string} result - Agent 结果文本
 * @returns {string} HTML 字符串
 */
export function renderAgentResult(result) {
  if (!result) return '';
  const p = typeof window.preprocessMathBeforeRender === 'function'
    ? window.preprocessMathBeforeRender(result) : result;
  return typeof window.renderMarkdown === 'function'
    ? window.renderMarkdown(p)
    : escapeHtml(result).replace(/\n/g, '<br>');
}
