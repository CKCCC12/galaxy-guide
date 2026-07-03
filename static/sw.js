// sw.js — Service Worker（PWA 離線殼）
//
// 策略刻意保守，避開 PWA 兩個經典陷阱：
//   1. 「部署後還顯示舊版」：導覽（HTML）用 network-first，線上一定拿到最新頁面，
//      只有離線才退回快取的殼；快取名帶版本，啟用時清掉舊版快取。
//   2. 「快取到查詢結果」：POST 一律不碰；/recommend、/ai-summary 等動態端點
//      直接放行走網路，永遠即時。
//
// 只快取「靜態殼」：首頁 HTML、manifest、圖示。外部資源（Leaflet、地圖磚、
// Chart.js CDN）交給瀏覽器自身的 HTTP 快取，SW 不介入。

const CACHE = 'galaxy-guide-v1';
const SHELL = [
  '/',
  '/static/manifest.json',
  '/static/icon-192.png',
  '/static/icon-512.png',
];

self.addEventListener('install', function (e) {
  // 預抓靜態殼；skipWaiting 讓新版 SW 立即接手
  e.waitUntil(
    caches.open(CACHE)
      .then(function (c) { return c.addAll(SHELL); })
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function (e) {
  // 清掉舊版快取，並立即接管所有頁面
  e.waitUntil(
    caches.keys()
      .then(function (keys) {
        return Promise.all(
          keys.filter(function (k) { return k !== CACHE; })
              .map(function (k) { return caches.delete(k); })
        );
      })
      .then(function () { return self.clients.claim(); })
  );
});

self.addEventListener('fetch', function (e) {
  const req = e.request;
  if (req.method !== 'GET') return;  // POST（查詢、AI）一律走網路

  const url = new URL(req.url);
  if (url.origin !== location.origin) return;  // 外部 CDN／地圖磚交給瀏覽器

  // 動態端點永遠即時，不進快取（含 /recommend/stream 這個 GET 的 SSE）
  if (url.pathname.startsWith('/recommend') ||
      url.pathname.startsWith('/ai-summary')) {
    return;
  }

  // 導覽請求（HTML）：network-first，離線才用快取的殼
  if (req.mode === 'navigate') {
    e.respondWith(
      fetch(req).catch(function () { return caches.match('/'); })
    );
    return;
  }

  // 其餘同源靜態資源：cache-first，沒有才連網
  e.respondWith(
    caches.match(req).then(function (cached) {
      return cached || fetch(req);
    })
  );
});
