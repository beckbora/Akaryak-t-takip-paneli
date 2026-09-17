(() => {
  const PREF = 'fuel_notifications_enabled';
  const STATE = 'fuel_notification_state_v1';
  const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const isStandalone = () => window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;

  function ensureManifest() {
    if (!document.querySelector('link[rel="manifest"]')) {
      const link = document.createElement('link');
      link.rel = 'manifest';
      link.href = '/manifest.webmanifest';
      document.head.appendChild(link);
    }
    if (!document.querySelector('meta[name="apple-mobile-web-app-capable"]')) {
      const m = document.createElement('meta');
      m.name = 'apple-mobile-web-app-capable';
      m.content = 'yes';
      document.head.appendChild(m);
    }
    if (!document.querySelector('meta[name="apple-mobile-web-app-status-bar-style"]')) {
      const m = document.createElement('meta');
      m.name = 'apple-mobile-web-app-status-bar-style';
      m.content = 'black-translucent';
      document.head.appendChild(m);
    }
  }

  async function registerSW() {
    if (!('serviceWorker' in navigator)) return null;
    try {
      await navigator.serviceWorker.register('/sw.js', { scope: '/' });
      return await navigator.serviceWorker.ready;
    } catch (e) {
      return null;
    }
  }

  function enabled() { return localStorage.getItem(PREF) === '1'; }
  function readState() { try { return JSON.parse(localStorage.getItem(STATE) || '{}'); } catch (e) { return {}; } }
  function sig(item) { return [item.status || '', item.amount ?? '', item.line || ''].join('|'); }
  function baseline(items) {
    const state = {};
    for (const item of items || []) if (item.fuel_key) state[item.fuel_key] = sig(item);
    localStorage.setItem(STATE, JSON.stringify(state));
  }

  function installGuide() {
    let modal = document.getElementById('iosInstallGuide');
    if (!modal) {
      const style = document.createElement('style');
      style.textContent = `
        .iosGuideOverlay{position:fixed;inset:0;background:rgba(2,8,18,.78);z-index:9999;display:flex;align-items:flex-end;justify-content:center;padding:14px}
        .iosGuideCard{width:min(560px,100%);background:#0d1a2b;border:1px solid #355476;border-radius:22px;padding:20px;color:#eef5ff;box-shadow:0 20px 70px rgba(0,0,0,.45)}
        .iosGuideCard h3{margin:0 0 10px;font-size:20px}.iosGuideCard p{color:#b8c7da;line-height:1.55;margin:7px 0}
        .iosSteps{display:grid;gap:8px;margin:14px 0}.iosStep{background:#091624;border:1px solid #263b5a;border-radius:12px;padding:11px 12px;font-size:14px}
        .iosGuideClose{width:100%;margin-top:6px;border:1px solid #238579;background:#0e3937;color:#c9fff8;padding:12px;border-radius:12px;font-weight:900}
      `;
      document.head.appendChild(style);
      modal = document.createElement('div');
      modal.id = 'iosInstallGuide';
      modal.className = 'iosGuideOverlay';
      modal.innerHTML = `<div class="iosGuideCard"><h3>📲 iPhone'da bildirimleri aç</h3><p>iOS, normal Safari sekmesinden web bildirimi vermiyor. Önce Fiyat Radar'ı Ana Ekrana eklemen gerekiyor.</p><div class="iosSteps"><div class="iosStep"><b>1.</b> Safari alt menüsündeki <b>Paylaş ⬆️</b> simgesine dokun.</div><div class="iosStep"><b>2.</b> <b>Ana Ekrana Ekle</b> seçeneğini seç ve <b>Ekle</b> de.</div><div class="iosStep"><b>3.</b> Ana ekrandaki <b>Fiyat Radar</b> simgesinden uygulamayı aç.</div><div class="iosStep"><b>4.</b> Buradaki <b>🔔 Bildirimleri Aç</b> düğmesine dokun ve iOS bildirim iznini onayla.</div></div><p style="font-size:12px">Telefon numarası, e-posta veya kullanıcı hesabı kaydedilmez. Tercih bu cihazda tutulur.</p><button class="iosGuideClose" type="button">Tamam</button></div>`;
      document.body.appendChild(modal);
      modal.querySelector('.iosGuideClose').onclick = () => modal.remove();
      modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    }
  }

  async function showLocal(body, tag) {
    try {
      const reg = await registerSW();
      if (reg && reg.showNotification) {
        await reg.showNotification('Petrol Piyasası Takip', { body, tag, data: { url: '/fiyatlar' } });
        return;
      }
    } catch (e) {}
    try {
      if ('Notification' in window && Notification.permission === 'granted') new Notification('Petrol Piyasası Takip', { body, tag });
    } catch (e) {}
  }

  function updateButton() {
    const b = document.getElementById('notifyBtn');
    if (!b) return;
    b.disabled = false;
    if (isIOS && !isStandalone()) {
      b.textContent = '📲 Ana Ekrana Ekle';
      b.className = 'btn notifyBtn';
      b.title = 'iPhone bildirimleri için Fiyat Radar’ı Ana Ekrana ekleyin';
      return;
    }
    if (!('Notification' in window) || !('serviceWorker' in navigator)) {
      b.textContent = '🔕 Bildirim Desteklenmiyor';
      b.className = 'btn notifyBtn blocked';
      b.disabled = true;
      return;
    }
    if (Notification.permission === 'denied') {
      b.textContent = '🚫 Bildirim İzni Kapalı';
      b.className = 'btn notifyBtn blocked';
      b.title = 'iPhone Ayarlar > Bildirimler > Fiyat Radar bölümünden izin verin';
      return;
    }
    const on = enabled() && Notification.permission === 'granted';
    b.textContent = on ? '🔔 Bildirimler Açık' : '🔕 Bildirimleri Aç';
    b.className = 'btn notifyBtn ' + (on ? 'enabled' : '');
  }

  async function toggle() {
    if (isIOS && !isStandalone()) {
      installGuide();
      return;
    }
    if (!('Notification' in window) || !('serviceWorker' in navigator)) return;
    if (enabled() && Notification.permission === 'granted') {
      localStorage.setItem(PREF, '0');
      updateButton();
      return;
    }
    if (Notification.permission === 'denied') {
      updateButton();
      return;
    }
    await registerSW();
    let permission = Notification.permission;
    if (permission === 'default') permission = await Notification.requestPermission();
    if (permission === 'granted') {
      localStorage.setItem(PREF, '1');
      baseline(window.__latestFuelExpectationItems || []);
      await showLocal('Akaryakıt fiyat bildirimleri açıldı.', 'fuel-notifications-enabled');
    } else {
      localStorage.setItem(PREF, '0');
    }
    updateButton();
  }

  async function notifyChanges(items) {
    window.__latestFuelExpectationItems = items || [];
    if (!enabled() || !('Notification' in window) || Notification.permission !== 'granted') return;
    const prior = readState();
    const next = { ...prior };
    for (const item of items || []) {
      if (!item.fuel_key) continue;
      const current = sig(item);
      const old = prior[item.fuel_key];
      if (old && old !== current && item.status !== 'none') {
        await showLocal(item.line || 'Akaryakıt fiyat durumu güncellendi.', 'fuel-' + item.fuel_key);
      }
      next[item.fuel_key] = current;
    }
    localStorage.setItem(STATE, JSON.stringify(next));
  }

  function takeOver() {
    ensureManifest();
    registerSW();

    const old = document.getElementById('notifyBtn');
    if (old) {
      const fresh = old.cloneNode(true);
      old.replaceWith(fresh);
      fresh.disabled = false;
      fresh.addEventListener('click', toggle);
    }

    window.updateNotifyButton = updateButton;
    window.toggleFuelNotifications = toggle;
    window.maybeNotifyFuelChanges = notifyChanges;
    updateButton();
  }

  if (document.readyState === 'loading') {
    window.addEventListener('DOMContentLoaded', () => setTimeout(takeOver, 0));
  } else {
    setTimeout(takeOver, 0);
  }
})();
