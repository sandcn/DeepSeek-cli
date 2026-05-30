/**
 * store.test.js — 全局状态管理单元测试
 *
 * 测试覆盖：
 * - getState / setState 基本读写
 * - 订阅/取消订阅机制
 * - 工具集合 CRUD
 * - Agent 集合 CRUD
 * - 消息集合 CRUD（含批量添加）
 * - Dispatch 状态管理
 * - resetAll 重置
 * - 流式暂停/恢复通知
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  getState,
  setState,
  subscribe,
  addTool,
  updateTool,
  removeTool,
  clearTools,
  getTools,
  addAgent,
  updateAgent,
  removeAgent,
  clearAgents,
  getAgents,
  addMessage,
  addMessagesBatch,
  updateMessage,
  removeMessage,
  clearMessages,
  getMessages,
  setDispatchState,
  resetDispatchState,
  getDispatchState,
  resetAll,
  setNotificationsPaused,
} from './store.js';

describe('基础状态操作', () => {
  beforeEach(() => {
    resetAll();
  });

  it('getState 返回初始状态', () => {
    const state = getState();
    expect(state.tools).toEqual({});
    expect(state.agents).toEqual({});
    expect(state.messages).toEqual({});
    expect(state.dispatch).toEqual({
      labelOrder: [],
      agentCounter: 0,
      batchDone: 0,
      pendingAgents: [],
    });
  });

  it('setState 合并更新状态', () => {
    setState({ foo: 'bar' });
    const state = getState();
    expect(state.foo).toBe('bar');
  });

  it('订阅机制 — setState 时通知 listener', () => {
    const listener = vi.fn();
    subscribe(listener);
    setState({ test: true });
    expect(listener).toHaveBeenCalledTimes(1);
    const snapshot = listener.mock.calls[0][0];
    expect(snapshot.test).toBe(true);
  });

  it('取消订阅后不再通知', () => {
    const listener = vi.fn();
    const unsub = subscribe(listener);
    unsub();
    setState({ test: true });
    expect(listener).not.toHaveBeenCalled();
  });

  it('多个 listener 分别通知', () => {
    const l1 = vi.fn();
    const l2 = vi.fn();
    subscribe(l1);
    subscribe(l2);
    setState({ x: 1 });
    expect(l1).toHaveBeenCalledTimes(1);
    expect(l2).toHaveBeenCalledTimes(1);
  });

  it('listener 异常不传播', () => {
    const l1 = vi.fn(() => { throw new Error('boom'); });
    const l2 = vi.fn();
    subscribe(l1);
    subscribe(l2);
    expect(() => setState({ x: 1 })).not.toThrow();
    expect(l2).toHaveBeenCalledTimes(1);
  });
});

describe('工具集合 CRUD', () => {
  beforeEach(() => {
    resetAll();
  });

  it('addTool 添加新工具', () => {
    addTool('tool-0', { toolName: 'read_file', status: 'parsing' });
    const tools = getTools();
    expect(tools['tool-0']).toBeDefined();
    expect(tools['tool-0'].toolName).toBe('read_file');
    expect(tools['tool-0'].label).toBe('tool-0');
  });

  it('updateTool 更新已有工具', () => {
    addTool('tool-0', { toolName: 'read_file', status: 'parsing' });
    updateTool('tool-0', { status: 'running' });
    expect(getTools()['tool-0'].status).toBe('running');
  });

  it('updateTool 不存在的工具不报错', () => {
    expect(() => updateTool('nonexistent', { status: 'done' })).not.toThrow();
  });

  it('removeTool 删除工具', () => {
    addTool('tool-0', { toolName: 'search' });
    removeTool('tool-0');
    expect(getTools()['tool-0']).toBeUndefined();
  });

  it('removeTool 不存在的工具不报错', () => {
    expect(() => removeTool('nonexistent')).not.toThrow();
  });

  it('clearTools 清空所有工具', () => {
    addTool('t1', { toolName: 'a' });
    addTool('t2', { toolName: 'b' });
    clearTools();
    expect(getTools()).toEqual({});
  });

  it('getTools 返回浅拷贝', () => {
    addTool('t1', { toolName: 'a' });
    const tools = getTools();
    expect(tools).not.toBe(getState().tools); // 不是同一引用
  });
});

describe('Agent 集合 CRUD', () => {
  beforeEach(() => {
    resetAll();
  });

  it('addAgent 添加新 Agent', () => {
    addAgent('agent-1', { description: '分析代码', status: 'running' });
    const agents = getAgents();
    expect(agents['agent-1']).toBeDefined();
    expect(agents['agent-1'].description).toBe('分析代码');
    expect(agents['agent-1'].label).toBe('agent-1');
  });

  it('updateAgent 更新已有 Agent', () => {
    addAgent('agent-1', { status: 'running' });
    updateAgent('agent-1', { status: 'done' });
    expect(getAgents()['agent-1'].status).toBe('done');
  });

  it('removeAgent 删除 Agent', () => {
    addAgent('agent-1', { status: 'running' });
    removeAgent('agent-1');
    expect(getAgents()['agent-1']).toBeUndefined();
  });

  it('clearAgents 清空所有 Agent', () => {
    addAgent('a1', {});
    addAgent('a2', {});
    clearAgents();
    expect(getAgents()).toEqual({});
  });
});

describe('消息集合 CRUD', () => {
  beforeEach(() => {
    resetAll();
  });

  it('addMessage 添加新消息', () => {
    addMessage('user-0', { content: 'hello', type: 'user' });
    const msgs = getMessages();
    expect(msgs['user-0']).toBeDefined();
    expect(msgs['user-0'].content).toBe('hello');
    expect(msgs['user-0'].key).toBe('user-0');
  });

  it('updateMessage 更新消息', () => {
    addMessage('user-0', { content: 'hi' });
    updateMessage('user-0', { content: 'hello' });
    expect(getMessages()['user-0'].content).toBe('hello');
  });

  it('updateMessage 不存在的消息不报错', () => {
    expect(() => updateMessage('nonexistent', { content: 'x' })).not.toThrow();
  });

  it('removeMessage 删除消息', () => {
    addMessage('user-0', { content: 'hi' });
    removeMessage('user-0');
    expect(getMessages()['user-0']).toBeUndefined();
  });

  it('clearMessages 清空消息', () => {
    addMessage('user-0', {});
    addMessage('assistant-1', {});
    clearMessages();
    expect(getMessages()).toEqual({});
  });

  it('addMessagesBatch 批量添加', () => {
    addMessagesBatch({
      'user-0': { content: 'u1' },
      'assistant-1': { content: 'a1' },
    });
    const msgs = getMessages();
    expect(msgs['user-0'].content).toBe('u1');
    expect(msgs['assistant-1'].content).toBe('a1');
  });

  it('addMessagesBatch 不覆盖已有消息', () => {
    addMessage('user-0', { content: 'original' });
    addMessagesBatch({ 'user-0': { content: 'new' } });
    expect(getMessages()['user-0'].content).toBe('original');
  });

  it('addMessagesBatch null/非对象不报错', () => {
    expect(() => addMessagesBatch(null)).not.toThrow();
    expect(() => addMessagesBatch(123)).not.toThrow();
  });
});

describe('Dispatch 状态管理', () => {
  beforeEach(() => {
    resetAll();
  });

  it('初始状态正确', () => {
    const ds = getDispatchState();
    expect(ds.labelOrder).toEqual([]);
    expect(ds.agentCounter).toBe(0);
  });

  it('setDispatchState 合并更新', () => {
    setDispatchState({ agentCounter: 3 });
    expect(getDispatchState().agentCounter).toBe(3);
    expect(getDispatchState().batchDone).toBe(0); // 其他字段不变
  });

  it('resetDispatchState 恢复初始', () => {
    setDispatchState({ agentCounter: 5, batchDone: 2 });
    resetDispatchState();
    const ds = getDispatchState();
    expect(ds.agentCounter).toBe(0);
    expect(ds.batchDone).toBe(0);
  });
});

describe('resetAll', () => {
  it('重置所有状态为空', () => {
    addTool('t1', {});
    addAgent('a1', {});
    addMessage('user-0', {});
    setDispatchState({ agentCounter: 3 });

    resetAll();

    const state = getState();
    expect(state.tools).toEqual({});
    expect(state.agents).toEqual({});
    expect(state.messages).toEqual({});
    expect(state.dispatch.agentCounter).toBe(0);
  });
});

describe('流式暂停/恢复', () => {
  beforeEach(() => {
    resetAll();
  });

  it('暂停时不通知 listener', () => {
    const listener = vi.fn();
    subscribe(listener);
    setNotificationsPaused(true);
    setState({ test: 1 });
    expect(listener).not.toHaveBeenCalled();
  });

  it('恢复时推送最后一次快照', () => {
    const listener = vi.fn();
    subscribe(listener);
    setNotificationsPaused(true);
    setState({ test: 'first' });
    setState({ test: 'last' }); // 只缓存最后一次
    expect(listener).not.toHaveBeenCalled();

    setNotificationsPaused(false); // 恢复 → 推送最后一次
    expect(listener).toHaveBeenCalledTimes(1);
    const snapshot = listener.mock.calls[0][0];
    expect(snapshot.test).toBe('last');
  });

  it('resumeAll 时没有缓存不推送', () => {
    const listener = vi.fn();
    subscribe(listener);
    setNotificationsPaused(true);
    setNotificationsPaused(false);
    // 暂停期间没有 setState，所以不推送
    // 但暂停前可能已有通知... resetAll 已经清理
    expect(listener).not.toHaveBeenCalled();
  });
});
