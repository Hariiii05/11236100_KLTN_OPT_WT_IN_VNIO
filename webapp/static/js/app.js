import { icon } from './icons.js';
import { api, esc, modal, toast, WEEKDAY, parseTs } from './ui.js';
import * as home from './pages/home.js';
import * as reception from './pages/reception.js';
import * as qms from './pages/qms.js';
import * as dashboard from './pages/dashboard.js';

// ------------------------------------------------------------------ replay clock
// The server owns a simulated hospital clock (replay of a real HIS day). We sync
// it periodically and interpolate locally so every screen ticks smoothly.
export const clock = {
  state: null, syncedAt: 0,
  async sync() { this.state = await api('/api/clock'); this.syncedAt = performance.now(); return this.state; },
  nowDate() {
    if (!this.state) return new Date();
    const base = parseTs(this.state.now);
    if (this.state.paused) return base;
    return new Date(base.getTime() + (performance.now() - this.syncedAt) * this.state.speed);
  },
  nowString() {
    const d = this.nowDate(); const p = (x) => String(x).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  },
  async update(body) {
    this.state = await api('/api/clock', { method: 'POST', body }); this.syncedAt = performance.now();
    window.dispatchEvent(new CustomEvent('clock-changed'));
  },
};

export const ctx = { meta: null, clock, openClockDialog };

