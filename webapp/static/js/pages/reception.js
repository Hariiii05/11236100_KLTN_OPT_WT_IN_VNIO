// Quản lý tiếp đón – bám theo ảnh mẫu "1. Tiếp đón hành chính" và "1.2 Tiếp đón dịch vụ".
import { icon } from '../icons.js';
import {
  api, esc, n0, hhmm, ddmm, age, minutes, toast, modal, selectOptions, waitClass, emptyState, lunchBadge,
} from '../ui.js';

const KEY = 'optwt.reception';
const LOAI_DOI_TUONG = {
  dich_vu: ['Bệnh nhân tự yêu cầu (TYC)', 'Bệnh nhân thường', 'Người nước ngoài', 'Chương trình Ánh mắt trẻ thơ'],
  bhyt: ['BHYT', 'BHYT đúng tuyến có giấy chuyển', 'BHYT đúng tuyến có giấy hẹn', 'BHYT vượt tuyến',
    'BHYT đúng tuyến chuyên sâu', 'BHYT đúng tuyến cấp cứu', 'BHYT theo yêu cầu'],
};
const REQUIRED = {
  ho_ten: 'Nhập họ và tên', ngay_sinh: 'Nhập ngày sinh', gioi_tinh: 'Chọn giới tính', tinh_xa: 'Nhập Phường/Xã, Tỉnh/TP',
  doi_tuong: 'Chọn đối tượng', loai_doi_tuong: 'Chọn loại đối tượng',
};

const blank = () => ({ admin: { loai_kham: 'Khám mới', uu_tien: false, cap_cuu: false, dan_toc: 'Kinh', loai_giay_to: 'Căn cước' }, service: {}, adminValid: false });
function load() { try { return { ...blank(), ...JSON.parse(sessionStorage.getItem(KEY)) }; } catch { return blank(); } }
function save(state) { sessionStorage.setItem(KEY, JSON.stringify(state)); }

const f = (name, label, control, { req = false, cls = '', hint = '' } = {}) => `<div class="f ${req ? 'req' : ''} ${cls}" data-field="${name}">
  <label for="f_${name}">${label}${hint ? `<span class="hint-r">${hint}</span>` : ''}</label>${control}<span class="err"></span></div>`;
const inp = (name, value, attrs = '') => `<input class="in" id="f_${name}" name="${name}" value="${esc(value ?? '')}" ${attrs}>`;
const sel = (name, items, value, placeholder, attrs = '') => `<select class="in" id="f_${name}" name="${name}" ${attrs}>${selectOptions(items, value, placeholder)}</select>`;
const USER_SVG = '<svg class="icon" viewBox="0 0 24 24"><circle cx="12" cy="8" r="4"/><path d="M4 21v-1a8 8 0 0 1 16 0v1"/></svg>';

function setErr(root, name, message) {
  const el = root.querySelector(`[data-field="${name}"]`);
  if (!el) return;
  el.classList.toggle('invalid', !!message);
  el.querySelector('.err').textContent = message || '';
}

export async function render(root, params, ctx) {
  return params.step === 2 ? renderService(root, ctx) : renderAdmin(root, ctx);
}

