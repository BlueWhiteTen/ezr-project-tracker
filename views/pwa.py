from django.http import HttpResponse
from django.shortcuts import render

SW_JS = """
// EZR Project Tracker service worker: lets the site install as an app.
// Pages are always fetched fresh from the server (nothing private is stored);
// only static files (logo, icons) are cached, plus a small offline page.
const V = 'ezr-v1';
self.addEventListener('install', e => {
  e.waitUntil(caches.open(V).then(c => c.add('/offline/')).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== V).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const r = e.request;
  if (r.method !== 'GET') return;
  const url = new URL(r.url);
  if (url.origin !== location.origin) return;
  if (r.mode === 'navigate') {
    e.respondWith(fetch(r).catch(() => caches.match('/offline/')));
    return;
  }
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(caches.open(V).then(c => c.match(r).then(hit => {
      const net = fetch(r).then(res => { if (res.ok) c.put(r, res.clone()); return res; }).catch(() => hit);
      return hit || net;
    })));
  }
});
"""


def service_worker(request):
    resp = HttpResponse(SW_JS, content_type='application/javascript')
    resp['Service-Worker-Allowed'] = '/'
    resp['Cache-Control'] = 'no-cache'
    return resp


def offline(request):
    return render(request, 'projects/offline.html')
