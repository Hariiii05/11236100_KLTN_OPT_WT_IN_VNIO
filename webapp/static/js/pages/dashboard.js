import { icon } from '../icons.js';
import { api, esc, n0, n1, n2, pct, hhmm, ddmm, minutes, selectOptions, emptyState, sourceBadge } from '../ui.js';
import { chartCard, bindToggles, mount, disposeAll, bar, lines, catAxis, valAxis, SERIES, SEQ } from '../charts.js';

const TABS = [
  ['overview', 'Tổng quan', 'dashboard'], ['time', 'Theo thời gian', 'clock'], ['rooms', 'Theo phòng khám', 'building'],
  ['forecast', 'Dự báo & thực tế', 'sparkles'], ['live', 'Vận hành & cảnh báo', 'bell'],
];
const KEY = 'optwt.dashboard';

const kpi = (label, value, { unit = '', foot = '', ic = 'activity', cls = '' } = {}) => `<div class="card kpi">
  <span class="kpi-icon ${cls}">${icon(ic)}</span><div class="kpi-label">${label}</div>
  <div class="kpi-value">${value}${unit ? `<small>${unit}</small>` : ''}</div><div class="kpi-foot">${foot}</div></div>`;

export async function render(root, _params, ctx) {
  const opts = ctx.meta.dashboard;
  const cat = ctx.meta.catalog;
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(KEY)) || {}; } catch { /* ignore */ }
  const f = { period: 'all', start: opts.date_min, end: opts.date_max, ...saved.filters, threshold: 30, tolerance: 10 };
  delete f.service; delete f.doctor; delete f.shift;
  let tab = saved.tab || 'overview';
  let data = null;
  let live = null;
  let liveTimer = null;

  const periods = [{ value: 'all', label: `Toàn bộ (${ddmm(opts.date_min).slice(0, 5)}–${ddmm(opts.date_max).slice(0, 5)})` },
    { value: '30', label: '30 ngày gần nhất' }, { value: '7', label: '7 ngày gần nhất' },
    ...(opts.holdout_min ? [{ value: 'holdout', label: `Giai đoạn đánh giá (${ddmm(opts.holdout_min).slice(0, 5)}–${ddmm(opts.holdout_max).slice(0, 5)})` }] : []),
    { value: 'custom', label: 'Tùy chọn' }];
  root.innerHTML = `<div class="container">
    <div class="page-title"><h1>Dashboard</h1><span class="muted" id="dataScope"></span></div>
    <div class="card" style="margin-bottom:12px"><div class="card-body tight"><div class="filters" id="filters">
      <div class="f" style="width:215px"><label>Thời gian</label><select class="in" data-f="period">${selectOptions(periods, f.period || 'all')}</select></div>
      <div class="f date"><label>Từ ngày</label><input class="in" type="date" data-f="start" min="${opts.date_min}" max="${opts.date_max}"></div>
      <div class="f date"><label>Đến ngày</label><input class="in" type="date" data-f="end" min="${opts.date_min}" max="${opts.date_max}"></div>
      <div class="f" style="width:210px"><label>Chuyên khoa</label><select class="in" data-f="department">${selectOptions(cat.departments.map((d) => ({ value: d.ma, label: d.ten })), '', 'Tất cả')}</select></div>
      <div class="f" style="width:240px"><label>Phòng khám</label><select class="in" data-f="room"></select></div>
      <div class="f" style="width:140px"><label>Đối tượng</label><select class="in" data-f="payment">${selectOptions([{ value: 'bhyt', label: 'BHYT' }, { value: 'dich_vu', label: 'Dịch vụ' }], '', 'Tất cả')}</select></div>
      <button class="btn btn-sm" id="reset" title="Đặt lại bộ lọc" style="height:30px">${icon('refresh')}Đặt lại</button>
    </div></div></div>
    <div class="tabs" id="tabs">${TABS.map(([k, l, ic]) => `<button data-tab="${k}">${icon(ic)}${l}</button>`).join('')}</div>
    <div id="content"></div>
  </div>`;

  const $ = (s) => root.querySelector(s);
  const content = $('#content');
  const roomSelect = () => {
    const rooms = cat.rooms.filter((r) => r.in_model && (!f.department || r.ma_khoa === f.department));
    $('[data-f="room"]').innerHTML = selectOptions(rooms.map((r) => ({ value: r.ma_phong, label: `${r.ma_phong} – ${r.ten_phong}` })), f.room, 'Tất cả');
  };
  const syncInputs = () => {
    roomSelect();
    root.querySelectorAll('[data-f]').forEach((el) => { el.value = f[el.dataset.f] ?? ''; });
  };
  const persist = () => localStorage.setItem(KEY, JSON.stringify({ filters: f, tab }));
  const query = () => new URLSearchParams(Object.entries(f).filter(([k, v]) => k !== 'period' && v !== '' && v != null)).toString();

  async function load() {
    persist();
    content.classList.add('loading');
    try {
      data = await api(`/api/dashboard?${query()}`);
      $('#dataScope').textContent = data.rows ? `${n0(data.rows)} lượt khám trong phạm vi lọc` : 'Không có dữ liệu';
      renderTab();
    } catch (e) { content.innerHTML = `<div class="alert critical">${icon('alertCircle')}<div>${esc(e.message)}</div></div>`; }
    finally { content.classList.remove('loading'); }
  }

  function renderTab() {
    root.querySelectorAll('#tabs button').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
    disposeAll();
    clearInterval(liveTimer); liveTimer = null;
    if (tab === 'live') { renderLive(); liveTimer = setInterval(renderLive, 15000); return; }
    if (!data || data.empty) { content.innerHTML = `<div class="card">${emptyState('Không có lượt khám nào khớp bộ lọc', 'filter')}</div>`; return; }
    ({ overview: renderOverview, time: renderTime, rooms: renderRooms, forecast: renderForecast })[tab]();
    bindToggles(content);
  }

  // ---------------------------------------------------------------- overview
  function renderOverview() {
    const o = data.overview;
    content.innerHTML = `
      <div class="grid g-5">
        ${kpi('Tổng lượt khám', n0(o.services), { ic: 'users', foot: `${n0(o.encounters)} lượt tiếp đón · ${n0(o.days)} ngày` })}
        ${kpi('Trung bình mỗi ngày', n0(o.per_day), { unit: 'lượt', ic: 'calendar', foot: `${n0(o.rooms)} phòng khám hoạt động` })}
        ${kpi('Thời gian chờ trung bình', n1(o.mean), { unit: 'phút', ic: 'clock', cls: 'accent', foot: `Trung vị ${n1(o.median)} phút` })}
        ${kpi('Thời gian chờ P80 · P90', `${n0(o.p80)} · ${n0(o.p90)}`, { unit: 'phút', ic: 'hourglass', cls: 'warn', foot: '80% / 90% người bệnh chờ không quá'})}
        ${kpi(`Tỷ lệ chờ &gt; ${data.threshold} phút`, pct(o.over), { ic: 'alert', cls: 'crit', foot: `Ưu tiên ${pct(o.priority_share)} · BHYT ${pct(o.bhyt_share)}` })}
      </div>
      <div class="card" style="margin-top:16px"><div class="card-body tight row wrap" id="nowStrip"><span class="spinner"></span><span class="muted">Đang tải dữ liệu vận hành…</span></div></div>
      <div class="grid g-2" style="margin-top:16px">
        ${chartCard('cDist', 'Phân bố thời gian chờ', { sub: '% lượt khám' })}
        ${chartCard('cDept', 'Thời gian chờ trung vị theo chuyên khoa', { sub: 'phút' })}
        ${chartCard('cShift', 'Thời gian chờ theo ca khám', { sub: 'Trung vị (phút)' })}
        ${chartCard('cGroup', 'Theo dịch vụ khám', { sub: 'Trung vị (phút)' })}
      </div>`;
    mount('cDist', bar({ categories: data.distribution.map((d) => d.bin), values: data.distribution.map((d) => d.share), name: 'Tỷ lệ', unit: '%' }),
      { columns: ['Khoảng chờ (phút)', 'Số lượt', 'Tỷ lệ (%)'], rows: data.distribution.map((d) => [d.bin, d.n, d.share]) });
    const dep = data.departments.filter((d) => d.n >= 20).sort((a, b) => (b.median ?? 0) - (a.median ?? 0));
    mount('cDept', bar({ categories: dep.map((d) => d.ten), values: dep.map((d) => d.median), name: 'Trung vị', unit: 'phút', horizontal: true }),
      { columns: ['Chuyên khoa', 'Lượt', 'TB', 'Trung vị', 'P90', `% > ${data.threshold}′`], rows: dep.map((d) => [d.ten, d.n, d.mean, d.median, d.p90, d.over]) });
    mount('cShift', bar({ categories: data.shifts.map((s) => s.ten), values: data.shifts.map((s) => s.median), name: 'Trung vị', unit: 'phút' }),
      { columns: ['Ca', 'Lượt', 'TB', 'Trung vị', 'P90'], rows: data.shifts.map((s) => [s.ten, s.n, s.mean, s.median, s.p90]) });
    const sv = [...data.services].sort((a, b) => b.n - a.n);
    const svOpt = bar({ categories: sv.map((s) => `${s.ten}`), values: sv.map((s) => s.median), name: 'Trung vị', unit: 'phút', horizontal: true });
    svOpt.series[0].label = { show: true, position: 'right', color: '#52514e', fontSize: 11, formatter: (p) => `${n0(sv[p.dataIndex].n)} lượt` };
    svOpt.grid = { right: 80 };
    mount('cGroup', svOpt, { columns: ['Dịch vụ', 'Lượt', 'TB', 'Trung vị', 'P90'], rows: sv.map((s) => [s.ten, s.n, s.mean, s.median, s.p90]).concat(
      data.payment.map((p) => [`Đối tượng: ${p.ten}`, p.n, p.mean, p.median, p.p90])) });
    api(`/api/dashboard/live?${new URLSearchParams(Object.entries({ department: f.department, room: f.room }).filter(([, v]) => v))}`).then((l) => {
      const k = l.kpi; const el = root.querySelector('#nowStrip'); if (!el) return;
      el.innerHTML = `<span class="badge web" style="font-size:11px">${icon('activity')}Vận hành hiện tại · ${hhmm(l.now.slice(0, 19))} ${ddmm(l.now.slice(0, 10))}</span>
        <span>Vào hàng đợi hôm nay <b>${n0(k.arrivals_today)}</b></span><span class="muted">·</span><span>Đang chờ <b>${n0(k.waiting)}</b></span><span class="muted">·</span>
        <span>Đang khám <b>${n0(k.in_exam)}</b></span><span class="muted">·</span><span>Đã khám <b>${n0(k.done)}</b></span><span class="muted">·</span>
        <span>Chờ thực tế TB hôm nay <b>${k.avg_actual_wait_today == null ? '–' : `${n1(k.avg_actual_wait_today)} phút`}</b></span><span class="muted">·</span>
        <span>Cảnh báo <b style="color:${l.alerts.length ? 'var(--critical-ink)' : 'inherit'}">${l.alerts.length}</b></span>
        <button class="btn btn-sm" style="margin-left:auto" id="goLive">${icon('bell')}Xem cảnh báo</button>`;
      el.querySelector('#goLive').addEventListener('click', () => { tab = 'live'; persist(); renderTab(); });
    }).catch(() => {});
  }

  // ---------------------------------------------------------------- time
  function renderTime() {
    const hours = data.by_hour.filter((h) => h.n >= 20);
    const busiest = [...hours].sort((a, b) => b.per_day - a.per_day)[0];
    const slowest = [...hours].filter((h) => h.per_day >= 5).sort((a, b) => (b.median ?? 0) - (a.median ?? 0))[0];
    const wd = [...data.by_weekday].sort((a, b) => b.per_day - a.per_day)[0];
    const peak = [...hours].sort((a, b) => b.per_day - a.per_day).slice(0, 3).map((h) => `${String(h.hour).padStart(2, '0')}h`).join(', ');
    content.innerHTML = `
      <div class="callouts" style="margin-bottom:16px">
        <div class="callout"><div class="c-label">Khung giờ đông nhất</div><div class="c-value">${busiest ? `${String(busiest.hour).padStart(2, '0')}:00–${String(busiest.hour).padStart(2, '0')}:59` : '–'}</div>
          <div class="c-sub">${busiest ? `${n0(busiest.per_day)} lượt/ngày · trung vị ${n1(busiest.median)} phút` : ''} · Cao điểm: ${peak}</div></div>
        <div class="callout"><div class="c-label">Khung giờ chờ lâu nhất (≥5 lượt/ngày)</div><div class="c-value">${slowest ? `${String(slowest.hour).padStart(2, '0')}:00` : '–'}</div>
          <div class="c-sub">${slowest ? `Trung vị ${n1(slowest.median)} phút · P90 ${n0(slowest.p90)} phút · ${pct(slowest.over)} vượt ngưỡng` : ''}</div></div>
        <div class="callout"><div class="c-label">Ngày trong tuần đông nhất</div><div class="c-value">${wd ? wd.label : '–'}</div>
          <div class="c-sub">${wd ? `${n0(wd.per_day)} lượt/ngày · trung vị ${n1(wd.median)} phút` : ''}</div></div>
      </div>
      <div class="grid g-2">
        ${chartCard('cHourN', 'Số lượt vào hàng đợi theo giờ', { sub: 'Trung bình lượt/ngày' })}
        ${chartCard('cHourW', 'Thời gian chờ theo giờ vào hàng đợi', { sub: 'Trung vị và P90 (phút)' })}
      </div>
      <div style="margin-top:16px">${chartCard('cHeat', 'Bản đồ nhiệt: thời gian chờ trung vị theo thứ × giờ', { sub: 'phút', cls: 'tall' })}</div>
      <div class="grid g-2" style="margin-top:16px">
        ${chartCard('cDayN', 'Số lượt khám theo ngày', { sub: 'lượt' })}
        ${chartCard('cDayW', 'Xu hướng thời gian chờ theo ngày', { sub: 'Trung vị và P90 (phút)' })}
        ${chartCard('cWdN', 'So sánh ngày trong tuần – lưu lượng', { sub: 'Trung bình lượt/ngày', cls: 'short' })}
        ${chartCard('cWdW', 'So sánh ngày trong tuần – thời gian chờ', { sub: 'Trung vị (phút)', cls: 'short' })}
      </div>`;
    const hx = data.by_hour.map((h) => `${String(h.hour).padStart(2, '0')}h`);
    mount('cHourN', bar({ categories: hx, values: data.by_hour.map((h) => h.per_day), name: 'Lượt/ngày' }),
      { columns: ['Giờ', 'Lượt/ngày', 'Tổng lượt'], rows: data.by_hour.map((h) => [`${h.hour}h`, h.per_day, h.n]) });
    mount('cHourW', lines({ x: hx, unit: 'phút', series: [{ name: 'Trung vị', data: data.by_hour.map((h) => h.median) }, { name: 'P90', data: data.by_hour.map((h) => h.p90), color: SERIES[1] }] }),
      { columns: ['Giờ', 'Lượt', 'TB', 'Trung vị', 'P90', `% > ${data.threshold}′`], rows: data.by_hour.map((h) => [`${h.hour}h`, h.n, h.mean, h.median, h.p90, h.over]) });
    const hm = data.heatmap; const hs = [...new Set(hm.cells.map((c) => c[0]))].sort((a, b) => a - b);
    const hIndex = new Map(hs.map((h, i) => [h, i]));
    const maxV = Math.max(...hm.cells.map((c) => c[2]), 1);
    mount('cHeat', {
      tooltip: { trigger: 'item', formatter: (p) => `${hm.weekdays[p.value[1]]}, ${String(hs[p.value[0]]).padStart(2, '0')}:00<br>Trung vị <b>${n1(p.value[2])} phút</b><br>${n0(p.value[3])} lượt` },
      grid: { left: 12, right: 18, top: 10, bottom: 50, containLabel: true },
      xAxis: catAxis(hs.map((h) => `${String(h).padStart(2, '0')}h`), { splitArea: { show: false } }),
      yAxis: catAxis(hm.weekdays, { inverse: true }),
      visualMap: { dimension: 2, min: 0, max: Math.ceil(maxV / 10) * 10, calculable: false, orient: 'horizontal', left: 'center', bottom: 0, itemWidth: 12, itemHeight: 200,
        inRange: { color: SEQ }, text: [`${Math.ceil(maxV / 10) * 10} phút`, '0'], textStyle: { color: '#898781', fontSize: 11 } },
      series: [{ type: 'heatmap', data: hm.cells.map((c) => [hIndex.get(c[0]), c[1], c[2], c[3]]), itemStyle: { borderColor: '#fff', borderWidth: 2, borderRadius: 4 },
        emphasis: { itemStyle: { borderColor: '#0f1b2d', borderWidth: 1 } } }],
    }, { columns: ['Thứ', 'Giờ', 'Trung vị (phút)', 'Lượt'], rows: hm.cells.map((c) => [hm.weekdays[c[1]], `${c[0]}h`, c[2], c[3]]) });
    const dx = data.daily.map((d) => ddmm(d.date).slice(0, 5));
    mount('cDayN', bar({ categories: dx, values: data.daily.map((d) => d.n), name: 'Lượt khám' }),
      { columns: ['Ngày', 'Lượt'], rows: data.daily.map((d) => [ddmm(d.date), d.n]) });
    mount('cDayW', lines({ x: dx, unit: 'phút', series: [{ name: 'Trung vị', data: data.daily.map((d) => d.median) }, { name: 'P90', data: data.daily.map((d) => d.p90), color: SERIES[1] }] }),
      { columns: ['Ngày', 'Lượt', 'TB', 'Trung vị', 'P90', `% > ${data.threshold}′`], rows: data.daily.map((d) => [ddmm(d.date), d.n, d.mean, d.median, d.p90, d.over]) });
    mount('cWdN', bar({ categories: data.by_weekday.map((w) => w.label), values: data.by_weekday.map((w) => w.per_day), name: 'Lượt/ngày' }),
      { columns: ['Thứ', 'Lượt/ngày', 'Tổng lượt'], rows: data.by_weekday.map((w) => [w.label, w.per_day, w.n]) });
    mount('cWdW', bar({ categories: data.by_weekday.map((w) => w.label), values: data.by_weekday.map((w) => w.median), name: 'Trung vị', unit: 'phút' }),
      { columns: ['Thứ', 'TB', 'Trung vị', 'P90', `% > ${data.threshold}′`], rows: data.by_weekday.map((w) => [w.label, w.mean, w.median, w.p90, w.over]) });
  }

  // ---------------------------------------------------------------- rooms
  let roomSort = { key: 'n', dir: -1 };
  function renderRooms() {
    const rooms = data.rooms;
    const ranked = rooms.filter((r) => r.n >= 50).sort((a, b) => b.median - a.median).slice(0, 15);
    content.innerHTML = `
      <div class="grid g-2">
        ${chartCard('cRoomTop', 'Top 15 phòng chờ lâu nhất', { sub: 'Trung vị (phút)', cls: 'tall' })}
        ${chartCard('cRoomScatter', 'Lưu lượng so với thời gian chờ', { sub: 'Mỗi điểm là một phòng', cls: 'tall' })}
      </div>
      <div class="card" style="margin-top:16px"><div class="card-header"><h3>${icon('building')}Chỉ số theo phòng khám</h3><span class="sub">${rooms.length} phòng · bấm tiêu đề cột để sắp xếp</span></div>
        <div class="table-wrap" style="max-height:560px"><table class="table" id="roomTbl"></table></div></div>`;
    mount('cRoomTop', bar({ categories: ranked.map((r) => r.ten_phong), values: ranked.map((r) => r.median), name: 'Trung vị', unit: 'phút', horizontal: true }),
      { columns: ['Phòng', 'Lượt', 'Trung vị', 'P90'], rows: ranked.map((r) => [`${r.ma_phong} – ${r.ten_phong}`, r.n, r.median, r.p90]) });
    mount('cRoomScatter', {
      tooltip: { trigger: 'item', formatter: (p) => `<b>${esc(p.data.name)}</b><br>${n0(p.data.value[0])} lượt · trung vị ${n1(p.data.value[1])} phút` },
      xAxis: valAxis('Số lượt khám', { nameLocation: 'middle', nameGap: 26 }), yAxis: valAxis('phút'),
      grid: { bottom: 26 },
      series: [{ type: 'scatter', symbolSize: 10, itemStyle: { color: SERIES[0], opacity: 0.8, borderColor: '#fff', borderWidth: 2 },
        data: rooms.filter((r) => r.n >= 20).map((r) => ({ name: `${r.ma_phong} – ${r.ten_phong}`, value: [r.n, r.median] })) }],
    }, { columns: ['Phòng', 'Lượt', 'Trung vị (phút)'], rows: rooms.filter((r) => r.n >= 20).map((r) => [`${r.ma_phong} – ${r.ten_phong}`, r.n, r.median]) });
    const drawTable = () => {
      const cols = [['ma_phong', 'Phòng'], ['ten_khoa', 'Chuyên khoa'], ['n', 'Lượt'], ['per_hour', 'Công suất (lượt/giờ)'], ['median_exam_minutes', 'TG khám TV'],
        ['mean', 'Chờ TB'], ['median', 'Trung vị'], ['p90', 'P90'], ['over', `% > ${data.threshold}′`]];
      const sorted = [...rooms].sort((a, b) => ((a[roomSort.key] ?? -1) > (b[roomSort.key] ?? -1) ? 1 : -1) * roomSort.dir);
      const maxOver = Math.max(...rooms.map((r) => r.over || 0), 1);
      root.querySelector('#roomTbl').innerHTML = `<thead><tr>${cols.map(([k, l], i) => `<th class="sortable ${i > 1 ? 'num' : ''}" data-k="${k}">${l}${roomSort.key === k ? (roomSort.dir > 0 ? ' ▲' : ' ▼') : ''}</th>`).join('')}</tr></thead>
        <tbody>${sorted.map((r) => `<tr><td><div class="cell-title">${esc(r.ten_phong)}</div><div class="cell-sub">${esc(r.ma_phong)}</div></td><td class="text-2">${esc(r.ten_khoa)}</td>
          <td class="num">${n0(r.n)}</td><td class="num">${n1(r.per_hour)}</td><td class="num">${n1(r.median_exam_minutes)}</td><td class="num">${n1(r.mean)}</td>
          <td class="num"><b>${n1(r.median)}</b></td><td class="num">${n1(r.p90)}</td>
          <td class="num"><div class="row" style="justify-content:flex-end;gap:8px"><div class="bar-inline" style="width:70px"><i style="width:${(100 * (r.over || 0)) / maxOver}%"></i></div>${pct(r.over)}</div></td></tr>`).join('')}</tbody>`;
      root.querySelectorAll('#roomTbl th').forEach((th) => th.addEventListener('click', () => {
        roomSort = { key: th.dataset.k, dir: roomSort.key === th.dataset.k ? -roomSort.dir : -1 }; drawTable();
      }));
    };
    drawTable();
  }

  // ---------------------------------------------------------------- forecast
  function renderForecast() {
    const fc = data.forecast;
    if (!fc.available) {
      content.innerHTML = `<div class="alert info">${icon('info')}<div><div class="alert-title">Không có dữ liệu dự báo trong khoảng thời gian đã chọn</div>
        <div style="margin-top:8px"><button class="btn btn-sm" id="toHoldout">Chọn giai đoạn đánh giá</button></div></div></div><div id="webFc" style="margin-top:16px"></div>`;
      content.querySelector('#toHoldout')?.addEventListener('click', () => applyPreset('holdout'));
      renderWebForecast();
      return;
    }
    const tol = f.tolerance || 10;
    content.innerHTML = `
      <div class="grid g-5">
        ${kpi('MAE', n2(fc.mae), { unit: 'phút', ic: 'activity', cls: 'accent', foot: `Median AE ${n2(fc.median_ae)} phút` })}
        ${kpi('RMSE', n2(fc.rmse), { unit: 'phút', ic: 'chart', foot: `Bias ${fc.bias > 0 ? '+' : ''}${n2(fc.bias)} phút` })}
        ${kpi(`Trong khoảng ±${tol} phút`, pct(fc.within_tolerance), { ic: 'checkCircle', cls: 'accent', foot: `±20 phút: ${pct(fc.within_20)}` })}
        ${kpi('Coverage khoảng P80', pct(fc.coverage_p80), { ic: 'shield', foot: 'Tỷ lệ thực tế ≤ cận trên P80' })}
        ${kpi('Cảnh báo quá giờ trưa', fc.lunch ? `${n2(fc.lunch.precision)} · ${n2(fc.lunch.recall)}` : '–', { ic: 'bell', cls: 'warn', foot: 'Precision · Recall' })}
      </div>
      <div class="grid g-2" style="margin-top:16px">
        ${chartCard('cFcDaily', 'Thời gian chờ thực tế và dự báo theo ngày', { sub: 'Trung bình (phút)' })}
        ${chartCard('cFcMae', 'Sai số tuyệt đối trung bình (MAE) theo ngày', { sub: 'phút' })}
        ${chartCard('cFcCal', 'Độ hiệu chỉnh dự báo', { sub: 'phút' })}
        ${chartCard('cFcErr', 'Phân bố sai số (dự báo − thực tế)', { sub: 'phút' })}
        ${chartCard('cFcDept', 'MAE theo chuyên khoa', { sub: 'phút' })}
        ${chartCard('cFcHour', 'MAE theo giờ vào hàng đợi', { sub: 'phút' })}
      </div>
      <div id="webFc" style="margin-top:16px"></div>`;
    const dx = fc.daily.map((d) => ddmm(d.date).slice(0, 5));
    mount('cFcDaily', lines({ x: dx, unit: 'phút', series: [{ name: 'Thực tế', data: fc.daily.map((d) => d.actual) }, { name: 'Dự báo (P50)', data: fc.daily.map((d) => d.predicted), color: SERIES[1] }] }),
      { columns: ['Ngày', 'Lượt', 'Thực tế TB', 'Dự báo TB', 'MAE'], rows: fc.daily.map((d) => [ddmm(d.date), d.n, d.actual, d.predicted, d.mae]) });
    mount('cFcMae', bar({ categories: dx, values: fc.daily.map((d) => d.mae), name: 'MAE', unit: 'phút' }),
      { columns: ['Ngày', 'Lượt', 'MAE'], rows: fc.daily.map((d) => [ddmm(d.date), d.n, d.mae]) });
    const maxC = Math.ceil(Math.max(...fc.calibration.map((c) => Math.max(c.actual, c.predicted))) / 10) * 10;
    mount('cFcCal', {
      tooltip: { trigger: 'item', formatter: (p) => (p.seriesType === 'scatter' ? `Dự báo TB <b>${n1(p.value[0])}</b> phút<br>Thực tế TB <b>${n1(p.value[1])}</b> phút<br>${n0(p.value[2])} lượt` : '') },
      xAxis: valAxis('Dự báo (phút)', { max: maxC, nameLocation: 'middle', nameGap: 26 }), yAxis: valAxis('Thực tế', { max: maxC }),
      grid: { bottom: 26 },
      series: [
        { type: 'line', data: [[0, 0], [maxC, maxC]], showSymbol: false, lineStyle: { color: '#c3c2b7', width: 1 }, silent: true, tooltip: { show: false } },
        { type: 'scatter', symbolSize: 11, data: fc.calibration.map((c) => [c.predicted, c.actual, c.n]), itemStyle: { color: SERIES[0], borderColor: '#fff', borderWidth: 2 } },
      ],
    }, { columns: ['Nhóm', 'Dự báo TB', 'Thực tế TB', 'Thực tế P80', 'Lượt'], rows: fc.calibration.map((c, i) => [`Thập phân vị ${i + 1}`, c.predicted, c.actual, c.actual_p80, c.n]) });
    mount('cFcErr', bar({ categories: fc.error_hist.map((e) => e.bin), values: fc.error_hist.map((e) => e.n), name: 'Số lượt' }),
      { columns: ['Sai số (phút)', 'Lượt'], rows: fc.error_hist.map((e) => [e.bin, e.n]) });
    const dep = [...fc.by_department].sort((a, b) => b.mae - a.mae);
    mount('cFcDept', bar({ categories: dep.map((d) => d.ten), values: dep.map((d) => d.mae), name: 'MAE', unit: 'phút', horizontal: true }),
      { columns: ['Chuyên khoa', 'Lượt', 'MAE', 'Bias'], rows: dep.map((d) => [d.ten, d.n, d.mae, d.bias]) });
    mount('cFcHour', bar({ categories: fc.by_hour.map((h) => `${String(h.hour).padStart(2, '0')}h`), values: fc.by_hour.map((h) => h.mae), name: 'MAE', unit: 'phút' }),
      { columns: ['Giờ', 'Lượt', 'MAE', 'Thực tế TB', 'Dự báo TB'], rows: fc.by_hour.map((h) => [`${h.hour}h`, h.n, h.mae, h.actual, h.predicted]) });
    renderWebForecast();
  }

  async function renderWebForecast() {
    const box = content.querySelector('#webFc'); if (!box) return;
    const l = await api('/api/dashboard/live');
    const w = l.web_forecast;
    box.innerHTML = `<div class="card"><div class="card-header"><h3>${icon('reception')}Theo dõi trong vận hành – phiếu tiếp đón trên hệ thống</h3>
      <span class="sub">${w.n ? `${w.n} lượt đã bắt đầu khám · MAE ${n2(w.mae)} phút · trong ±10 phút ${pct(w.within_10)}` : 'Chưa có lượt nào hoàn tất'}</span></div>
      <div class="table-wrap" style="max-height:320px"><table class="table"><thead><tr><th>Phiếu</th><th>Phòng</th><th>Vào hàng đợi</th><th class="num">Dự báo P50</th><th class="num">P80</th><th class="num">Thực tế</th><th class="num">Sai số</th></tr></thead>
      <tbody>${w.rows.map((r) => `<tr><td><b>${r.display_stt}</b> <span class="cell-sub">${esc(r.id)}</span></td><td>${esc(r.room)}</td><td>${hhmm(r.queue_entry_time)} ${ddmm(r.queue_entry_time).slice(0, 5)}</td>
        <td class="num">${n1(r.predicted)}</td><td class="num">${n1(r.p80)}</td><td class="num"><b>${n1(r.actual)}</b></td>
        <td class="num" style="color:${Math.abs(r.error) <= 10 ? 'var(--good-ink)' : 'var(--critical-ink)'}">${r.error > 0 ? '+' : ''}${n1(r.error)}</td></tr>`).join('')
        || `<tr><td colspan="7">${emptyState('Tiếp đón người bệnh và cập nhật trạng thái ở QMS để ghi nhận thời gian chờ thực tế', 'reception')}</td></tr>`}</tbody></table></div></div>`;
  }

  // ---------------------------------------------------------------- live
  async function renderLive() {
    const q = new URLSearchParams(Object.entries({ department: f.department, room: f.room }).filter(([, v]) => v));
    try { live = await api(`/api/dashboard/live?${q}`); } catch (e) { content.innerHTML = `<div class="alert critical">${icon('alertCircle')}${esc(e.message)}</div>`; return; }
    const k = live.kpi;
    const levelLabel = { high: 'Cao', medium: 'Trung bình', info: 'Thông tin' };
    const levelIcon = { high: 'alertCircle', medium: 'alert', info: 'info' };
    content.innerHTML = `
      <div class="row" style="margin-bottom:12px"><span class="badge web">${icon('activity')}Thời điểm ${hhmm(live.now.slice(0, 19))} · ${ddmm(live.now.slice(0, 10))}</span></div>
      <div class="grid g-5">
        ${kpi('Lượt vào hàng đợi hôm nay', n0(k.arrivals_today), { ic: 'users' })}
        ${kpi('Đang chờ', n0(k.waiting), { ic: 'hourglass', cls: 'warn', foot: `${n0(k.active_rooms)} phòng đang hoạt động` })}
        ${kpi('Đang khám', n0(k.in_exam), { ic: 'stethoscope', cls: 'accent' })}
        ${kpi('Đã khám xong', n0(k.done), { ic: 'checkCircle', cls: 'accent' })}
        ${kpi('Chờ thực tế TB hôm nay', k.avg_actual_wait_today == null ? '–' : n1(k.avg_actual_wait_today), { unit: 'phút', ic: 'clock', foot: 'Các lượt đã bắt đầu khám' })}
      </div>
      <div class="grid" style="grid-template-columns:minmax(0,1fr) minmax(0,1.4fr);margin-top:16px;align-items:start">
        <div class="card"><div class="card-header"><h3>${icon('bell')}Cảnh báo vận hành</h3><span class="badge ${live.alerts.some((a) => a.level === 'high') ? 'skipped' : 'waiting'}" style="margin-left:auto">${live.alerts.length} cảnh báo</span></div>
          <div class="card-body tight" style="max-height:560px;overflow:auto"><div class="alert-list">${live.alerts.map((a) => `<div class="alert-item ${a.level}">
            <span class="ai">${icon(levelIcon[a.level])}</span><div><div class="at">${esc(a.type)} <span class="level ${a.level}">${levelLabel[a.level]}</span></div><div class="am">${esc(a.message)}</div>
            ${a.room ? `<div class="row" style="margin-top:6px;gap:6px"><a class="btn btn-sm" href="#/qms/dieu-phoi/${encodeURIComponent(a.room)}">${icon('megaphone')}Điều phối</a></div>` : ''}</div></div>`).join('')
            || `<div class="alert good">${icon('checkCircle')}<div>Không có cảnh báo – vận hành trong ngưỡng.</div></div>`}</div></div></div>
        <div class="card"><div class="card-header"><h3>${icon('building')}Tải các phòng khám hiện tại</h3><span class="sub">Sắp xếp theo số người đang chờ</span></div>
          <div class="table-wrap" style="max-height:560px"><table class="table"><thead><tr><th>Phòng</th><th class="num">Đang chờ</th><th class="num">Đang khám</th>
            <th class="num">Vào / khám (30′)</th><th class="num">Chờ dự kiến</th><th class="num">Chờ lâu nhất</th><th class="num">Chờ TT hôm nay</th></tr></thead>
          <tbody>${live.rooms.map((r) => `<tr><td style="max-width:230px"><a href="#/qms/dieu-phoi/${encodeURIComponent(r.ma_phong)}" class="cell-title">${esc(r.ma_phong)}</a><div class="cell-sub" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.ten_phong)}</div></td>
            <td class="num"><b style="color:${r.waiting > live.thresholds.queue ? 'var(--critical-ink)' : 'inherit'}">${n0(r.waiting)}</b></td><td class="num">${n0(r.in_exam)}</td>
            <td class="num">${n0(r.arrivals_30m)} / ${n0(r.starts_30m)}</td>
            <td class="num" style="color:${r.median_predicted_wait > live.thresholds.wait ? 'var(--critical-ink)' : 'inherit'}">${r.median_predicted_wait == null ? '–' : `${n0(r.median_predicted_wait)}′`}</td>
            <td class="num">${r.max_waited == null ? '–' : `${n0(r.max_waited)}′`}</td><td class="num">${r.avg_actual_wait_today == null ? '–' : `${n0(r.avg_actual_wait_today)}′`}</td></tr>`).join('')
            || `<tr><td colspan="7">${emptyState('Không có phòng nào đang hoạt động', 'building')}</td></tr>`}</tbody></table></div></div>
      </div>
      <div class="card" style="margin-top:16px"><div class="card-header"><h3>${icon('alert')}Người bệnh chờ vượt cận trên dự báo P80</h3><span class="sub">Đã chờ lâu hơn P80 quá ${live.thresholds.overdue} phút</span></div>
        <div class="table-wrap" style="max-height:360px"><table class="table"><thead><tr><th>STT</th><th>Phòng</th><th>Nguồn</th><th>Vào hàng đợi</th><th class="num">Đã chờ</th><th class="num">Dự báo P50 · P80</th><th class="num">Vượt P80</th></tr></thead>
        <tbody>${live.overdue.map((t) => `<tr><td><b>${t.display_stt}</b></td><td>${esc(t.room.ma_phong)} – ${esc(t.room.ten_phong)}</td><td>${sourceBadge(t.source)}</td><td>${hhmm(t.queue_entry_time)}</td>
          <td class="num"><b>${minutes(t.waited_minutes)}</b></td><td class="num">${n0(t.predicted_wait_minutes)}′ · ${n0(t.predicted_wait_p80_minutes)}′</td>
          <td class="num" style="color:var(--critical-ink);font-weight:700">+${n0(t.overdue_minutes)}′</td></tr>`).join('')
          || `<tr><td colspan="7">${emptyState('Không có người bệnh chờ vượt dự báo', 'checkCircle')}</td></tr>`}</tbody></table></div></div>`;
  }

  // ---------------------------------------------------------------- events
  function applyPreset(p) {
    f.period = p;
    if (p === 'all') { f.start = opts.date_min; f.end = opts.date_max; }
    else if (p === 'holdout') { f.start = opts.holdout_min; f.end = opts.holdout_max; }
    else if (p !== 'custom') { const end = new Date(opts.date_max); const start = new Date(end.getTime() - (Number(p) - 1) * 86400000); f.start = start.toISOString().slice(0, 10); f.end = opts.date_max; }
    if (p === 'holdout' && tab !== 'forecast') tab = 'forecast';
    syncInputs(); load();
  }
  root.querySelector('#filters').addEventListener('change', (e) => {
    const el = e.target.closest('[data-f]'); if (!el) return;
    if (el.dataset.f === 'period') { applyPreset(el.value); return; }
    f[el.dataset.f] = el.value;
    if (el.dataset.f === 'start' || el.dataset.f === 'end') f.period = 'custom';
    if (el.dataset.f === 'department') { f.room = ''; roomSelect(); }
    if (f.start && f.end && f.start > f.end) [f.start, f.end] = [f.end, f.start];
    syncInputs(); load();
  });
  root.querySelector('#reset').addEventListener('click', () => {
    Object.assign(f, { period: 'all', start: opts.date_min, end: opts.date_max, department: '', room: '', payment: '' });
    syncInputs(); load();
  });
  root.querySelector('#tabs').addEventListener('click', (e) => { const b = e.target.closest('[data-tab]'); if (!b) return; tab = b.dataset.tab; persist(); renderTab(); });
  const onClock = () => { if (tab === 'live') renderLive(); };
  window.addEventListener('clock-changed', onClock);

  syncInputs();
  await load();
  return () => { clearInterval(liveTimer); disposeAll(); window.removeEventListener('clock-changed', onClock); };
}