const ROUTES = [
  { re: /^#?\/?$/, page: home, nav: 'home' },
  { re: /^#\/tiep-don\/?$/, page: reception, nav: 'reception', params: () => ({ step: 1 }) },
  { re: /^#\/tiep-don\/dich-vu\/?$/, page: reception, nav: 'reception', params: () => ({ step: 2 }) },
  { re: /^#\/qms\/?$/, page: qms, nav: 'qms', params: () => ({ view: 'hub' }) },
  { re: /^#\/qms\/tv\/(.+)$/, page: qms, nav: 'qms', tv: true, params: (m) => ({ view: 'tv', room: decodeURIComponent(m[1]) }) },
  { re: /^#\/qms\/khu-vuc\/?(.*)$/, page: qms, nav: 'qms', tv: true, params: (m) => ({ view: 'area', department: decodeURIComponent(m[1] || '') }) },
  { re: /^#\/qms\/dieu-phoi\/(.+)$/, page: qms, nav: 'qms', params: (m) => ({ view: 'console', room: decodeURIComponent(m[1]) }) },
  { re: /^#\/qms\/tra-cuu\/?$/, page: qms, nav: 'qms', tv: true, params: () => ({ view: 'kiosk' }) },
  { re: /^#\/dashboard\/?$/, page: dashboard, nav: 'dashboard' },
];

const SCREENS = [
  ['Trang chủ', '#/', 'home'],
  ['Quản lý tiếp đón – Thông tin hành chính', '#/tiep-don', 'reception'],
  ['Chỉ định dịch vụ – Dự báo thời gian chờ', '#/tiep-don/dich-vu', 'stethoscope'],
  ['QMS – Quản lý hàng đợi', '#/qms', 'qms'],
  ['QMS – Màn hình khu vực chờ', '#/qms/khu-vuc/', 'users'],
  ['QMS – Kiosk tra cứu số thứ tự', '#/qms/tra-cuu', 'kiosk'],
  ['Dashboard – Theo dõi và phân tích', '#/dashboard', 'dashboard'],
];

function shell() {
  const header = document.querySelector('.app-header');
  header.innerHTML = `
    <a class="brand" href="#/"><span class="brand-mark">${icon('logo')}</span><span>${esc(ctx.meta.hospital)}</span></a>
    <span class="header-sep"></span>
    <a class="icon-btn" href="#/" title="Trang chủ">${icon('grid')}</a>
    <div class="screen-search">
      <input id="screenSearch" placeholder="Tìm kiếm tên màn hình" autocomplete="off">
      <kbd>Ctrl + K</kbd>${icon('search')}
      <div class="screen-results" id="screenResults"></div>
    </div>
    <div class="header-right">
      <div class="clock-widget" title="Đồng hồ mô phỏng: phát lại dữ liệu HIS thật">
        <span class="replay-pill">Phát lại HIS</span>
        <div class="clock-text"><span class="clock-time" id="clockTime">--:--:--</span><span class="clock-date" id="clockDate"></span></div>
        <button id="clockToggle" aria-label="Tạm dừng / chạy">${icon('pause')}</button>
        <button id="clockSettings" aria-label="Đặt đồng hồ">${icon('clock')}</button>
      </div>
    </div>`;
  header.querySelector('#clockToggle').addEventListener('click', async () => {
    await clock.update({ paused: !clock.state.paused });
    toast(clock.state.paused ? 'Đã tạm dừng đồng hồ' : 'Đồng hồ tiếp tục chạy');
  });
  header.querySelector('#clockSettings').addEventListener('click', openClockDialog);

  const input = header.querySelector('#screenSearch');
  const box = header.querySelector('#screenResults');
  let active = 0;
  const norm = (x) => x.normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/đ/g, 'd').toLowerCase();
  const matches = () => SCREENS.filter(([name]) => norm(name).includes(norm(input.value.trim())));
  const draw = () => {
    const list = matches();
    active = Math.min(active, Math.max(list.length - 1, 0));
    box.innerHTML = list.map(([name, href, ic], i) => `<a href="${href}" class="${i === active ? 'active' : ''}">${icon(ic)}${esc(name)}</a>`).join('')
      || '<div class="empty">Không tìm thấy màn hình</div>';
    box.style.display = 'block';
  };
  const close = () => { box.style.display = 'none'; };
  input.addEventListener('focus', draw);
  input.addEventListener('input', () => { active = 0; draw(); });
  input.addEventListener('keydown', (e) => {
    const list = matches();
    if (e.key === 'ArrowDown') { active = Math.min(active + 1, list.length - 1); draw(); e.preventDefault(); }
    if (e.key === 'ArrowUp') { active = Math.max(active - 1, 0); draw(); e.preventDefault(); }
    if (e.key === 'Enter' && list[active]) { location.hash = list[active][1]; input.value = ''; close(); input.blur(); }
    if (e.key === 'Escape') { close(); input.blur(); }
  });
  box.addEventListener('mousedown', (e) => { if (e.target.closest('a')) { input.value = ''; setTimeout(close, 0); } });
  input.addEventListener('blur', () => setTimeout(close, 150));
  document.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.key.toLowerCase() === 'k') { e.preventDefault(); input.focus(); input.select(); }
  });
}

function tickClock() {
  const t = document.getElementById('clockTime');
  if (!t || !clock.state) return;
  const s = clock.nowString();
  t.textContent = s.slice(11);
  document.getElementById('clockDate').textContent = `${WEEKDAY[clock.nowDate().getDay()]}, ${s.slice(8, 10)}/${s.slice(5, 7)}/${s.slice(0, 4)}${clock.state.speed !== 1 ? ` · x${clock.state.speed}` : ''}`;
  document.getElementById('clockToggle').innerHTML = icon(clock.state.paused ? 'play' : 'pause');
}

export function openClockDialog() {
  const now = clock.nowString();
  const speeds = [1, 5, 15, 60];
  const m = modal({
    title: 'Đặt đồng hồ',
    body: `
      <div class="fg fg-2">
        <div class="f"><label>Ngày</label><input class="in" type="date" id="cDate" value="${now.slice(0, 10)}" min="${clock.state.min.slice(0, 10)}" max="${clock.state.max.slice(0, 10)}"></div>
        <div class="f"><label>Giờ</label><input class="in" type="time" id="cTime" step="60" value="${now.slice(11, 16)}"></div>
        <div class="f full"><label>Tốc độ</label><div class="row" id="cSpeed">${speeds.map((s) => `<button data-v="${s}" class="btn btn-sm ${clock.state.speed === s ? 'btn-primary' : ''}">x${s}</button>`).join('')}</div></div>
      </div>
      <div class="row wrap" style="margin-top:14px">${['07:00', '08:30', '10:00', '11:00', '13:30', '15:00'].map((t) => `<button class="btn btn-sm" data-t="${t}">${t}</button>`).join('')}</div>`,
    footer: '<button class="btn" data-close>Hủy</button><button class="btn btn-primary" id="cApply">Áp dụng</button>',
  });
  let speed = clock.state.speed;
  m.el.querySelectorAll('#cSpeed button').forEach((b) => b.addEventListener('click', () => {
    speed = Number(b.dataset.v); m.el.querySelectorAll('#cSpeed button').forEach((x) => x.classList.toggle('btn-primary', x === b));
  }));
  m.el.querySelectorAll('[data-t]').forEach((b) => b.addEventListener('click', () => { m.el.querySelector('#cTime').value = b.dataset.t; }));
  m.el.querySelector('#cApply').addEventListener('click', async () => {
    try {
      await clock.update({ now: `${m.el.querySelector('#cDate').value} ${m.el.querySelector('#cTime').value}:00`, speed });
      m.close(); toast('Đã cập nhật đồng hồ', 'success');
    } catch (e) { toast(e.message, 'error'); }
  });
}

// ------------------------------------------------------------------ router
let cleanup = null;
async function route() {
  if (cleanup) { try { cleanup(); } catch { /* ignore */ } cleanup = null; }
  const hash = location.hash || '#/';
  const match = ROUTES.map((r) => ({ r, m: hash.match(r.re) })).find((x) => x.m);
  const { r, m } = match || { r: ROUTES[0], m: [] };
  document.body.classList.toggle('tv', !!r.tv);
  const root = document.getElementById('app');
  root.innerHTML = '';
  window.scrollTo(0, 0);
  cleanup = await r.page.render(root, r.params ? r.params(m) : {}, ctx);
}

async function boot() {
  try {
    ctx.meta = await api('/api/meta');
    await clock.sync();
  } catch (e) {
    document.getElementById('app').innerHTML = `<div class="container"><div class="alert critical">${icon('alertCircle')}<div>
      <div class="alert-title">Không kết nối được máy chủ</div>${esc(e.message)}</div></div></div>`;
    return;
  }
  shell();
  setInterval(tickClock, 1000);
  setInterval(() => clock.sync().catch(() => {}), 20000);
  tickClock();
  window.addEventListener('hashchange', route);
  route();
}

boot();
