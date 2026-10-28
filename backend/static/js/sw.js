/* Service worker CryptisDchat: Web Push, когда вкладка приложения закрыта.
 *
 * Ключей шифрования здесь нет (они живут только в разблокированной PIN вкладке), поэтому
 * расшифровать сообщение нельзя: сервер присылает имя отправителя и «New message».
 */
'use strict';

const ICON = '/static/images/icon-round.png';

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { /* не JSON — покажем общее уведомление */ }
  event.waitUntil(self.registration.showNotification(data.title || 'CryptisDchat', {
    body: data.body || 'New message',
    icon: ICON,
    badge: ICON,
    tag: data.thread_id ? 'cx-' + data.thread_id : 'cx',  // уведомления одного чата заменяют друг друга
    renotify: true,
    data: { threadId: data.thread_id || null },
  }));
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
