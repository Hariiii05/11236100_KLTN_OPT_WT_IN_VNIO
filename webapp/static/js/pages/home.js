// Trang chủ: đúng 3 phân hệ, bố cục theo ảnh mẫu "0. Trang chủ".

const ILLUS = {
  reception: `<svg class="illus" viewBox="0 0 96 96" aria-hidden="true">
    <rect x="10" y="58" width="76" height="8" rx="2" fill="#8d6e63"/><rect x="14" y="66" width="6" height="22" fill="#6d4c41"/><rect x="76" y="66" width="6" height="22" fill="#6d4c41"/>
    <rect x="18" y="36" width="30" height="20" rx="2" fill="#42a5f5"/><rect x="21" y="39" width="24" height="14" fill="#e3f2fd"/><rect x="30" y="56" width="6" height="3" fill="#1e88e5"/>
    <circle cx="66" cy="26" r="9" fill="#ffcc80"/><path d="M57 24c1-8 17-8 18 0-4-3-13-3-18 0z" fill="#5d4037"/>
    <path d="M52 58c0-13 6-20 14-20s14 7 14 20z" fill="#26a69a"/><path d="M62 38h8l-4 8z" fill="#fff"/>
    <rect x="22" y="42" width="14" height="2" fill="#90caf9"/><rect x="22" y="46" width="18" height="2" fill="#90caf9"/></svg>`,
  qms: `<svg class="illus" viewBox="0 0 96 96" aria-hidden="true">
    <rect x="8" y="12" width="80" height="50" rx="5" fill="#26a69a"/><rect x="13" y="17" width="70" height="40" rx="3" fill="#e0f2f1"/>
    <rect x="13" y="17" width="70" height="10" fill="#00897b"/><rect x="18" y="20" width="26" height="4" rx="2" fill="#b2dfdb"/>
    <text x="30" y="49" font-family="Arial" font-weight="700" font-size="17" fill="#00695c">025</text>
    <rect x="58" y="33" width="20" height="4" rx="2" fill="#80cbc4"/><rect x="58" y="40" width="20" height="4" rx="2" fill="#80cbc4"/><rect x="58" y="47" width="14" height="4" rx="2" fill="#ffb74d"/>
    <rect x="44" y="62" width="8" height="12" fill="#546e7a"/><rect x="32" y="74" width="32" height="5" rx="2" fill="#546e7a"/>
    <circle cx="18" cy="80" r="5" fill="#ffcc80"/><path d="M10 94c0-8 4-11 8-11s8 3 8 11z" fill="#5c6bc0"/>
    <circle cx="80" cy="80" r="5" fill="#ffcc80"/><path d="M72 94c0-8 4-11 8-11s8 3 8 11z" fill="#ef5350"/></svg>`,
  dashboard: `<svg class="illus" viewBox="0 0 96 96" aria-hidden="true">
    <rect x="8" y="10" width="80" height="62" rx="5" fill="#fff" stroke="#90a4ae" stroke-width="2"/>
    <rect x="8" y="10" width="80" height="10" rx="5" fill="#42a5f5"/><circle cx="15" cy="15" r="2" fill="#fff"/><circle cx="21" cy="15" r="2" fill="#fff"/>
    <rect x="16" y="50" width="8" height="14" rx="1.5" fill="#42a5f5"/><rect x="28" y="40" width="8" height="24" rx="1.5" fill="#1e88e5"/><rect x="40" y="46" width="8" height="18" rx="1.5" fill="#42a5f5"/>
    <polyline points="16,38 28,30 40,34 52,24" fill="none" stroke="#ff7043" stroke-width="2.5" stroke-linecap="round"/>
    <circle cx="70" cy="42" r="12" fill="#ffca28"/><path d="M70 42V30a12 12 0 0 1 12 12z" fill="#ef5350"/><path d="M70 42h12a12 12 0 0 1-4 9z" fill="#26a69a"/>
    <rect x="30" y="72" width="36" height="6" fill="#90a4ae"/><rect x="22" y="78" width="52" height="5" rx="2" fill="#607d8b"/></svg>`,
};

export async function render(root, _params, ctx) {
  root.innerHTML = `
  <div class="container" style="max-width:1120px">
    <div class="home-head"><h1>Trang chủ</h1><p>Chào mừng đến với ${ctx.meta.hospital}</p></div>
    <div class="module-grid">
      <a class="module" href="#/tiep-don">${ILLUS.reception}<span>Quản lý tiếp đón</span></a>
      <a class="module" href="#/qms">${ILLUS.qms}<span>QMS – Quản lý hàng đợi</span></a>
      <a class="module" href="#/dashboard">${ILLUS.dashboard}<span>Dashboard</span></a>
    </div>
  </div>`;
}
