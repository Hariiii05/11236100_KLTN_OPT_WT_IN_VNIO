// QMS – bám theo ảnh mẫu "2. QMS hiển thị".
import { icon } from '../icons.js';
import {
  api, esc, n0, hhmm, ddmm, weekday, minutes, toast, statusBadge, sourceBadge, selectOptions, emptyState, confirmDialog, lunchBadge,
} from '../ui.js';

export async function render(root, params, ctx) {
  switch (params.view) {
    case 'tv': return renderTv(root, params.room, ctx);
    case 'area': return renderArea(root, params.department, ctx);
    case 'console': return renderConsole(root, params.room, ctx);
    case 'kiosk': return renderKiosk(root, ctx);
    default: return renderHub(root, ctx);
  }
}

function poll(fn, ms) {
  let stopped = false;
  const run = async () => { if (!stopped) { try { await fn(); } catch (e) { console.warn(e); } } };
  run();
  const t = setInterval(run, ms);
  window.addEventListener('clock-changed', run);
  return () => { stopped = true; clearInterval(t); window.removeEventListener('clock-changed', run); };
}

const eta = (t) => {
  if (t.prediction_status !== 'ok' || !t.predicted_exam_time) return '<span class="muted">–</span>';
  const rem = t.remaining_minutes;
  return `${hhmm(t.predicted_exam_time)}<small>${rem > 0 ? `còn ~${Math.round(rem)} phút` : 'sắp tới lượt'}</small>`;
};

// ------------------------------------------------------------------ màn hình (khung chung)
function screen(ctx, title, inner) {
  return `<div class="qms-screen">
    <div class="qms-top"><a class="exit btn btn-ghost btn-sm" href="#/qms">${icon('arrowLeft')}Thoát</a>
      <span class="logo">${icon('logo')}</span><span class="hosp">${esc(ctx.meta.hospital)}</span>
      <div class="clock"><b id="tvClock">--:--</b><span id="tvDate"></span></div></div>
    <div class="qms-frame">${title ? `<div class="qms-title">${title}</div>` : ''}${inner}</div>
    <div class="qms-foot">Powered by Opt-WT</div></div>`;
}
function startClock(root, ctx) {
  const tick = () => {
    const s = ctx.clock.nowString();
    const t = root.querySelector('#tvClock'); if (!t) return;
    t.textContent = s.slice(11, 16);
    root.querySelector('#tvDate').textContent = `${weekday(s)}, ${ddmm(s)}`;
  };
  tick();
  const id = setInterval(tick, 1000);
  return () => clearInterval(id);
}

