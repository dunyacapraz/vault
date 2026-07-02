// Vault — Service Worker
// PWA olarak ana ekrana eklenebilmesi ve push bildirimi alabilmesi için gerekli.

self.addEventListener('install', (event) => {
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(self.clients.claim());
});

// Sunucudan gelen push bildirimini göster
self.addEventListener('push', (event) => {
    let data = {};
    try {
        data = event.data ? event.data.json() : {};
    } catch (e) {
        data = { title: 'Vault', body: event.data ? event.data.text() : 'Yeni bir şey var.' };
    }

    const title = data.title || 'Vault';
    const options = {
        body: data.body || '',
        icon: '/static/icons/icon-192.png',
        badge: '/static/icons/icon-192.png',
        data: { url: data.url || '/' },
        tag: data.tag || undefined,
        renotify: !!data.tag,
        vibrate: [80, 40, 80],
    };
    if (data.image) {
        options.image = data.image;
    }

    event.waitUntil(self.registration.showNotification(title, options));
});

// Bildirime tıklanınca ilgili sayfayı aç (zaten açıksa ona odaklan)
self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    const targetUrl = (event.notification.data && event.notification.data.url) || '/';

    event.waitUntil(
        self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
            for (const client of clientList) {
                if (client.url.includes(targetUrl) && 'focus' in client) {
                    return client.focus();
                }
            }
            if (self.clients.openWindow) {
                return self.clients.openWindow(targetUrl);
            }
        })
    );
});
