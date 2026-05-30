/* ═══════════════════════════════════════════════════════════════
   MessageList — 消息列表 Preact 组件
   订阅 store，读取 state.messages 并渲染气泡列表
   消息按 key 中提取的 idx 排序显示
   新消息到达时自动滚动到底部
   ═══════════════════════════════════════════════════════════════ */
import { h } from '../lib/preact.module.js';
import htm from '../lib/htm.module.js';
import { useState, useEffect, useRef } from '../lib/hooks.module.js';
import { getState, subscribe, getMessages } from '../state/store.js';
import { Bubble } from './Bubble.js';

const html = htm.bind(h);

/* ── 工具函数 ──────────────────────────────────────────── */

/**
 * 从消息 key 中提取数值索引用于排序
 * key 格式: "user-0", "assistant-1", "tool-calc", "agent-2"
 * 提取最后一个 '-' 之后的部分，若能转为数值则返回该数值，否则返回 Infinity
 * @param {string} key
 * @returns {number}
 */
function getMsgIndex(key) {
  const parts = key.split('-');
  const last = parts[parts.length - 1];
  const n = parseInt(last, 10);
  return isNaN(n) ? Number.MAX_SAFE_INTEGER : n;
}

/**
 * 从消息 key 推断气泡类型
 * @param {string} key
 * @returns {'user' | 'assistant' | 'tool' | 'agent'}
 */
function getBubbleType(key) {
  if (key.startsWith('user-')) return 'user';
  if (key.startsWith('assistant-')) return 'assistant';
  if (key.startsWith('tool-')) return 'tool';
  if (key.startsWith('agent-')) return 'agent';
  // 默认 fallback
  return 'user';
}

/* ── MessageList 组件 ──────────────────────────────────── */

/**
 * MessageList — 消息列表组件
 *
 * 从 store 读取 state.messages（对象 { key: msgData }），
 * 转换为按 idx 排序的数组，遍历渲染 Bubble 组件。
 * 订阅 store，当 state.messages 变化时自动重新渲染，
 * 并自动滚动到列表底部。
 */
export function MessageList() {
  const [messages, setMessages] = useState(getMessages());
  const listEndRef = useRef(null);
  const throttleRef = useRef(null);  // 节流定时器
  const latestRef = useRef(null);    // 最新 state 引用

  // 订阅 store，带节流控制 — 快速流式 chunk 合并到 ~100ms 窗口内批处理
  // 避免每 50ms 一个 chunk 就重新渲染全量消息列表（O(N) per chunk）
  useEffect(() => {
    const unsub = subscribe((state) => {
      latestRef.current = state;
      if (!throttleRef.current) {
        throttleRef.current = setTimeout(() => {
          throttleRef.current = null;
          const s = latestRef.current;
          if (s) {
            setMessages(s.messages ? { ...s.messages } : {});
          }
        }, 100);
      }
    });
    return () => {
      if (throttleRef.current) {
        clearTimeout(throttleRef.current);
        throttleRef.current = null;
      }
      // 卸载前刷新最终状态，确保不丢数据
      if (latestRef.current) {
        const s = latestRef.current;
        setMessages(s.messages ? { ...s.messages } : {});
      }
      unsub();
    };
  }, []);

  // 将 messages 对象转为排序后的条目数组
  const sorted = Object.entries(messages)
    .map(([key, msg]) => ({ key, msg, idx: getMsgIndex(key) }))
    .sort((a, b) => a.idx - b.idx);

  // 每次消息列表变化（新增/内容流式更新）时自动滚动到底部
  useEffect(() => {
    if (listEndRef.current) {
      listEndRef.current.scrollIntoView({ behavior: 'smooth', block: 'end' });
    }
  });

  // 空状态
  if (sorted.length === 0) {
    return html`
      <div class="message-list empty-state">
        <p class="empty-hint">暂无消息</p>
      </div>
    `;
  }

  return html`
    <div class="message-list">
      ${sorted.map(({ key, msg }) => {
        const type = getBubbleType(key);
        const msgIdx = msg.msgIndex != null ? msg.msgIndex : getMsgIndex(key);
        return html`
          <${Bubble}
            key=${key}
            type=${type}
            msgIndex=${msgIdx}
            content=${msg.content || ''}
            thinkRaw=${msg.thinkRaw || ''}
            answerRaw=${msg.answerRaw || ''}
            timestamp=${msg.timestamp || ''}
            toolName=${msg.toolName || ''}
            toolStatus=${msg.toolStatus || ''}
            toolOutput=${msg.toolOutput || ''}
            toolMeta=${msg.toolMeta || ''}
            agentDesc=${msg.agentDesc || ''}
            agentStatus=${msg.agentStatus || ''}
            agentPhase=${msg.agentPhase || ''}
            agentTools=${msg.agentTools || null}
            agentUsage=${msg.agentUsage || ''}
            agentResult=${msg.agentResult || ''}
            agentError=${msg.agentError || ''}
          />
        `;
      })}
      <div ref=${listEndRef} class="scroll-anchor"></div>
    </div>
  `;
}
