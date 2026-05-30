/* ═══════════════════════════════════════════════════════════════
   service-worker.js — PWA 离线缓存策略
   仅缓存关键静态资源（JS/CSS/lib），不缓存 API 响应。
   策略：
   - 核心库（preact/marked/katex/highlight/DOMPurify）：Cache-First
   - 应用 JS/CSS：Cache-First
   - index.html：Network-First（确保始终最新）
   - 其他未知资源：Network-First
   ═══════════════════════════════════════════════════════════════ */

const CACHE_NAME = 'ai-chat-v1';

/* ── 需要 Cache-First 的核心资源（离线可用） ── */
const CORE_ASSETS = [
  '/style.css',
  '/app.js',
  '/ws-client.js',
  '/bubble.js',
  '/utils/core.js',
  '/utils/postprocess.js',
  '/utils/timer.js',
  '/utils/scroll-observer.js',
  '/gestures.js',
  '/utils.js',
  '/markdown-renderer.js',
  '/tool-renderer.js',
  '/tool-renderers/web-diff.js',
  '/webui-console.js',
  '/sandbox.js',
  '/handlers/streaming.js',
  '/handlers/tools.js',
  '/handlers/agents.js',
  '/handlers/gen-status.js',
  '/handlers/editmsg.js',
  '/handlers/sessions.js',
  '/handlers/rebuild.js',
  '/handlers.js',
  '/handlers/register.js',
  '/md-engine.js',
  '/lib/preact.module.js',
  '/lib/htm.module.js',
  '/lib/hooks.module.js',
  '/lib/preact-compat.module.js',
  '/lib/preact-jsx-runtime.module.js',
  '/lib/jsx-runtime.module.js',
  '/lib/highlight.min.js',
  '/lib/highlight-atom-one-dark.min.css',
  '/lib/katex.min.css',
  '/lib/katex.min.js',
  '/lib/purify.min.js',
  '/lib/md-engine.bundle.js',
  '/components/AgentCard.js',
  '/components/Bubble.js',
  '/components/MessageList.js',
  '/components/SelectModal.js',
  '/components/ToolRow.js',
  '/components/agent-utils.js',
  '/state/store.js',
  '/state/hooks.js',
  '/manifest.json',
];

/* ── 安装：预缓存核心资源 ── */
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      // 预缓存核心资源，失败不阻塞安装
      return Promise.allSettled(
        CORE_ASSETS.map((url) =>
          cache.add(url).catch((err) => {
            console.warn('[SW] 预缓存失败（跳过）:', url, err.message);
          })
        )
      );
    }).then(() => {
      // 跳过 waiting，立即激活
      self.skipWaiting();
    })
  );
});

/* ── 激活：清理旧缓存 ── */
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
      );
    }).then(() => {
      // 接管所有客户端页面
      self.clients.claim();
    })
  );
});

/* ── 请求拦截：区分策略 ── */
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // 仅拦截同源请求
  if (url.origin !== self.location.origin) return;

  // WebSocket 请求不拦截
  if (url.protocol === 'ws:' || url.protocol === 'wss:') return;

  const pathname = url.pathname;

  // ── 核心资源：Cache-First ──
  if (CORE_ASSETS.includes(pathname) || pathname.startsWith('/lib/')) {
    event.respondWith(
      caches.match(event.request).then((cached) => {
        if (cached) return cached;
        return fetch(event.request).then((response) => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          }
          return response;
        });
      }).catch(() => {
        // 网络 + 缓存都失败时返回离线提示
        return new Response('离线: 资源不可用', { status: 200, headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
      })
    );
    return;
  }

  // ── index.html（根路径）：Network-First ──
  if (pathname === '/' || pathname === '/index.html') {
    event.respondWith(
      fetch(event.request).then((response) => {
        if (response.ok) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        }
        return response;
      }).catch(() => {
        return caches.match(event.request).then((cached) => {
          return cached || new Response('离线: 无法加载页面', { status: 200, headers: { 'Content-Type': 'text/html; charset=utf-8' } });
        });
      })
    );
    return;
  }

  // ── 其他资源（CSS/JS/图片等）：Cache-First 兜底 ──
  if (pathname.endsWith('.css') || pathname.endsWith('.js') || pathname.endsWith('.mjs') ||
      pathname.endsWith('.json') || pathname.endsWith('.png') || pathname.endsWith('.svg') ||
      pathname.endsWith('.woff2') || pathname.endsWith('.woff') || pathname.endsWith('.ttf')) {
    event.respondWith(
      caches.match(event.request).then((cached) => {
        return cached || fetch(event.request).then((response) => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          }
          return response;
        });
      })
    );
    return;
  }

  // ── 默认：Network-First ──
  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request))
  );
});
