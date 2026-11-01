/* Service worker CryptisDchat: Web Push, когда вкладка приложения закрыта.
 *
 * Ключей шифрования здесь нет (они живут только в разблокированной PIN вкладке), поэтому
 * расшифровать сообщение нельзя: сервер присылает имя отправителя и «New message».
 */
'use strict';

let appIconPromise;
function appIcon() {
  if (!appIconPromise) {
    appIconPromise = fetch('/static/manifest.json', { cache: 'no-cache' }).then((r) => r.json())
      .then((manifest) => {
        const icon = (manifest.icons || []).find((item) => (item.purpose || 'any').split(/\s+/).includes('any')) || manifest.icons[0];
        return icon ? new URL(icon.src, self.location.origin).href : new URL('/static/images/icon-round.png', self.location.origin).href;
      })
      .catch(() => new URL('/static/images/icon-round.png', self.location.origin).href);
  }
  return appIconPromise;
}

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { /* не JSON — покажем общее уведомление */ }
  event.waitUntil(appIcon().then((icon) => self.registration.showNotification(data.title || 'CryptisDchat', {
    body: data.body || 'New message', icon, badge: icon,
    tag: data.thread_id ? 'cx-' + data.thread_id : 'cx',  // уведомления одного чата заменяют друг друга
    renotify: true, data: { threadId: data.thread_id || null },
  })));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const threadId = event.notification.data && event.notification.data.threadId;
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    const win = windows.find((w) => new URL(w.url).pathname.startsWith('/app'));
    if (win) {
      await win.focus();
      if (threadId) win.postMessage({ type: 'open-thread', threadId });
      return;
    }
    await self.clients.openWindow(threadId ? '/app/chat/' + encodeURIComponent(threadId) : '/app');
  })());
});