// ================================================================== HUB
async function renderHub(root, ctx) {
  const rooms = ctx.meta.catalog.rooms.filter((r) => r.in_model);
  const deps = ctx.meta.catalog.departments;
  const saved = localStorage.getItem('optwt.qms.room') || '';
  const roomOpts = rooms.map((r) => ({ value: r.ma_phong, label: `${r.ma_phong} – ${r.ten_phong}` }));
  const depOpts = deps.map((d) => ({ value: d.ma, label: d.ten }));
  root.innerHTML = `
  <div class="container">
    <div class="page-title"><h1>QMS – Quản lý hàng đợi</h1></div>
    <div class="card"><div class="card-body tight"><div class="row wrap" style="gap:10px">
      <div class="row"><select class="in" id="room" style="width:250px">${selectOptions(roomOpts, saved, 'Chọn phòng khám')}</select>
        <button class="btn btn-primary" id="openTv">${icon('qms')}Màn hình phòng khám</button>
        <button class="btn btn-calling" id="openCon">${icon('megaphone')}Điều phối</button></div>
      <span class="header-sep" style="background:var(--border)"></span>
      <div class="row"><select class="in" id="dep" style="width:190px">${selectOptions(depOpts, '', 'Tất cả chuyên khoa')}</select>
        <button class="btn btn-primary" id="openArea">${icon('users')}Màn hình khu vực chờ</button></div>
      <span class="header-sep" style="background:var(--border)"></span>
      <a class="btn btn-outline" href="#/qms/tra-cuu">${icon('kiosk')}Kiosk tra cứu</a>
    </div></div></div>

    <div class="panel" style="margin-top:12px"><div class="panel-head">Hàng đợi theo phòng khám<span class="right" id="hubNow"></span></div>
      <div class="table-wrap"><table class="table"><thead><tr><th>Phòng khám</th><th>Đang khám</th><th>Số tiếp theo</th>
        <th class="num">Đang chờ</th><th class="num">Chờ dự kiến</th><th>Cảnh báo</th><th></th></tr></thead><tbody id="hubBody"></tbody></table></div></div>
  </div>`;
  const $ = (s) => root.querySelector(s);
  const go = (prefix) => { const v = $('#room').value; if (!v) { toast('Vui lòng chọn phòng khám', 'error'); return; } localStorage.setItem('optwt.qms.room', v); location.hash = `${prefix}${encodeURIComponent(v)}`; };
  $('#openTv').addEventListener('click', () => go('#/qms/tv/'));
  $('#openCon').addEventListener('click', () => go('#/qms/dieu-phoi/'));
  $('#openArea').addEventListener('click', () => { location.hash = `#/qms/khu-vuc/${encodeURIComponent($('#dep').value)}`; });
  const load = async () => {
    const dep = $('#dep').value;
    const data = await api(`/api/queue/area${dep ? `?department=${encodeURIComponent(dep)}` : ''}`);
    $('#hubNow').textContent = `${data.rooms.length} phòng đang hoạt động · ${hhmm(data.now)}`;
    const list = [...data.rooms].sort((x, y) => y.waiting - x.waiting || y.lunch_warnings - x.lunch_warnings);
    $('#hubBody').innerHTML = list.map((r) => `<tr>
      <td><b>${esc(r.room.ma_phong)}</b> – ${esc(r.room.ten_phong)}</td>
      <td>${r.current.map((n) => `<span class="badge in_exam">${esc(n)}</span>`).join(' ') || '–'}</td>
      <td>${r.next.map((n) => `<span class="badge soon">${esc(n)}</span>`).join(' ') || '–'}</td>
      <td class="num"><b>${n0(r.waiting)}</b></td><td class="num">${r.median_predicted_wait == null ? '–' : `${n0(r.median_predicted_wait)} phút`}</td>
      <td>${r.lunch_warnings ? `<span class="badge lunch">${icon('sun')}${r.lunch_warnings} NB quá giờ trưa</span>` : ''}</td>
      <td class="nowrap" style="text-align:right"><a class="btn btn-sm" href="#/qms/tv/${encodeURIComponent(r.room.ma_phong)}">${icon('qms')}Màn hình</a>
        <a class="btn btn-sm" href="#/qms/dieu-phoi/${encodeURIComponent(r.room.ma_phong)}">${icon('megaphone')}Điều phối</a></td></tr>`).join('')
      || `<tr><td colspan="7">${emptyState('Hiện không có người bệnh trong hàng đợi', 'users')}</td></tr>`;
  };
  $('#dep').addEventListener('change', load);
  return poll(load, 15000);
}