// ============================================================== 1. Thông tin hành chính
function renderAdmin(root, ctx) {
  const state = load();
  const a = state.admin;
  const isBhyt = a.doi_tuong === 'bhyt';
  root.innerHTML = `
  <div class="container">
    <div class="card"><div class="card-body tight"><div class="recep-top">
      <div class="avatar-box">${USER_SVG}<span class="cam">${icon('camera')}</span></div>
      <div class="stack" style="gap:8px">
        ${f('search', 'Mã định danh', `<div class="in-icon" style="position:relative"><input class="in" id="search" placeholder="Nhập mã NB, SĐT, CCCD hoặc họ tên" autocomplete="off">${icon('qr')}<div class="dropdown" id="searchResults" hidden></div></div>`, { hint: 'Tìm người bệnh đã tiếp đón' })}
        ${f('ly_do_kham', 'Lý do đến khám', inp('ly_do_kham', a.ly_do_kham))}
        <div class="fg fg-2">
          ${f('loai_kham', 'Phân loại đối tượng', sel('loai_kham', ['Khám mới', 'Tái khám'], a.loai_kham))}
          ${f('reception_time', 'Thời gian tiếp đón', '<input class="in" id="recepTime" readonly>')}
        </div>
      </div>
      <div class="stack" style="gap:8px">
        ${f('doi_tuong', 'Đối tượng [F10]', sel('doi_tuong', [{ value: 'dich_vu', label: 'Không bảo hiểm' }, { value: 'bhyt', label: 'Bảo hiểm y tế' }], a.doi_tuong, 'Chọn đối tượng'), { req: true })}
        ${f('loai_doi_tuong', 'Loại đối tượng', sel('loai_doi_tuong', LOAI_DOI_TUONG[a.doi_tuong] || [], a.loai_doi_tuong, 'Chọn loại đối tượng'), { req: true })}
      </div>
      <div class="check-box">
        <label class="check"><input type="checkbox" id="f_uu_tien" ${a.uu_tien ? 'checked' : ''}>Ưu tiên</label>
        <label class="check"><input type="checkbox" id="f_cap_cuu" ${a.cap_cuu ? 'checked' : ''}>Cấp cứu</label>
      </div>
      <div class="stack" style="gap:8px">
        ${f('ma_nb', 'Mã người bệnh', `<input class="in" value="${esc(a.ma_nb || '')}" readonly placeholder="Tự sinh khi lưu">`)}
        ${f('ma_hs', 'Mã hồ sơ', '<input class="in" readonly placeholder="Tự sinh khi lưu">')}
        ${f('ngay_dang_ky', 'Ngày đăng ký', '<input class="in" id="regDate" readonly>')}
      </div>
    </div></div></div>

    <div class="recep-panels">
      <div class="panel"><div class="panel-head">Thông tin cá nhân</div><div class="panel-body"><div class="fg fg-2">
        ${f('ho_ten', 'Họ và tên', inp('ho_ten', a.ho_ten, 'placeholder="Nhập họ và tên" autocomplete="off"'), { req: true })}
        ${f('so_dien_thoai', 'Số điện thoại', inp('so_dien_thoai', a.so_dien_thoai, 'placeholder="Nhập số điện thoại" inputmode="numeric"'))}
        <div class="full"><div class="fg fg-3">
          ${f('ngay_sinh', 'Ngày sinh', inp('ngay_sinh', a.ngay_sinh, 'type="date"'), { req: true })}
          ${f('tuoi', 'Tuổi', `<input class="in" id="f_tuoi" readonly value="${a.ngay_sinh ? age(a.ngay_sinh, ctx.clock.nowString()) : ''}">`)}
          ${f('gioi_tinh', 'Giới tính', sel('gioi_tinh', ['Nam', 'Nữ'], a.gioi_tinh, 'Chọn'), { req: true })}
        </div></div>
        ${f('nghe_nghiep', 'Nghề nghiệp', inp('nghe_nghiep', a.nghe_nghiep, 'placeholder="Chọn nghề nghiệp"'))}
        ${f('dan_toc', 'Dân tộc', inp('dan_toc', a.dan_toc))}
        ${f('dia_chi', 'Số nhà/ Thôn/ Xóm', inp('dia_chi', a.dia_chi, 'placeholder="SN/ Thôn/ Xóm"'))}
        ${f('tinh_xa', 'Phường/Xã, Tỉnh/TP', inp('tinh_xa', a.tinh_xa, 'placeholder="Phường/Xã, Tỉnh/TP"'), { req: true })}
        ${f('loai_giay_to', 'Loại giấy tờ tùy thân', sel('loai_giay_to', ['Căn cước', 'CMND', 'Hộ chiếu'], a.loai_giay_to))}
        ${f('cccd', 'Mã số giấy tờ tùy thân', inp('cccd', a.cccd, 'placeholder="Nhập mã số giấy tờ" inputmode="numeric"'))}
      </div></div></div>

      <div class="panel ${isBhyt ? '' : 'disabled'}" id="bhytPanel"><div class="panel-head">Thông tin BHYT</div><div class="panel-body"><div class="fg fg-2">
        ${f('so_bhyt', 'Số bảo hiểm', inp('so_bhyt', a.so_bhyt, 'placeholder="VD: DN4010123456789"'), { req: isBhyt, cls: 'full' })}
        ${f('bhyt_tu_ngay', 'Bảo hiểm từ ngày', inp('bhyt_tu_ngay', a.bhyt_tu_ngay, 'type="date"'))}
        ${f('bhyt_den_ngay', 'Bảo hiểm đến ngày', inp('bhyt_den_ngay', a.bhyt_den_ngay, 'type="date"'))}
        ${f('noi_dang_ky_bhyt', 'Nơi đăng ký', inp('noi_dang_ky_bhyt', a.noi_dang_ky_bhyt, 'placeholder="Chọn nơi đăng ký"'), { cls: 'full' })}
        ${f('noi_gioi_thieu', 'Nơi giới thiệu', inp('noi_gioi_thieu', a.noi_gioi_thieu, 'placeholder="Chọn nơi giới thiệu"'), { cls: 'full' })}
      </div></div></div>

      <div class="panel"><div class="panel-head">Thông tin bổ sung</div><div class="panel-body"><div class="fg fg-2">
        ${f('nguoi_bao_lanh', 'Họ tên người bảo lãnh', inp('nguoi_bao_lanh', a.nguoi_bao_lanh, 'placeholder="Nhập họ tên người bảo lãnh"'))}
        ${f('quan_he', 'Mối quan hệ với NB', sel('quan_he', ['Bố', 'Mẹ', 'Vợ', 'Chồng', 'Con', 'Anh/Chị/Em', 'Khác'], a.quan_he, 'Chọn mối quan hệ'))}
        ${f('sdt_bao_lanh', 'SĐT người bảo lãnh', inp('sdt_bao_lanh', a.sdt_bao_lanh, 'placeholder="Nhập SĐT người bảo lãnh"'))}
        ${f('ghi_chu', 'Ghi chú', inp('ghi_chu', a.ghi_chu, 'placeholder="Nhập ghi chú"'))}
      </div></div></div>
    </div>

    <div class="actions-bar">
      <button class="btn btn-outline" id="btnCancel">${icon('x')}Hủy tiếp đón</button>
      <button class="btn btn-primary" id="btnSave">${icon('save')}Lưu thông tin <kbd>[F4]</kbd></button>
    </div>
  </div>`;

  const $ = (q) => root.querySelector(q);
  const tick = () => {
    const s = ctx.clock.nowString();
    $('#recepTime').value = `${s.slice(11, 19)} – ${ddmm(s)}`;
    $('#regDate').value = ddmm(s);
  };
  tick();
  const timer = setInterval(tick, 1000);

  const collect = () => {
    root.querySelectorAll('.in[name]').forEach((el) => { a[el.name] = el.value.trim(); });
    a.so_bhyt = (a.so_bhyt || '').toUpperCase();
    state.adminValid = false;
    save(state);
  };
  root.addEventListener('input', (e) => { if (e.target.name) { setErr(root, e.target.name, ''); collect(); } });
  root.addEventListener('change', (e) => { if (e.target.name) { setErr(root, e.target.name, ''); collect(); } });
  $('#f_ngay_sinh').addEventListener('change', () => { $('#f_tuoi').value = a.ngay_sinh ? age(a.ngay_sinh, ctx.clock.nowString()) : ''; });
  $('#f_doi_tuong').addEventListener('change', () => {
    a.loai_doi_tuong = (LOAI_DOI_TUONG[a.doi_tuong] || [])[0] || '';
    $('#f_loai_doi_tuong').innerHTML = selectOptions(LOAI_DOI_TUONG[a.doi_tuong] || [], a.loai_doi_tuong, 'Chọn loại đối tượng');
    const bhyt = a.doi_tuong === 'bhyt';
    $('#bhytPanel').classList.toggle('disabled', !bhyt);
    $('[data-field="so_bhyt"]').classList.toggle('req', bhyt);
    if (!bhyt) setErr(root, 'so_bhyt', '');
    save(state);
  });
  for (const key of ['uu_tien', 'cap_cuu']) {
    $(`#f_${key}`).addEventListener('change', (e) => { a[key] = e.target.checked; state.adminValid = false; save(state); });
  }

  const validate = () => {
    collect();
    const errors = {};
    for (const [k, m] of Object.entries(REQUIRED)) if (!String(a[k] || '').trim()) errors[k] = m;
    if (a.doi_tuong === 'bhyt' && !a.so_bhyt) errors.so_bhyt = 'Người bệnh BHYT phải có số bảo hiểm';
    if (a.doi_tuong === 'bhyt' && a.so_bhyt && !/^[A-Z]{2}\d{13}$/.test(a.so_bhyt)) errors.so_bhyt = 'Gồm 2 chữ cái và 13 chữ số';
    if (a.so_dien_thoai && !/^\d{9,11}$/.test(a.so_dien_thoai)) errors.so_dien_thoai = 'Gồm 9–11 chữ số';
    if (a.cccd && !/^(\d{9}|\d{12})$/.test(a.cccd)) errors.cccd = 'Gồm 12 (hoặc 9) chữ số';
    if (a.ngay_sinh && a.ngay_sinh > ctx.clock.nowString().slice(0, 10)) errors.ngay_sinh = 'Sau ngày tiếp đón';
    ['ho_ten', 'ngay_sinh', 'gioi_tinh', 'tinh_xa', 'doi_tuong', 'loai_doi_tuong', 'so_bhyt', 'so_dien_thoai', 'cccd']
      .forEach((k) => setErr(root, k, errors[k]));
    return errors;
  };
  const submit = () => {
    const errors = validate();
    if (Object.keys(errors).length) {
      toast(`Vui lòng kiểm tra ${Object.keys(errors).length} trường bắt buộc/không hợp lệ`, 'error');
      root.querySelector('.f.invalid .in')?.focus();
      return;
    }
    state.adminValid = true; save(state);
    location.hash = '#/tiep-don/dich-vu';
  };
  $('#btnSave').addEventListener('click', submit);
  $('#btnCancel').addEventListener('click', () => { sessionStorage.removeItem(KEY); renderAdmin(root, ctx); toast('Đã hủy tiếp đón'); });
  const onKey = (e) => {
    if (e.key === 'F4') { e.preventDefault(); submit(); }
    if (e.key === 'F10') { e.preventDefault(); $('#f_doi_tuong').focus(); }
  };
  document.addEventListener('keydown', onKey);

  // Tìm người bệnh đã tiếp đón trên hệ thống
  let searchTimer;
  $('#search').addEventListener('input', (e) => {
    clearTimeout(searchTimer);
    const q = e.target.value.trim();
    const box = $('#searchResults');
    if (q.length < 2) { box.hidden = true; return; }
    searchTimer = setTimeout(async () => {
      const rows = await api(`/api/patients?q=${encodeURIComponent(q)}`);
      box.hidden = false;
      box.innerHTML = rows.length ? rows.map((p, i) => `<div class="opt" data-i="${i}"><b>${esc(p.ho_ten)}</b>
          <div class="cell-sub">${esc(p.ma_nb)} · ${ddmm(p.ngay_sinh)} · ${esc(p.so_dien_thoai || '')}</div></div>`).join('')
        : '<div class="empty" style="padding:12px">Không tìm thấy người bệnh</div>';
      box.querySelectorAll('[data-i]').forEach((el) => el.addEventListener('click', () => {
        const p = rows[Number(el.dataset.i)];
        Object.assign(a, { ma_nb: p.ma_nb, ho_ten: p.ho_ten, ngay_sinh: p.ngay_sinh, gioi_tinh: p.gioi_tinh, so_dien_thoai: p.so_dien_thoai,
          cccd: p.cccd, dia_chi: p.dia_chi, tinh_xa: p.tinh_xa, nghe_nghiep: p.nghe_nghiep, dan_toc: p.dan_toc, loai_kham: 'Tái khám' });
        save(state); renderAdmin(root, ctx); toast(`Đã lấy thông tin ${p.ho_ten}`, 'success');
      }));
    }, 250);
  });

  return () => { clearInterval(timer); document.removeEventListener('keydown', onKey); };
}

