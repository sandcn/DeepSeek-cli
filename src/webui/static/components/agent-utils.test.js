/**
 * agent-utils.test.js — Agent 树形渲染工具函数测试
 *
 * 测试覆盖：
 * - getPhaseText — 各阶段文本映射
 * - getStatusIcon — 状态图标（done/fail/running）
 * - getToolStatusIcon — 工具记录状态图标
 * - getTokenParts — token 用量
 * - getAgentElapsed — 实时耗时计算
 * - renderAgentResult — 结果渲染
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  getPhaseText,
  getStatusIcon,
  getToolStatusIcon,
  getTokenParts,
  getAgentElapsed,
  renderAgentResult,
} from './agent-utils.js';

describe('getPhaseText', () => {
  it('agent 为 null/undefined 返回空串', () => {
    expect(getPhaseText(null)).toBe('');
    expect(getPhaseText(undefined)).toBe('');
  });

  it('无 phase 返回空串', () => {
    expect(getPhaseText({})).toBe('');
  });

  it('thinking 阶段', () => {
    expect(getPhaseText({ phase: 'thinking' })).toBe('...思考中');
  });

  it('answering 阶段', () => {
    expect(getPhaseText({ phase: 'answering' })).toContain('生成中');
  });

  it('parsing 阶段含 phaseInfo', () => {
    const text = getPhaseText({ phase: 'parsing', phaseInfo: 'read_file' });
    expect(text).toContain('解析中');
    expect(text).toContain('read_file');
  });

  it('parsing 阶段无 phaseInfo', () => {
    const text = getPhaseText({ phase: 'parsing' });
    expect(text).toBe('🔍 解析中');
  });

  it('batch 阶段', () => {
    const text = getPhaseText({ phase: 'batch', phaseInfo: '3 tools' });
    expect(text).toContain('批量执行');
    expect(text).toContain('3 tools');
  });

  it('error 阶段', () => {
    const text = getPhaseText({ phase: 'error', phaseInfo: '超时' });
    expect(text).toContain('超时');
  });

  it('未知阶段原样返回', () => {
    const text = getPhaseText({ phase: 'custom' });
    expect(text).toContain('custom');
  });
});

describe('getStatusIcon', () => {
  it('agent 为 null 返回 null', () => {
    expect(getStatusIcon(null)).toBeNull();
  });

  it('done 返回 ✔ 图标', () => {
    const icon = getStatusIcon({ status: 'done' });
    expect(icon).toBeTruthy();
  });

  it('completed 返回 ✔ 图标', () => {
    const icon = getStatusIcon({ status: 'completed' });
    expect(icon).toBeTruthy();
  });

  it('fail 返回 ✗ 图标', () => {
    const icon = getStatusIcon({ status: 'fail' });
    expect(icon).toBeTruthy();
  });

  it('error 返回 ✗ 图标', () => {
    const icon = getStatusIcon({ status: 'error' });
    expect(icon).toBeTruthy();
  });

  it('running 返回运行中动画点', () => {
    const icon = getStatusIcon({ status: 'running' });
    expect(icon).toBeTruthy();
  });
});

describe('getToolStatusIcon', () => {
  it('trec 为 null 返回 null', () => {
    expect(getToolStatusIcon(null)).toBeNull();
  });

  it('done + success 返回 ✔', () => {
    const icon = getToolStatusIcon({ status: 'done', success: true });
    expect(icon).toBeTruthy();
  });

  it('done + not success 返回 ✗', () => {
    const icon = getToolStatusIcon({ status: 'done', success: false });
    expect(icon).toBeTruthy();
  });

  it('started 返回 ●', () => {
    const icon = getToolStatusIcon({ status: 'started' });
    expect(icon).toBeTruthy();
  });

  it('其他状态（如 parsing）返回 ⏳', () => {
    const icon = getToolStatusIcon({ status: 'parsing' });
    expect(icon).toBeTruthy();
  });
});

describe('getTokenParts', () => {
  it('agent 为 null 返回空数组', () => {
    expect(getTokenParts(null)).toEqual([]);
  });

  it('无 usage 返回空数组', () => {
    expect(getTokenParts({})).toEqual([]);
  });

  it('有 output 无 input', () => {
    const parts = getTokenParts({ usage: { output: 1200 } });
    expect(parts.length).toBe(1);
    expect(parts[0]).toContain('out');
  });

  it('有 input 无 output', () => {
    const parts = getTokenParts({ usage: { input: 500 } });
    expect(parts.length).toBe(1);
    expect(parts[0]).toContain('in');
  });

  it('有 output 和 input', () => {
    const parts = getTokenParts({ usage: { output: 1500, input: 800 } });
    expect(parts.length).toBe(2);
  });
});

describe('getAgentElapsed', () => {
  it('agent 为 null 返回空串', () => {
    expect(getAgentElapsed(null, Date.now())).toBe('');
  });

  it('已完成状态取工具记录最大耗时', () => {
    const startTime = Date.now() - 3200;
    const agent = {
      status: 'done',
      _completedAt: Date.now(),
      startTime: startTime,
      tools: [
        { toolKey: 't1', status: 'done', elapsed: 1.5, startTime: startTime },
        { toolKey: 't2', status: 'done', elapsed: 3.2, startTime: startTime + 1000 },
      ],
    };
    const elapsed = getAgentElapsed(agent, Date.now());
    expect(parseFloat(elapsed)).toBeCloseTo(3.2, 0);
  });

  it('已完成状态无可耗时返回空串', () => {
    const agent = { status: 'done', tools: {} };
    expect(getAgentElapsed(agent, Date.now())).toBe('');
  });

  it('运行中使用 agent.startTime', () => {
    const now = Date.now();
    const agent = { status: 'running', startTime: now - 5000 };
    const elapsed = getAgentElapsed(agent, now);
    expect(elapsed).toBe('5.0s');
  });

  it('运行中无 startTime 使用工具最早 startTime', () => {
    const now = Date.now();
    const agent = {
      status: 'running',
      tools: [
        { toolKey: 't1', startTime: now - 3000 },
        { toolKey: 't2', startTime: now - 1000 },
      ],
    };
    const elapsed = getAgentElapsed(agent, now);
    expect(elapsed).toBe('3.0s');
  });

  it('无任何时间信息返回空串', () => {
    const agent = { status: 'running', tools: {} };
    expect(getAgentElapsed(agent, Date.now())).toBe('');
  });
});

describe('renderAgentResult', () => {
  beforeEach(() => {
    // 清理全局函数
    delete window.preprocessMathBeforeRender;
    delete window.renderMarkdown;
    delete window.escapeHtml;
  });

  it('result 为空返回空串', () => {
    expect(renderAgentResult('')).toBe('');
    expect(renderAgentResult(null)).toBe('');
    expect(renderAgentResult(undefined)).toBe('');
  });

  it('无 renderMarkdown 时做 HTML 转义', () => {
    window.escapeHtml = (s) => String(s).replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const result = renderAgentResult('hello <world>');
    expect(result).toContain('&lt;world&gt;');
  });

  it('有 renderMarkdown 时调用', () => {
    window.renderMarkdown = vi.fn((t) => `<p>${t}</p>`);
    const result = renderAgentResult('hello');
    expect(window.renderMarkdown).toHaveBeenCalledWith('hello');
    expect(result).toBe('<p>hello</p>');
  });

  it('有 preprocessMathBeforeRender 时先预处理', () => {
    window.preprocessMathBeforeRender = vi.fn((t) => t.toUpperCase());
    window.renderMarkdown = vi.fn((t) => `<p>${t}</p>`);
    const result = renderAgentResult('hello');
    expect(window.preprocessMathBeforeRender).toHaveBeenCalledWith('hello');
    expect(window.renderMarkdown).toHaveBeenCalledWith('HELLO');
  });
});