// ================================================================== TV phòng khám
async function renderTv(root, room, ctx) {
  const info = ctx.meta.catalog.rooms.find((r) => r.ma_phong === room) || { ma_phong: room, ten_phong: room };
  root.innerHTML = screen(ctx, esc(info.ten_phong), `
    <div class="qms-band"><span class="t" id="bandTitle">» ĐANG KHÁM</span><div class="nums" id="bandNums"></div><span class="who" id="bandWho"></span></div>
    <div class="qms-next" id="next"></div>
    <div class="qms-cols">
      <div class="qms-col"><div class="qms-col-head green">» CHỜ KHÁM<span class="count" id="waitCount"></span></div><div class="qms-list" id="waitList"></div></div>
      <div class="qms-col"><div class="qms-col-head red">» GỌI NHỠ<span class="count" id="missCount"></span></div><div class="qms-list" id="missList"></div></div>
    </div>`);
  const stopClock = startClock(root, ctx);
  const $ = (s) => root.querySelector(s);
  const stop = poll(async () => {
    const b = await api(`/api/queue/room/${encodeURIComponent(room)}`);
    const called = b.called[b.called.length - 1];
    const current = called || b.in_exam[b.in_exam.length - 1];
    $('#bandTitle').textContent = called ? '» MỜI VÀO KHÁM' : '» ĐANG KHÁM';
    const others = b.in_exam.filter((t) => !called || t.id !== called.id).slice(0, -1).slice(-3).map((t) => t.display_stt);
    $('#bandNums').innerHTML = current ? `<span class="main ${called ? 'calling' : ''}">${esc(current.display_stt)}</span>${!called && others.length ? `<span class="rest">${others.map(esc).join(' · ')}</span>` : ''}` : '<span class="rest">–</span>';
    $('#bandWho').textContent = called ? called.patient : '';
    $('#next').innerHTML = b.waiting.length ? `Tiếp theo: ${b.waiting.slice(0, 5).map((t) => `<span class="n">${esc(t.display_stt)}</span>`).join('')}` : '';
    $('#waitCount').textContent = `(${b.stats.waiting} NB)`;
    $('#missCount').textContent = `(${b.stats.skipped} NB)`;
    $('#waitList').innerHTML = b.waiting.slice(0, 10).map((t) => `<div class="qms-row ${t.status === 'soon' ? 'soon' : ''}">
        <span class="stt">${esc(t.display_stt)}</span>
        <span class="nm">${esc(t.patient)} ${t.uu_tien ? '<span class="badge prio">Ưu tiên</span>' : ''} ${lunchBadge(t)}</span>
        <span class="eta">${eta(t)}</span></div>`).join('') || '<div class="empty" style="font-size:18px">Không có người bệnh đang chờ</div>';
    $('#missList').innerHTML = b.skipped.map((t) => `<div class="qms-row"><span class="stt" style="color:var(--red)">${esc(t.display_stt)}</span>
        <span class="nm">${esc(t.patient)}</span><span class="eta" style="color:var(--text-2)">${hhmm(t.skipped_time)}</span></div>`).join('');
  }, 5000);
  return () => { stop(); stopClock(); };
}

// ================================================================== TV khu vực chờ
async function renderArea(root, department, ctx) {
  const dep = ctx.meta.catalog.departments.find((d) => d.ma === department);
  root.innerHTML = screen(ctx, dep ? esc(dep.ten) : 'Khu vực chờ khám', '<div class="area-grid" id="grid"></div>');
  const stopClock = startClock(root, ctx);
  const stop = poll(async () => {
    const data = await api(`/api/queue/area${department ? `?department=${encodeURIComponent(department)}` : ''}`);
    root.querySelector('#grid').innerHTML = data.rooms.map((r) => `<div class="area-tile">
      <div class="r-name">${esc(r.room.ten_phong)}</div>
      <div class="r-now"><span class="muted" style="font-weight:700">ĐANG KHÁM</span>
        <span class="big">${r.calling.length ? `<span style="color:var(--calling)">${esc(r.calling[r.calling.length - 1])}</span>` : esc(r.current[r.current.length - 1] ?? '–')}</span></div>
      <div class="r-next"><span class="muted">Tiếp theo</span>${r.next.map((n) => `<span class="n">${esc(n)}</span>`).join('') || '–'}</div>
      ${r.lunch_warnings ? `<div><span class="badge lunch">${icon('sun')}${r.lunch_warnings} người bệnh có thể chờ quá giờ trưa</span></div>` : ''}
      <div class="r-stats"><span>Đang chờ <b>${r.waiting}</b></span>
        <span>Chờ dự kiến <b>${r.median_predicted_wait == null ? '–' : `~${r.median_predicted_wait} phút`}</b></span></div></div>`).join('')
      || `<div>${emptyState('Hiện không có người bệnh đang chờ', 'users')}</div>`;
  }, 8000);
  return () => { stop(); stopClock(); };
}