// ============================================================== 2. Chỉ định dịch vụ
function shiftLabel(now) {
  const m = Number(now.slice(11, 13)) * 60 + Number(now.slice(14, 16));
  if (m >= 300 && m < 690) return 'Ca sáng (5h00–11h30)';
  if (m >= 690 && m < 780) return 'Nghỉ trưa (11h30–13h00)';
  if (m >= 780 && m < 1020) return 'Ca chiều (13h00–17h00)';
  return 'Ngoài giờ';
}

function renderService(root, ctx) {
  const state = load();
  if (!state.adminValid) { toast('Vui lòng lưu thông tin hành chính trước', 'error'); location.hash = '#/tiep-don'; return undefined; }
  const a = state.admin;
  const s = state.service;
  const cat = ctx.meta.catalog;
  const services = cat.services.map((x) => ({ value: x.ma, label: `${x.ma} – ${x.ten}`, disabled: !x.in_scope }));
  const now = ctx.clock.nowString();
  let rooms = [];
  let prediction = null;
  let saved = null;
  let busy = false;

  const inputs = () => ({
    ma_dich_vu: s.ma_dich_vu || null, ma_phong_thuc_hien: s.ma_phong_thuc_hien || null, ma_khoa: s.ma_khoa || null,
    patient_payment_type: a.doi_tuong || null, uu_tien: !!a.uu_tien, cap_cuu: !!a.cap_cuu, ordered_time: s.ordered_time || null,
  });

  root.innerHTML = `
  <div class="container">
    <div class="page-title"><h1>Chỉ định dịch vụ</h1>
      <div class="right"><span class="in" style="display:flex;align-items:center;gap:6px;width:auto">${icon('clock')}<span id="shift">${shiftLabel(now)}</span></span></div></div>
    <div class="svc-layout">
      <div class="stack" style="gap:0">
        <div class="card"><div class="card-body tight"><div class="patient-card">
          <div class="photo">${USER_SVG}</div>
          <div style="min-width:0">
            <div class="row"><span class="name">${esc(a.ho_ten)}</span><span class="muted">(${esc(a.gioi_tinh)})</span>
              ${a.uu_tien ? '<span class="badge prio">Ưu tiên</span>' : ''}${a.cap_cuu ? '<span class="badge skipped">Cấp cứu</span>' : ''}
              <a class="row" style="margin-left:auto;gap:4px" href="#/tiep-don">Sửa thông tin ${icon('edit')}</a></div>
            <div class="info-box">
              <div>Ngày sinh: <b>${ddmm(a.ngay_sinh)} – ${age(a.ngay_sinh, now)} tuổi</b></div>
              <div>Đối tượng: <b>${a.doi_tuong === 'bhyt' ? 'BHYT' : 'Không bảo hiểm'}</b></div>
              <div>Địa chỉ: <b>${esc([a.dia_chi, a.tinh_xa].filter(Boolean).join(', '))}</b></div>
              <div>Mã NB: <b>${esc(a.ma_nb || 'Tự sinh khi lưu')}</b></div>
              <div>Loại đối tượng: <b>${esc(a.loai_doi_tuong)}</b></div>
              <div>SĐT: <b>${esc(a.so_dien_thoai || '')}</b></div>
              <div>Phân loại: <b>${esc(a.loai_kham)}</b></div>
              <div>Số BHYT: <b>${esc(a.so_bhyt || '')}</b></div>
              <div>Người bảo lãnh: <b>${esc(a.nguoi_bao_lanh || '')}</b></div>
            </div>
          </div>
        </div></div></div>

        <div class="svc-pick">
          <b>Loại dịch vụ [F6]</b><select class="in" style="width:110px" id="svcType"><option>Khám</option></select>
          <div class="f" data-field="ma_dich_vu" style="width:360px"><select class="in" id="f_service">${selectOptions(services, s.ma_dich_vu, 'Chọn dịch vụ [F2]')}</select><span class="err"></span></div>
        </div>

        <div class="card">
          <div class="card-header"><h3>Dịch vụ đã chọn</h3>
            <div class="right"><select class="in" id="f_bac_si" style="width:260px;height:30px">${selectOptions(cat.doctors.map((d) => ({ value: d, label: `BS ${d}` })), s.bac_si, 'BS chỉ định')}</select></div></div>
          <div class="table-wrap"><table class="table"><thead><tr>
            <th class="center">STT</th><th>Mã DV</th><th>Tên dịch vụ</th><th>Phòng thực hiện</th><th class="num">TG chờ<br>dự kiến</th>
            <th class="num">Dự kiến<br>khám</th><th>Cảnh báo</th><th></th></tr></thead><tbody id="svcBody"></tbody></table></div>
          <div id="result"></div>
        </div>
      </div>

      <div class="card">
        <div class="card-header"><h3 class="nowrap">SL NB theo PK</h3>
          <div class="right"><select class="in" id="f_khoa" style="width:150px;height:30px">${selectOptions(cat.departments.map((d) => ({ value: d.ma, label: d.ten })), s.ma_khoa, 'Tất cả chuyên khoa')}</select>
            <div class="in-icon" style="width:150px"><input class="in" id="roomSearch" placeholder="Tìm phòng" style="height:30px">${icon('search')}</div></div></div>
        <div class="table-wrap" style="max-height:calc(100vh - 250px)"><table class="table" id="roomTable">
          <thead><tr><th>Phòng</th><th class="num">CK<br>thường</th><th class="num">CK<br>UT</th><th class="num">Đang<br>khám</th><th class="num">Đã<br>KL</th><th class="num">Tổng</th>
          <th class="num">TG chờ<br>dự kiến</th></tr></thead><tbody></tbody></table></div>
      </div>
    </div>

    <div class="actions-bar">
      <button class="btn btn-outline" id="btnPrint" disabled>${icon('printer')}In giấy tờ</button>
      <button class="btn btn-primary" id="btnSave">${icon('ticket')}Lưu &amp; cấp số <kbd>[F4]</kbd></button>
      <button class="btn btn-primary" id="btnNext">NB tiếp theo <kbd>[F1]</kbd>${icon('doubleRight')}</button>
    </div>
  </div>`;

  const $ = (q) => root.querySelector(q);

  function renderServiceRow() {
    const body = $('#svcBody');
    if (!s.ma_dich_vu) { body.innerHTML = `<tr><td colspan="8">${emptyState('Chưa chọn dịch vụ. Chọn dịch vụ khám [F2].', 'stethoscope')}</td></tr>`; return; }
    const svc = cat.services.find((x) => x.ma === s.ma_dich_vu);
    const roomOpts = rooms.map((r) => ({ value: r.ma_phong, label: `${r.ma_phong} – ${r.ten_phong}` }));
    if (s.ma_phong_thuc_hien && !rooms.some((r) => r.ma_phong === s.ma_phong_thuc_hien)) roomOpts.unshift({ value: s.ma_phong_thuc_hien, label: s.ma_phong_thuc_hien });
    const p = prediction?.status === 'ok' ? prediction.prediction : null;
    body.innerHTML = `<tr class="${saved ? '' : 'selected'}">
      <td class="center">1</td><td><b>${esc(s.ma_dich_vu)}</b></td><td>${esc(svc?.ten || '')}</td>
      <td><div class="f ${s.ma_phong_thuc_hien ? '' : 'invalid'}" data-field="ma_phong_thuc_hien" style="min-width:170px;max-width:220px">
        <select class="in" id="f_room" style="height:28px" ${saved ? 'disabled' : ''}>${selectOptions(roomOpts, s.ma_phong_thuc_hien, 'Chọn phòng khám')}</select><span class="err"></span></div></td>
      <td class="num">${p ? `<b style="color:var(--primary)">${Math.round(p.wait_minutes)} phút</b>` : '–'}</td>
      <td class="num">${p ? `<b>${hhmm(p.predicted_exam_time)}</b>` : '–'}</td>
      <td>${p ? (p.lunch_warning ? lunchBadge({ lunch_warning: true, suggested_return_time: p.suggested_return_time }) : '') : ''}</td>
      <td class="center">${saved ? '' : `<button class="btn btn-ghost btn-sm" id="btnRemove" title="Xóa dịch vụ">${icon('trash')}</button>`}</td></tr>`;
    $('#f_room')?.addEventListener('change', (e) => chooseRoom(e.target.value));
    $('#btnRemove')?.addEventListener('click', () => { s.ma_dich_vu = undefined; s.ma_phong_thuc_hien = undefined; s.ordered_time = undefined; prediction = null; save(state); $('#f_service').value = ''; refresh(); });
  }

  function renderResult() {
    const box = $('#result');
    if (!s.ma_dich_vu) { box.innerHTML = ''; return; }
    if (!s.ma_phong_thuc_hien) { box.innerHTML = '<div class="result-bar empty-state">Vui lòng chọn Phòng khám trước khi dự báo thời gian chờ.</div>'; return; }
    if (!prediction) { box.innerHTML = '<div class="result-bar empty-state" style="color:var(--muted)"><span class="spinner"></span> Đang dự báo…</div>'; return; }
    if (prediction.status !== 'ok') { box.innerHTML = `<div class="result-bar empty-state" style="color:var(--warning-ink)">Không dự báo: ${esc(prediction.reason)}</div>`; return; }
    const p = prediction.prediction;
    const stt = saved ? saved.display_stt : prediction.display_stt;
    box.innerHTML = `<div class="result-bar">
        <div><div class="lbl">Thời gian chờ dự kiến</div><div class="val">${Math.round(p.wait_minutes)}<small>phút</small></div><div class="sub">Khoảng 80%: ≤ ${Math.round(p.wait_p80_minutes)} phút</div></div>
        <div><div class="lbl">Dự kiến được khám lúc</div><div class="val">${hhmm(p.predicted_exam_time)}</div><div class="sub">${ddmm(p.predicted_exam_time)}</div></div>
        <div><div class="lbl">${saved ? 'Số thứ tự' : 'Số thứ tự dự kiến'}</div><div class="val navy">${esc(stt)}</div><div class="sub">${esc(prediction.room.ma_phong)}</div></div>
        <div><div class="lbl">Phòng thực hiện</div><div style="font-weight:500;margin-top:4px">${esc(prediction.room.ten_phong)}</div><div class="sub">${esc(prediction.room.dia_diem || '')}</div></div>
      </div>
      ${p.probability_cross_lunch == null ? '' : p.lunch_warning
    ? `<div class="lunch-alert">${icon('sun')}Cảnh báo: Chờ quá giờ nghỉ trưa (${ctx.meta.model.lunch_start}–${ctx.meta.model.lunch_end}) · xác suất ${Math.round(100 * p.probability_cross_lunch)}%${p.suggested_return_time ? ` · đề xuất quay lại lúc ${hhmm(p.suggested_return_time)}` : ''}</div>`
    : `<div class="lunch-ok">${icon('checkCircle')}Không có nguy cơ chờ quá giờ nghỉ trưa (xác suất ${Math.round(100 * p.probability_cross_lunch)}%)</div>`}`;
  }

  function renderRooms() {
    const q = $('#roomSearch').value.trim().toLowerCase();
    const body = $('#roomTable tbody');
    if (!s.ma_dich_vu) { body.innerHTML = `<tr><td colspan="7">${emptyState('Chọn dịch vụ để xem các phòng khám', 'building')}</td></tr>`; return; }
    const list = rooms.filter((r) => !q || `${r.ma_phong} ${r.ten_phong}`.toLowerCase().includes(q));
    body.innerHTML = list.map((r) => {
      const w = r.predicted_wait_minutes;
      return `<tr class="selectable ${r.ma_phong === s.ma_phong_thuc_hien ? 'selected' : ''} ${r.active ? '' : 'dim'}" data-room="${esc(r.ma_phong)}">
        <td>${esc(r.ma_phong)} – ${esc(r.ten_phong)}</td>
        <td class="num">${n0(r.waiting_normal)}</td><td class="num">${n0(r.waiting_priority)}</td><td class="num">${n0(r.in_exam)}</td>
        <td class="num">${n0(r.done)}</td><td class="num">${n0(r.total)}</td>
        <td class="num wait-cell ${waitClass(w)}">${w == null ? '–' : `<b>${Math.round(w)} phút</b><div class="cell-sub">${hhmm(r.predicted_exam_time)}</div>${lunchBadge(r)}`}</td></tr>`;
    }).join('') || `<tr><td colspan="7">${emptyState('Không có phòng phù hợp', 'building')}</td></tr>`;
  }

  async function loadRooms() {
    if (!s.ma_dich_vu) { rooms = []; return; }
    const res = await api('/api/rooms/options', { method: 'POST', body: inputs() });
    rooms = res.rooms;
    if (res.out_of_scope_reason) rooms.forEach((r) => { r.predicted_wait_minutes = null; r.lunch_warning = false; });
  }

  async function predict() {
    prediction = null;
    if (!s.ma_dich_vu || !s.ma_phong_thuc_hien) return;
    renderResult();
    try { prediction = await api('/api/predict', { method: 'POST', body: inputs() }); } catch (e) {
      Object.entries(e.errors || {}).forEach(([k, m]) => setErr(root, k, m)); toast(e.message, 'error');
    }
  }

  async function refresh() {
    if (saved) return;
    try { await loadRooms(); await predict(); } catch (e) { toast(e.message, 'error'); }
    renderServiceRow(); renderResult(); renderRooms();
  }
  async function chooseRoom(code) {
    if (saved) return;
    s.ma_phong_thuc_hien = code || undefined; save(state);
    renderRooms(); await predict(); renderServiceRow(); renderResult();
  }

  $('#f_service').addEventListener('change', async (e) => {
    if (saved) return;
    s.ma_dich_vu = e.target.value || undefined; s.ma_phong_thuc_hien = undefined; prediction = null;
    setErr(root, 'ma_dich_vu', '');
    s.ordered_time = s.ma_dich_vu ? (await api('/api/clock')).now : undefined;  // thời điểm chỉ định
    save(state); refresh();
  });
  $('#f_khoa').addEventListener('change', (e) => { s.ma_khoa = e.target.value || undefined; save(state); refresh(); });
  $('#f_bac_si').addEventListener('change', (e) => { s.bac_si = e.target.value || undefined; save(state); });
  $('#roomSearch').addEventListener('input', renderRooms);
  $('#roomTable').addEventListener('click', (e) => { const tr = e.target.closest('tr[data-room]'); if (tr) chooseRoom(tr.dataset.room); });

  const submit = async () => {
    if (busy || saved) return;
    if (!s.ma_dich_vu) { setErr(root, 'ma_dich_vu', 'Vui lòng chọn dịch vụ khám'); toast('Vui lòng chọn dịch vụ khám', 'error'); return; }
    if (!s.ma_phong_thuc_hien) { toast('Vui lòng chọn Phòng khám trước khi dự báo thời gian chờ', 'error'); return; }
    busy = true;
    try {
      const res = await api('/api/reception', { method: 'POST', body: { admin: a, service: { ma_dich_vu: s.ma_dich_vu, ma_phong_thuc_hien: s.ma_phong_thuc_hien, bac_si: s.bac_si || null, ordered_time: s.ordered_time || null } } });
      saved = res.ticket;
      prediction = res.prediction;
      sessionStorage.removeItem(KEY);
      $('#btnPrint').disabled = false; $('#btnSave').disabled = true; $('#f_service').disabled = true;
      renderServiceRow(); renderResult();
      toast(`Tiếp đón thành công – số thứ tự ${saved.display_stt}, ${saved.room.ma_phong}`, 'success');
      showTicket(saved);
    } catch (e) {
      Object.entries(e.errors || {}).forEach(([k, m]) => setErr(root, k, m));
      toast(Object.keys(e.errors || {}).length ? Object.values(e.errors).join(' ') : e.message, 'error');
    } finally { busy = false; }
  };
  const next = () => { sessionStorage.removeItem(KEY); location.hash = '#/tiep-don'; };
  $('#btnSave').addEventListener('click', submit);
  $('#btnNext').addEventListener('click', next);
  $('#btnPrint').addEventListener('click', () => saved && showTicket(saved, true));
  const onKey = (e) => {
    if (e.key === 'F4') { e.preventDefault(); submit(); }
    if (e.key === 'F1') { e.preventDefault(); next(); }
    if (e.key === 'F2') { e.preventDefault(); $('#f_service').focus(); }
    if (e.key === 'F6') { e.preventDefault(); $('#svcType').focus(); }
  };
  document.addEventListener('keydown', onKey);

  renderServiceRow(); renderResult(); renderRooms();
  if (s.ma_dich_vu) refresh();
  const timer = setInterval(() => { if (s.ma_dich_vu && !saved) refresh(); $('#shift').textContent = shiftLabel(ctx.clock.nowString()); }, 60000);
  const onClock = () => { if (s.ma_dich_vu && !saved) refresh(); };
  window.addEventListener('clock-changed', onClock);
  return () => { clearInterval(timer); document.removeEventListener('keydown', onKey); window.removeEventListener('clock-changed', onClock); };
}

