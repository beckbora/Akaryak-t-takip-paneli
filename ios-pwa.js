(() => {
  const PREF = 'fuel_notifications_enabled';
  const STATE = 'fuel_notification_state_v1';
  const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const isStandalone = () => window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  let configPromise = null;

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

  function ensureMobileLayout() {
    if (document.getElementById('fuelRadarMobileLayout')) return;
    const style = document.createElement('style');
    style.id = 'fuelRadarMobileLayout';
    style.textContent = `
      html,body{width:100%;max-width:100%;overflow-x:hidden}
      body{position:relative}
      .wrap{width:100%;max-width:1360px;min-width:0}
      .wrap>*,.top>*,.grid>*,.cards>*,.yearCards>*,.livebar>*,.expectationRow>*{min-width:0}
      .nav,.top,.livebar,.expectationBox,.filters,.cards,.yearCards,.grid,.panel,.fuel,.sourcebar,.tableWrap,.chartWrap{max-width:100%;min-width:0}
      .nav a,.btn,.select{max-width:100%}
      .expectationRow a,.expectationRow strong,.expSource,.sub,.muted,.footer{overflow-wrap:anywhere;word-break:normal}
      .expectationRow a,.expectationRow strong{white-space:normal}
      .expSource{white-space:normal}
      .chartWrap{overflow:hidden}
      .chartWrap svg{display:block;width:100%;max-width:100%;height:100%}
      .tableWrap{width:100%;overflow-x:auto;overflow-y:hidden;-webkit-overflow-scrolling:touch;overscroll-behavior-x:contain}
      .table{min-width:650px}
      .source{white-space:normal}
      @media(max-width:700px){
        .wrap{padding:14px 12px}
        .nav{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px;width:100%;margin-bottom:16px}
        .nav a{display:flex;align-items:center;justify-content:center;text-align:center;white-space:normal;padding:9px 7px;font-size:12px;line-height:1.25}
        .top{display:block;width:100%}
        .top h1{font-size:clamp(30px,9vw,40px);line-height:1.05;margin-top:8px}
        .sub{font-size:15px;line-height:1.48}
        .notifyActions{display:grid!important;grid-template-columns:1fr 1fr;gap:8px;width:100%;margin-top:16px}
        .notifyActions .btn{width:100%;min-width:0;padding:11px 8px;font-size:12px;line-height:1.25;white-space:normal}
        .livebar{display:block;padding:12px;margin:14px 0 11px}
        .liveleft{align-items:flex-start}
        #updated{margin-top:10px;padding-left:20px}
        .expectationTitle{padding:8px 11px}
        .expectationRow{display:grid!important;grid-template-columns:auto minmax(0,1fr);gap:7px 9px;align-items:start;padding:11px}
        .expectationRow .expLabel{grid-column:1}
        .expectationRow>a,.expectationRow>strong{grid-column:2;line-height:1.35}
        .expectationRow .expSource{grid-column:1/-1;margin-left:0!important;width:100%;font-size:10px;line-height:1.35}
        .filters{display:grid;grid-template-columns:1fr;width:100%;gap:8px;margin:12px 0}
        .filters .select{width:100%;min-width:0;padding:11px 10px}
        .cards,.yearCards,.grid{grid-template-columns:1fr!important;width:100%;gap:10px}
        .fuel,.yearCard,.panel{width:100%;padding:13px}
        .fuel .price{font-size:31px}
        .yearDelta{font-size:25px}.yearPct{font-size:16px}
        .chartWrap{height:235px!important;width:100%}
        .chartWrap.tall{height:260px!important}
        .legend{gap:8px;font-size:10px;line-height:1.35}
        .sourcebar{gap:6px}.source{font-size:10px;padding:6px 8px}
        .tableWrap{border-radius:10px}.table th,.table td{padding:9px 8px;font-size:12px}
        .change{padding:10px}.footer{font-size:10px}
      }
      @media(max-width:390px){
        .wrap{padding:12px 10px}.nav{grid-template-columns:1fr 1fr}.notifyActions{grid-template-columns:1fr}
        .top h1{font-size:30px}.expectationRow{grid-template-columns:1fr}
        .expectationRow .expLabel,.expectationRow>a,.expectationRow>strong,.expectationRow .expSource{grid-column:1}
        .chartWrap{height:220px!important}.chartWrap.tall{height:245px!important}
      }
    `;
    document.head.appendChild(style);
  }

  async function registerSW() {
    if (!('serviceWorker' in navigator)) return null;
    try {
      const reg = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
      try { await reg.update(); } catch (e) {}
      return await navigator.serviceWorker.ready;
    } catch (e) {
      return null;
    }
  }

  async function getConfig() {
    if (configPromise) return configPromise;
    configPromise = (async () => {
      const url = location.hostname.endsWith('github.io') ? './push-config.json' : '/api/push-config';
      try {
        const r = await fetch(url + (url.includes('?') ? '&' : '?') + 't=' + Date.now(), { cache: 'no-store' });
        if (!r.ok) throw new Error('push config unavailable');
        return await r.json();
      } catch (e) {
        return { configured: false, sender_configured: false };
      }
    })();
    return configPromise;
  }

  function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const rawData = atob(base64);
    return Uint8Array.from([...rawData].map(ch => ch.charCodeAt(0)));
  }

  function uint8ToBase64Url(value) {
    if (!value) return '';
    const bytes = value instanceof Uint8Array ? value : new Uint8Array(value);
    let binary = '';
    for (const b of bytes) binary += String.fromCharCode(b);
    return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
  }

  function subscriptionMatchesKey(sub, publicKey) {
    try {
      const key = sub && sub.options && sub.options.applicationServerKey;
      return Boolean(key) && uint8ToBase64Url(key) === publicKey;
    } catch (e) {
      return false;
    }
  }

  async function subscribeFresh(reg, cfg) {
    return await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(cfg.vapid_public_key),
    });
  }

  async function rpc(name, payload) {
    const cfg = await getConfig();
    if (!cfg.configured || !cfg.supabase_url || !cfg.supabase_publishable_key) {
      throw new Error('Arka plan bildirim servisi henüz yapılandırılmadı.');
    }
    const base = cfg.supabase_url.replace(/\/$/, '');
    const r = await fetch(base + '/rest/v1/rpc/' + name, {
      method: 'POST',
      headers: {
        'apikey': cfg.supabase_publishable_key,
        'Authorization': 'Bearer ' + cfg.supabase_publishable_key,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    });
    if (!r.ok) throw new Error((await r.text()).slice(0, 220) || 'Push kaydı başarısız');
    return r;
  }

  async function subscription() {
    const reg = await registerSW();
    if (!reg || !reg.pushManager) return null;
    return await reg.pushManager.getSubscription();
  }

  async function registerRemote(sub) {
    const json = sub.toJSON();
    await rpc('register_push_subscription', {
      p_endpoint: json.endpoint,
      p_p256dh: json.keys && json.keys.p256dh,
      p_auth: json.keys && json.keys.auth,
    });
  }

  async function unregisterRemote(sub) {
    try {
      await rpc('unregister_push_subscription', { p_endpoint: sub.endpoint });
    } catch (e) {}
  }

  function installGuide() {
    let modal = document.getElementById('iosInstallGuide');
    if (modal) return;
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
    modal.innerHTML = `<div class="iosGuideCard"><h3>📲 iPhone'da arka plan bildirimini aç</h3><p>iPhone'da gerçek Web Push için Fiyat Radar'ı Ana Ekrana ekleyip uygulama simgesinden açmalısın.</p><div class="iosSteps"><div class="iosStep"><b>1.</b> Safari'de <b>Paylaş ⬆️</b> simgesine dokun.</div><div class="iosStep"><b>2.</b> <b>Ana Ekrana Ekle</b> seçeneğini seç.</div><div class="iosStep"><b>3.</b> Ana ekrandaki <b>Fiyat Radar</b> simgesinden aç.</div><div class="iosStep"><b>4.</b> <b>🔔 Bildirimleri Aç</b> düğmesine dokun ve izni onayla.</div></div><p style="font-size:12px">Ad, telefon, e-posta veya konum tutulmaz. Yalnızca tarayıcının anonim Web Push abonelik anahtarları saklanır.</p><button class="iosGuideClose" type="button">Tamam</button></div>`;
    document.body.appendChild(modal);
    modal.querySelector('.iosGuideClose').onclick = () => modal.remove();
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
  }

  async function showLocal(body, tag) {
    try {
      const reg = await registerSW();
      if (reg && reg.showNotification) {
        await reg.showNotification('Petrol Piyasası Takip', {
          body, tag, icon: '/app-icon.svg', badge: '/app-icon.svg', data: { url: '/fiyatlar' }
        });
      }
    } catch (e) {}
  }

  async function updateButton() {
    const b = document.getElementById('notifyBtn');
    if (!b) return;
    b.disabled = false;

    if (isIOS && !isStandalone()) {
      b.textContent = '📲 Ana Ekrana Ekle';
      b.className = 'btn notifyBtn';
      b.title = 'iPhone arka plan bildirimleri için uygulamayı Ana Ekrana ekleyin';
      return;
    }
    if (!('Notification' in window) || !('serviceWorker' in navigator) || !('PushManager' in window)) {
      b.textContent = '🔕 Push Desteklenmiyor';
      b.className = 'btn notifyBtn blocked';
      b.disabled = true;
      return;
    }
    if (Notification.permission === 'denied') {
      b.textContent = '🚫 Bildirim İzni Kapalı';
      b.className = 'btn notifyBtn blocked';
      return;
    }
    const cfg = await getConfig();
    if (!cfg.configured || !cfg.sender_configured) {
      b.textContent = '⚙️ Push Kurulumu Bekliyor';
      b.className = 'btn notifyBtn blocked';
      b.title = 'Arka plan bildirim altyapısı henüz tamamlanmadı';
      return;
    }
    const sub = await subscription();
    const on = Boolean(sub) && Notification.permission === 'granted';
    localStorage.setItem(PREF, on ? '1' : '0');
    b.textContent = on ? '🔔 Arka Plan Bildirimi Açık' : '🔕 Bildirimleri Aç';
    b.className = 'btn notifyBtn ' + (on ? 'enabled' : '');
    b.title = on ? 'Site kapalıyken de zam/indirim bildirimi gelir' : 'Gerçek Web Push aboneliğini aç';
  }

  async function toggle() {
    if (isIOS && !isStandalone()) {
      installGuide();
      return;
    }
    if (!('Notification' in window) || !('serviceWorker' in navigator) || !('PushManager' in window)) return;
    const cfg = await getConfig();
    if (!cfg.configured || !cfg.sender_configured || !cfg.vapid_public_key) {
      await updateButton();
      return;
    }

    const reg = await registerSW();
    if (!reg || !reg.pushManager) return;
    let existing = await reg.pushManager.getSubscription();
    if (existing && !subscriptionMatchesKey(existing, cfg.vapid_public_key)) {
      await unregisterRemote(existing);
      try { await existing.unsubscribe(); } catch (e) {}
      existing = null;
    }
    if (existing) {
      await unregisterRemote(existing);
      await existing.unsubscribe();
      localStorage.setItem(PREF, '0');
      await updateButton();
      return;
    }

    if (Notification.permission === 'denied') {
      await updateButton();
      return;
    }
    let permission = Notification.permission;
    if (permission === 'default') permission = await Notification.requestPermission();
    if (permission !== 'granted') {
      localStorage.setItem(PREF, '0');
      await updateButton();
      return;
    }

    let sub = null;
    try {
      sub = await subscribeFresh(reg, cfg);
      await registerRemote(sub);
      localStorage.setItem(PREF, '1');
      await showLocal('Arka plan bildirimleri açıldı. Site kapalıyken de fiyat beklentileri bildirilecek.', 'fuel-push-enabled');
    } catch (e) {
      if (sub) try { await sub.unsubscribe(); } catch (_) {}
      localStorage.setItem(PREF, '0');
      const msg = e && e.message ? e.message : 'Bilinmeyen hata';
      if (/push service error/i.test(msg)) {
        alert('Android push servisine kayıt başarısız. Chrome ve Google Play Hizmetleri güncel/açık olmalı. Uygulamayı kapatıp tekrar açtıktan sonra yeniden deneyin. Hata: ' + msg);
      } else {
        alert('Arka plan bildirimi açılamadı: ' + msg);
      }
    }
    await updateButton();
  }

  async function reconcileSubscription() {
    try {
      if (Notification.permission !== 'granted') return;
      const cfg = await getConfig();
      if (!cfg.configured || !cfg.sender_configured) return;
      const reg = await registerSW();
      if (!reg || !reg.pushManager) return;
      let sub = await reg.pushManager.getSubscription();
      const wanted = localStorage.getItem(PREF) === '1';
      if (sub && !subscriptionMatchesKey(sub, cfg.vapid_public_key)) {
        await unregisterRemote(sub);
        try { await sub.unsubscribe(); } catch (e) {}
        sub = null;
      }
      if (!sub && wanted) {
        try { sub = await subscribeFresh(reg, cfg); } catch (e) { sub = null; }
      }
      if (sub) {
        await registerRemote(sub);
        localStorage.setItem(PREF, '1');
      }
    } catch (e) {}
  }

  function notifyChanges(items) {
    window.__latestFuelExpectationItems = items || [];
    const state = {};
    for (const item of items || []) {
      if (item.fuel_key) state[item.fuel_key] = [item.status || '', item.amount ?? '', item.effective_date || ''].join('|');
    }
    localStorage.setItem(STATE, JSON.stringify(state));
  }

  async function takeOver() {
    ensureManifest();
    ensureMobileLayout();
    await registerSW();

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
    await reconcileSubscription();
    await updateButton();
  }

  if (document.readyState === 'loading') {
    window.addEventListener('DOMContentLoaded', () => setTimeout(takeOver, 0));
  } else {
    setTimeout(takeOver, 0);
  }
})();