// ================================================================== Điều phối phòng khám
async function renderConsole(root, room, ctx) {
  const info = ctx.meta.catalog.rooms.find((r) => r.ma_phong === room) || { ma_phong: room, ten_phong: room };
  localStorage.setItem('optwt.qms.room', room);
  root.innerHTML = `<div class="container">
    <div class="page-title"><h1>Điều phối: ${esc(info.ma_phong)} – ${esc(info.ten_phong)}</h1>
      <div class="right"><a class="btn" href="#/qms">${icon('arrowLeft')}QMS</a><a class="btn" href="#/qms/tv/${encodeURIComponent(room)}">${icon('qms')}Màn hình phòng</a>
        <button class="btn btn-calling btn-lg" id="btnNext">${icon('megaphone')}Gọi số tiếp theo</button></div></div>
    <div class="kpi-strip" id="stats"></div>
    <div class="grid" style="grid-template-columns:minmax(0,1fr) minmax(0,1.6fr);margin-top:12px;align-items:start">
      <div class="stack">
        <div class="panel"><div class="panel-head">Đang gọi</div><div class="panel-body" id="calledBox"></div></div>
        <div class="panel"><div class="panel-head">Đang khám<span class="right" id="examCount"></span></div><div class="table-wrap" style="max-height:300px"><table class="table"><tbody id="examBody"></tbody></table></div></div>
        <div class="panel"><div class="panel-head">Gọi nhỡ</div><div class="table-wrap"><table class="table"><tbody id="skipBody"></tbody></table></div></div>
      </div>
      <div class="panel"><div class="panel-head">Danh sách chờ</div>
        <div class="table-wrap" style="max-height:calc(100vh - 290px)"><table class="table"><thead><tr><th>STT</th><th>Người bệnh</th><th class="num">Vào hàng đợi</th><th class="num">Đã chờ</th>
          <th class="num">Dự kiến khám</th><th>Trạng thái</th><th></th></tr></thead><tbody id="waitBody"></tbody></table></div></div>
    </div>
    <div class="panel" style="margin-top:12px"><div class="panel-head">Đã khám – thời gian chờ thực tế và dự báo</div>
      <div class="table-wrap" style="max-height:300px"><table class="table"><thead><tr><th>STT</th><th>Người bệnh</th><th class="num">Vào hàng đợi</th><th class="num">Bắt đầu khám</th>
        <th class="num">Chờ thực tế</th><th class="num">Dự báo</th><th class="num">Chênh lệch</th></tr></thead><tbody id="doneBody"></tbody></table></div></div>
  </div>`;
  const $ = (s) => root.querySelector(s);
  const act = async (id, action, label) => {
    if (action === 'cancel' && !(await confirmDialog('Hủy phiếu', 'Hủy phiếu này khỏi hàng đợi?', 'Hủy phiếu', true))) return;
    try { const t = await api(`/api/tickets/${encodeURIComponent(id)}/${action}`, { method: 'POST' }); toast(`${label}: số ${t.display_stt}`, 'success'); load(); } catch (e) { toast(e.message, 'error'); }
  };
  const actions = (t) => {
    if (t.source !== 'web') return '';
    const b = (a, l, cls, ic) => `<button class="btn btn-sm ${cls}" data-id="${esc(t.id)}" data-a="${a}" data-l="${l}">${icon(ic)}${l}</button>`;
    if (t.status === 'waiting' || t.status === 'soon') return `${b('call', 'Gọi', 'btn-calling', 'megaphone')} ${b('start', 'Khám', '', 'playCircle')} ${b('skip', 'Bỏ lượt', 'btn-danger', 'skip')}`;
    if (t.status === 'called') return `${b('recall', 'Gọi lại', '', 'megaphone')} ${b('start', 'Bắt đầu khám', 'btn-success', 'playCircle')} ${b('skip', 'Bỏ lượt', 'btn-danger', 'skip')}`;
    if (t.status === 'in_exam') return b('finish', 'Hoàn thành', 'btn-success', 'flag');
    if (t.status === 'skipped') return `${b('restore', 'Khôi phục', '', 'undo')} ${b('call', 'Gọi lại', 'btn-calling', 'megaphone')} ${b('cancel', 'Hủy', 'btn-danger', 'trash')}`;
    return '';
  };
  const who = (t) => `<b>${esc(t.source === 'web' ? t.ho_ten : t.patient)}</b> ${sourceBadge(t.source)}${t.uu_tien ? ' <span class="badge prio">UT</span>' : ''} ${['waiting', 'soon', 'called'].includes(t.status) ? lunchBadge(t) : ''}`;
  root.addEventListener('click', (e) => { const btn = e.target.closest('[data-a]'); if (btn) act(btn.dataset.id, btn.dataset.a, btn.dataset.l); });
  $('#btnNext').addEventListener('click', async () => {
    try { const t = await api(`/api/queue/room/${encodeURIComponent(room)}/call-next`, { method: 'POST' }); toast(`Đang gọi số ${t.display_stt} – ${t.ho_ten}`, 'success'); load(); } catch (e) { toast(e.message, 'error'); }
  });
  const kpi = (label, value) => `<div class="card kpi"><div class="kpi-label">${label}</div><div class="kpi-value">${value}</div></div>`;
  const load = async () => {
    const b = await api(`/api/queue/room/${encodeURIComponent(room)}`);
    const st = b.stats;
    $('#stats').innerHTML = kpi('Đang chờ', n0(st.waiting)) + kpi('Đang khám', n0(st.in_exam)) + kpi('Đã khám hôm nay', n0(st.done))
      + kpi('Chờ thực tế TB hôm nay', st.avg_actual_wait_today == null ? '–' : `${Math.round(st.avg_actual_wait_today)}<small>phút</small>`)
      + kpi('Chờ dự kiến TB (60 phút qua)', st.avg_predicted_wait_60m == null ? '–' : `${Math.round(st.avg_predicted_wait_60m)}<small>phút</small>`);
    const c = b.called[b.called.length - 1];
    $('#calledBox').innerHTML = c ? `<div class="console-current"><div class="big">${esc(c.display_stt)}</div><div style="flex:1">${who(c)}
        <div class="cell-sub">Gọi lúc ${hhmm(c.called_time)} · lần ${c.call_count} · đã chờ ${minutes(c.waited_minutes)}</div><div class="row wrap" style="margin-top:8px">${actions(c)}</div></div></div>`
      : emptyState('Chưa gọi người bệnh', 'megaphone');
    $('#examCount').textContent = `${st.in_exam} lượt`;
    $('#examBody').innerHTML = b.in_exam.slice().reverse().map((t) => `<tr><td style="width:56px"><b>${esc(t.display_stt)}</b></td><td>${who(t)}<div class="cell-sub">Bắt đầu ${hhmm(t.start_time)} · chờ ${minutes(t.actual_wait_minutes)}</div></td><td style="text-align:right">${actions(t)}</td></tr>`).join('')
      || `<tr><td>${emptyState('Không có', 'stethoscope')}</td></tr>`;
    $('#skipBody').innerHTML = b.skipped.map((t) => `<tr><td style="width:56px"><b>${esc(t.display_stt)}</b></td><td>${who(t)}<div class="cell-sub">Bỏ lượt ${hhmm(t.skipped_time)}</div></td><td class="nowrap" style="text-align:right">${actions(t)}</td></tr>`).join('')
      || '<tr><td class="muted">Không có</td></tr>';
    $('#waitBody').innerHTML = b.waiting.map((t) => `<tr><td><b>${esc(t.display_stt)}</b></td><td>${who(t)}</td><td class="num">${hhmm(t.queue_entry_time)}</td>
      <td class="num" style="${t.overdue_minutes > 0 ? 'color:var(--critical-ink);font-weight:700' : ''}">${Math.round(t.waited_minutes)} phút</td>
      <td class="num">${t.prediction_status === 'ok' ? `<b>${hhmm(t.predicted_exam_time)}</b> <span class="muted">(${Math.round(t.predicted_wait_minutes)}′)</span>` : '–'}</td>
      <td>${statusBadge(t.status)}</td><td class="nowrap" style="text-align:right">${actions(t)}</td></tr>`).join('')
      || `<tr><td colspan="7">${emptyState('Không có người bệnh đang chờ', 'users')}</td></tr>`;
    $('#doneBody').innerHTML = b.done.slice().reverse().map((t) => {
      const err = t.prediction_status === 'ok' ? t.predicted_wait_minutes - t.actual_wait_minutes : null;
      return `<tr><td><b>${esc(t.display_stt)}</b></td><td>${who(t)}</td><td class="num">${hhmm(t.queue_entry_time)}</td><td class="num">${hhmm(t.start_time)}</td>
        <td class="num"><b>${n0(t.actual_wait_minutes)} phút</b></td><td class="num">${t.prediction_status === 'ok' ? `${n0(t.predicted_wait_minutes)} phút` : '–'}</td>
        <td class="num" style="color:${err == null ? '' : Math.abs(err) <= 10 ? 'var(--good-ink)' : 'var(--critical-ink)'}">${err == null ? '–' : `${err > 0 ? '+' : ''}${Math.round(err)} phút`}</td></tr>`;
    }).join('') || `<tr><td colspan="7">${emptyState('Chưa có lượt hoàn thành', 'checkCircle')}</td></tr>`;
  };
  return poll(load, 7000);
}