// ============================================================== Phiếu số thứ tự
function showTicket(t, print = false) {
  const ok = t.prediction_status === 'ok';
  const m = modal({
    title: 'Phiếu số thứ tự',
    body: `<div class="print-area"><div class="ticket">
      <div class="t-hosp">Phiếu số thứ tự khám bệnh</div>
      <div class="t-room">${esc(t.room.ten_phong)}</div>
      <div class="muted">${esc(t.room.dia_diem || '')} · ${esc(t.service_name || t.ma_dich_vu)}</div>
      <div class="t-stt">${esc(t.display_stt)}</div>
      <div style="font-weight:700;font-size:15px">${esc(t.ho_ten)}</div>
      <div class="muted">${esc(t.ma_nb)} · ${hhmm(t.queue_entry_time)} ${ddmm(t.queue_entry_time)}</div>
      ${ok ? `<div class="t-grid"><div>Thời gian chờ dự kiến<b>${minutes(t.predicted_wait_minutes)}</b></div><div>Dự kiến được khám lúc<b>${hhmm(t.predicted_exam_time)}</b></div></div>
        ${t.lunch_warning ? `<div class="lunch-alert" style="margin:10px 0 0">${icon('sun')}Chờ quá giờ nghỉ trưa${t.suggested_return_time ? ` · quay lại lúc ${hhmm(t.suggested_return_time)}` : ''}</div>` : ''}`
    : `<div class="muted" style="margin-top:10px">${esc(t.out_of_scope_reason || '')}</div>`}
    </div></div>`,
    footer: `<a class="btn" href="#/qms/tv/${encodeURIComponent(t.ma_phong)}" data-close>${icon('qms')}Màn hình phòng</a>
      <button class="btn" id="tPrint">${icon('printer')}In phiếu</button>
      <button class="btn btn-primary" id="tNext">NB tiếp theo [F1]</button>`,
  });
  m.el.querySelector('#tPrint').addEventListener('click', () => window.print());
  m.el.querySelector('#tNext').addEventListener('click', () => { m.close(); sessionStorage.removeItem(KEY); location.hash = '#/tiep-don'; });
  if (print) setTimeout(() => window.print(), 200);
}
