import { icon } from './icons.js';

// ------------------------------------------------------------------ API
export class ApiError extends Error {
  constructor(message, status, errors) { super(message); this.status = status; this.errors = errors || {}; }
}

export async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch(path, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) {
    const detail = typeof data?.detail === 'string' ? data.detail : 'Yêu cầu không thành công';
    throw new ApiError(detail, res.status, data?.errors);
  }
  return data;
}

// ------------------------------------------------------------------ formatting
export const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const nf0 = new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 1, minimumFractionDigits: 1 });
const nf2 = new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 2, minimumFractionDigits: 2 });
export const n0 = (v) => (v === null || v === undefined || Number.isNaN(v) ? '–' : nf0.format(v));
export const n1 = (v) => (v === null || v === undefined || Number.isNaN(v) ? '–' : nf1.format(v));
export const n2 = (v) => (v === null || v === undefined || Number.isNaN(v) ? '–' : nf2.format(v));
export const pct = (v) => (v === null || v === undefined ? '–' : `${nf1.format(v)}%`);

export const parseTs = (s) => (s ? new Date(s.replace(' ', 'T')) : null);
export const hhmm = (s) => (s ? s.slice(11, 16) : '–');
export const ddmm = (s) => (s ? `${s.slice(8, 10)}/${s.slice(5, 7)}/${s.slice(0, 4)}` : '–');
export const WEEKDAY = ['Chủ nhật', 'Thứ 2', 'Thứ 3', 'Thứ 4', 'Thứ 5', 'Thứ 6', 'Thứ 7'];
export const weekday = (s) => (s ? WEEKDAY[parseTs(s).getDay()] : '');
export function minutes(v) {
  if (v === null || v === undefined) return '–';
  const m = Math.round(v);
  if (m < 60) return `${m} phút`;
  return `${Math.floor(m / 60)} giờ ${String(m % 60).padStart(2, '0')} phút`;
}
export function age(dob, nowStr) {
  if (!dob) return '';
  const d = new Date(dob); const now = parseTs(nowStr) || new Date();
  let a = now.getFullYear() - d.getFullYear();
  if (now < new Date(now.getFullYear(), d.getMonth(), d.getDate())) a -= 1;
  return a;
}
export const initials = (name) => (name || '?').trim().split(/\s+/).slice(-2).map((p) => p[0]).join('').toUpperCase();

export const STATUS = {
  waiting: ['Đang chờ', 'hourglass'], soon: ['Sắp tới lượt', 'bell'], called: ['Đang gọi', 'megaphone'],
  in_exam: ['Đang khám', 'stethoscope'], done: ['Hoàn thành', 'checkCircle'], skipped: ['Bỏ lượt', 'skip'],
  cancelled: ['Đã hủy', 'xCircle'],
};
export function statusBadge(status) {
  const [label, ic] = STATUS[status] || [status, 'info'];
  return `<span class="badge ${esc(status)}">${icon(ic)}${esc(label)}</span>`;
}
export const sourceBadge = (source) => (source === 'his'
  ? '<span class="badge his" title="Lượt khám thật từ dữ liệu HIS được phát lại">HIS</span>'
  : '<span class="badge web" title="Tiếp đón trên hệ thống này">WEB</span>');
export const waitClass = (m) => (m === null || m === undefined ? '' : m < 20 ? 'low' : m < 45 ? 'mid' : 'high');

// ------------------------------------------------------------------ notice bar & modal
// Notifications use the dark bar at the bottom of the screen (as in the HIS reference).
let noticeTimer;
export function toast(message, kind = '') {
  let bar = document.querySelector('.notice-bar');
  if (!bar) { bar = document.createElement('div'); bar.className = 'notice-bar'; bar.setAttribute('role', 'status'); bar.setAttribute('aria-live', 'polite'); document.body.append(bar); }
  bar.className = `notice-bar ${kind}`;
  bar.innerHTML = `${icon(kind === 'error' ? 'alertCircle' : kind === 'success' ? 'checkCircle' : 'info')}<span>${esc(message)}</span>`;
  requestAnimationFrame(() => bar.classList.add('show'));
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => bar.classList.remove('show'), 4500);
}

// Lunch-break warning label ("Chờ quá giờ nghỉ trưa"), identical on every screen.
export function lunchBadge(t, { long = false } = {}) {
  if (!t || !t.lunch_warning) return '';
  const back = t.suggested_return_time ? ` · quay lại ${hhmm(t.suggested_return_time)}` : '';
  return `<span class="badge lunch" title="Mô hình cảnh báo có thể phải chờ qua giờ nghỉ trưa${back}">${icon('sun')}${long ? `Chờ quá giờ nghỉ trưa${back}` : 'Quá giờ trưa'}</span>`;
}

export function modal({ title, body, footer = '', wide = false, onClose }) {
  const wrap = document.createElement('div');
  wrap.className = 'modal-backdrop';
  wrap.innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-labelledby="modalTitle" style="${wide ? 'width:min(980px,100%)' : ''}">
      <div class="modal-head"><h2 id="modalTitle">${title}</h2><button class="btn btn-ghost btn-icon" data-close aria-label="Đóng" style="margin-left:auto">${icon('x')}</button></div>
      <div class="modal-body">${body}</div>${footer ? `<div class="modal-foot">${footer}</div>` : ''}</div>`;
  const opener = document.activeElement;
  const close = () => { wrap.remove(); document.removeEventListener('keydown', onKey); opener?.focus?.(); onClose?.(); };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  wrap.addEventListener('click', (e) => { if (e.target === wrap || e.target.closest('[data-close]')) close(); });
  document.addEventListener('keydown', onKey);
  document.body.append(wrap);
  (wrap.querySelector('.modal-body input, .modal-body select, .modal-body textarea') || wrap.querySelector('[data-close]'))?.focus();
  return { el: wrap, close };
}

export function confirmDialog(title, message, okLabel = 'Xác nhận', danger = false) {
  return new Promise((resolve) => {
    let answered = false;
    const m = modal({
      title, body: `<p class="text-2">${message}</p>`,
      footer: `<button class="btn" data-close>Hủy</button><button class="btn ${danger ? 'btn-danger' : 'btn-primary'}" data-ok>${okLabel}</button>`,
      onClose: () => { if (!answered) resolve(false); },
    });
    m.el.querySelector('[data-ok]').addEventListener('click', () => { answered = true; m.close(); resolve(true); });
  });
}

export const emptyState = (text, ic = 'list') => `<div class="empty">${icon(ic)}<div>${esc(text)}</div></div>`;

export function selectOptions(items, value, placeholder) {
  const opts = placeholder !== undefined ? [`<option value="">${esc(placeholder)}</option>`] : [];
  for (const item of items) {
    const v = typeof item === 'object' ? item.value : item;
    const label = typeof item === 'object' ? item.label : item;
    const dis = typeof item === 'object' && item.disabled ? ' disabled' : '';
    opts.push(`<option value="${esc(v)}"${String(v) === String(value ?? '') ? ' selected' : ''}${dis}>${esc(label)}</option>`);
  }
  return opts.join('');
}