// ================================================================== Kiosk tra cứu
async function renderKiosk(root, ctx) {
  root.innerHTML = screen(ctx, 'Tra cứu số thứ tự', `<div class="kiosk-box">
      <div class="row"><input class="in k-input" id="q" placeholder="Số điện thoại / mã người bệnh / mã phiếu" autocomplete="off">
        <button class="btn btn-primary" style="height:58px;padding:0 26px;font-size:18px" id="go">${icon('search')}Tra cứu</button></div>
      <div class="row wrap" style="margin-top:10px;justify-content:center" id="pad">${[1, 2, 3, 4, 5, 6, 7, 8, 9, 0].map((d) => `<button class="btn" style="width:58px;height:48px;font-size:20px" data-d="${d}">${d}</button>`).join('')}
        <button class="btn" style="height:48px" data-d="del">${icon('arrowLeft')}Xóa</button></div>
      <div id="result" style="margin-top:14px"></div></div>`);
  const stopClock = startClock(root, ctx);
  const $ = (s) => root.querySelector(s);
  const search = async () => {
    const q = $('#q').value.trim();
    if (!q) return;
    const rows = await api(`/api/lookup?q=${encodeURIComponent(q)}`);
    $('#result').innerHTML = rows.length ? rows.slice().reverse().map((t) => `<div class="card kiosk-card">
      <div class="muted" style="font-size:15px">${esc(t.room.ten_phong)} · ${esc(t.room.dia_diem || '')}</div>
      <div class="stt">${esc(t.display_stt)}</div><div style="font-size:20px;font-weight:700">${esc(t.patient)}</div>
      <div style="margin:10px 0">${statusBadge(t.status)} ${lunchBadge(t, { long: true })}</div>
      ${t.prediction_status === 'ok' && ['waiting', 'called'].includes(t.status) ? `<div class="grid g-3">
        <div class="callout"><div class="c-label">Dự kiến được khám</div><div class="c-value" style="font-size:26px">${hhmm(t.predicted_exam_time)}</div></div>
        <div class="callout"><div class="c-label">Còn khoảng</div><div class="c-value" style="font-size:26px">${t.remaining_minutes > 0 ? `${Math.round(t.remaining_minutes)} phút` : 'Sắp tới lượt'}</div></div>
        <div class="callout"><div class="c-label">Đã chờ</div><div class="c-value" style="font-size:26px">${Math.round(t.waited_minutes)} phút</div></div></div>` : ''}
    </div>`).join('') : `<div class="card">${emptyState('Không tìm thấy lượt khám. Vui lòng liên hệ quầy tiếp đón.', 'search')}</div>`;
  };
  $('#go').addEventListener('click', search);
  $('#q').addEventListener('keydown', (e) => { if (e.key === 'Enter') search(); });
  $('#pad').addEventListener('click', (e) => {
    const b = e.target.closest('[data-d]'); if (!b) return;
    const q = $('#q'); q.value = b.dataset.d === 'del' ? q.value.slice(0, -1) : q.value + b.dataset.d; q.focus();
  });
  return stopClock;
}
