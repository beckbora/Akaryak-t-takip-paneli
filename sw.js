self.addEventListener('install', event => { self.skipWaiting(); });
self.addEventListener('activate', event => { event.waitUntil(self.clients.claim()); });

self.addEventListener('push', event => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { body: event.data ? event.data.text() : 'Akaryakıt fiyat durumu güncellendi.' };
  }
  const title = data.title || 'Petrol Piyasası Takip';
  const options = {
    body: data.body || 'Akaryakıt fiyat durumu güncellendi.',
    tag: data.tag || 'fuel-price-update',
    icon: data.icon || '/app-icon.svg',
    badge: data.badge || '/app-icon.svg',
    renotify: true,
    data: { url: data.url || '/fiyatlar' },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || '/fiyatlar';
  event.waitUntil((async () => {
    const list = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const client of list) {
      if ('focus' in client) {
        try { await client.navigate(target); } catch (e) {}
        return client.focus();
      }
    }
    if (self.clients.openWindow) return self.clients.openWindow(target);
  })());
});