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
        .sourcebar{gap:6px}
        .source{font-size:10px;padding:6px 8px}
        .tableWrap{border-radius:10px}
        .table th,.table td{padding:9px 8px;font-size:12px}
        .change{padding:10px}
        .footer{font-size:10px}
      }
      @media(max-width:390px){
        .wrap{padding:12px 10px}
        .nav{grid-template-columns:1fr 1fr}
        .notifyActions{grid-template-columns:1fr}
        .top h1{font-size:30px}
        .expectationRow{grid-template-columns:1fr}
        .expectationRow .expLabel,.expectationRow>a,.expectationRow>strong,.expectationRow .expSource{grid-column:1}
        .chartWrap{height:220px!important}
        .chartWrap.tall{height:245px!important}
      }
    `;
    document.head.appendChild(style);
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
    ensureMobileLayout();
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