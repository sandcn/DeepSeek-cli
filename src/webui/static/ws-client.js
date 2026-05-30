/* ═══════════════════════════════════════════════════════════════
   WebSocket 连接管理器
   独立模块，通过回调机制与外部通信，不依赖其他模块
   ═══════════════════════════════════════════════════════════════ */
class WSClient {
  constructor(onStatusChange) {
    this.ws = null;
    this.reconnectTimer = null;
    this.reconnectDelay = 1000;
    this.maxReconnectDelay = 10000;
    this.handlers = {};
    this.intentionalClose = false;
    this._onStatusChange = onStatusChange;
    this._onReconnect = null;   // 重连时回调（用于清理计时器等）
    this._keepaliveTimer = null;
    this._keepaliveInterval = 25000;  // 25s 客户端 ping（后台时仍有效）
    this._onReconnectStateSync = null; // 重连后状态同步回调
  }

  connect() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${protocol}//${location.host}/ws`;
    this.intentionalClose = false;

    try {
      this.ws = new WebSocket(url);
    } catch (e) {
      this._scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      // 重连时触发外部清理回调（#1 修复：清理所有残留计时器）
      if (this._onReconnect) this._onReconnect();
      if (this._onStatusChange) this._onStatusChange(true);
      this.reconnectDelay = 1000;
    };

    this.ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        this._dispatch(data);
      } catch (e) {
        const raw = typeof event.data === 'string' ? event.data : String(event.data);
        console.error('❌ parse error:', e.message || e, '| data:', raw.slice(0, 300));
      }
    };

    this.ws.onclose = () => {
      if (this._onStatusChange) this._onStatusChange(false);
      if (this._onConnectionChange) this._onConnectionChange(false, this.reconnectDelay);
      if (!this.intentionalClose) {
        this._scheduleReconnect();
      }
    };

    this.ws.onerror = () => {
      // onclose will fire after this
    };

    // ── 客户端 keepalive ping（后台时仍有效，防止 NAT/代理断连） ──
    this._startKeepalive();
  }

  _scheduleReconnect() {
    if (this.reconnectTimer) return;
    const delay = this.reconnectDelay;
    this.reconnectDelay = Math.min(delay * 1.5, this.maxReconnectDelay);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
      // ★ 重连后触发状态同步回调（请求完整会话状态，弥补后台期间错过的消息）
      if (this._onReconnectStateSync) {
        setTimeout(() => this._onReconnectStateSync(), 500);
      }
    }, delay);
  }

  close() {
    this.intentionalClose = true;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this._stopKeepalive();
    // ★ 清空 handler 注册表，防止重连后 handler 累积
    this.handlers = {};
  }

  send(data) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
      return true;
    }
    return false;
  }

  on(type, handler) {
    if (!this.handlers[type]) this.handlers[type] = [];
    // ★ 去重：避免重复注册导致 handler 数组无限增长
    if (!this.handlers[type].includes(handler)) {
      this.handlers[type].push(handler);
    }
  }

  _dispatch(data) {
    const type = data.type;
    const handlers = this.handlers[type];
    if (handlers) {
      for (const h of handlers) h(data);
    }
  }

  /** 启动客户端 keepalive ping（每 25s 发送一次轻量 ping，后台时仍有效） */
  _startKeepalive() {
    this._stopKeepalive();
    this._keepaliveTimer = setInterval(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        // 发送轻量 ping 消息，NAT/代理保活，不触发模型处理
        try {
          this.ws.send(JSON.stringify({ type: 'ping' }));
        } catch (_) {
          // 发送失败时不清除 timer，留给 onclose 处理
        }
      }
    }, this._keepaliveInterval);
  }

  /** 停止 keepalive */
  _stopKeepalive() {
    if (this._keepaliveTimer) {
      clearInterval(this._keepaliveTimer);
      this._keepaliveTimer = null;
    }
  }

}
