// ECharts helpers following the dataviz method: fixed categorical order,
// one-hue sequential ramp, single y-axis, thin marks, hover tooltips, and a
// table-view twin for every chart.
import { icon } from './icons.js';
import { esc } from './ui.js';

export const SERIES = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#4a3aa7', '#e34948'];
export const SEQ = ['#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95', '#0d366b'];
const INK = '#0f1b2d';
const INK2 = '#52514e';
const MUTED = '#898781';
const GRID = '#e8e7e1';
const BASE = '#c3c2b7';
const FONT = 'Roboto, "Segoe UI", Arial, sans-serif';

const instances = new Map();
const tables = new Map();

export function chartCard(id, title, { sub = '', cls = '', note = '' } = {}) {
  return `<div class="card chart-card"><div class="card-header"><h3>${title}</h3>${sub ? `<span class="sub">${sub}</span>` : ''}
    <div class="right"><div class="view-toggle" data-chart="${id}">
      <button class="active" data-view="chart" title="Biểu đồ">${icon('chart')}Biểu đồ</button>
      <button data-view="table" title="Bảng số liệu">${icon('table')}Bảng</button></div></div></div>
    <div class="card-body tight"><div class="chart ${cls}" id="${id}"></div><div class="chart-table" id="${id}-table" hidden></div>
    ${note ? `<div class="note" style="margin-top:6px">${icon('info')}<span>${note}</span></div>` : ''}</div></div>`;
}

export function bindToggles(root) {
  root.querySelectorAll('.view-toggle').forEach((box) => box.addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return;
    const id = box.dataset.chart;
    box.querySelectorAll('button').forEach((x) => x.classList.toggle('active', x === b));
    const showTable = b.dataset.view === 'table';
    document.getElementById(id).hidden = showTable;
    const t = document.getElementById(`${id}-table`);
    t.hidden = !showTable;
    if (showTable) t.innerHTML = renderTable(tables.get(id));
    else instances.get(id)?.resize();
  }));
}

function renderTable(spec) {
  if (!spec) return '<div class="empty">Không có dữ liệu</div>';
  return `<table class="table"><thead><tr>${spec.columns.map((c, i) => `<th class="${i ? 'num' : ''}">${esc(c)}</th>`).join('')}</tr></thead>
    <tbody>${spec.rows.map((r) => `<tr>${r.map((v, i) => `<td class="${i ? 'num' : ''}">${v === null || v === undefined ? '–' : esc(typeof v === 'number' ? v.toLocaleString('vi-VN') : v)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}

export function mount(id, option, table) {
  const el = document.getElementById(id);
  if (!el) return;
  tables.set(id, table);
  let chart = instances.get(id);
  if (chart && chart.getDom() !== el) { chart.dispose(); chart = null; }
  if (!chart) { chart = window.echarts.init(el, null, { renderer: 'canvas' }); instances.set(id, chart); }
  chart.setOption(base(option), true);
  const t = document.getElementById(`${id}-table`);
  if (t && !t.hidden) t.innerHTML = renderTable(table);
}

export function disposeAll() { instances.forEach((c) => c.dispose()); instances.clear(); tables.clear(); }
window.addEventListener('resize', () => instances.forEach((c) => c.resize()));

function base(option) {
  const { grid, legend, tooltip, ...rest } = option;
  return {
    textStyle: { fontFamily: FONT, color: INK2 },
    animationDuration: 300,
    ...rest,
    grid: { left: 12, right: 18, top: legend ? 38 : 16, bottom: 8, containLabel: true, ...(grid || {}) },
    tooltip: {
      backgroundColor: '#fff', borderColor: '#e2e8f0', borderWidth: 1, padding: [8, 12],
      textStyle: { color: INK, fontFamily: FONT, fontSize: 12.5 }, extraCssText: 'box-shadow:0 6px 20px rgba(15,27,45,.12);border-radius:8px;',
      ...(tooltip || {}),
    },
    legend: legend ? { top: 0, left: 0, icon: 'roundRect', itemWidth: 12, itemHeight: 4, textStyle: { color: INK2, fontSize: 12 }, ...legend } : undefined,
  };
}

const axisCommon = {
  axisLine: { lineStyle: { color: BASE } }, axisTick: { show: false },
  axisLabel: { color: MUTED, fontSize: 11.5 }, splitLine: { lineStyle: { color: GRID, type: 'solid' } },
  nameTextStyle: { color: MUTED, fontSize: 11.5 },
};
export const catAxis = (data, extra = {}) => ({ type: 'category', data, ...axisCommon, splitLine: { show: false }, ...extra });
export const valAxis = (name, extra = {}) => ({ type: 'value', name, ...axisCommon, axisLine: { show: false }, ...extra });

export function bar({ categories, values, name, unit = '', color = SERIES[0], horizontal = false, valueFmt, labels = false }) {
  const fmt = valueFmt || ((v) => (v == null ? '–' : `${Number(v).toLocaleString('vi-VN')}${unit ? ` ${unit}` : ''}`));
  const series = [{
    type: 'bar', name, data: values, barMaxWidth: horizontal ? 14 : 22, barCategoryGap: '30%',
    itemStyle: { color, borderRadius: horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0] },
    emphasis: { itemStyle: { color: '#184f95' } },
    label: labels ? { show: true, position: horizontal ? 'right' : 'top', color: INK2, fontSize: 11, formatter: (p) => fmt(p.value) } : undefined,
  }];
  return {
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(42,120,214,.06)' } }, valueFormatter: fmt },
    xAxis: horizontal ? valAxis(unit) : catAxis(categories, categories.length <= 14 ? { axisLabel: { color: MUTED, fontSize: 11.5, interval: 0 } } : {}),
    yAxis: horizontal ? catAxis(categories, { inverse: true, axisLabel: { color: INK2, fontSize: 11.5, width: 210, overflow: 'truncate' } }) : valAxis(unit),
    series,
  };
}

export function lines({ x, series, unit = '', area = false }) {
  const fmt = (v) => (v == null ? '–' : `${Number(v).toLocaleString('vi-VN')}${unit ? ` ${unit}` : ''}`);
  return {
    legend: series.length > 1 ? {} : undefined,
    tooltip: { trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: BASE } }, valueFormatter: fmt },
    xAxis: catAxis(x, { boundaryGap: false }),
    yAxis: valAxis(series.length > 1 ? '' : unit),
    series: series.map((s, i) => ({
      type: 'line', name: s.name, data: s.data, smooth: 0.2, showSymbol: false, symbolSize: 8, connectNulls: false,
      lineStyle: { width: 2, color: s.color || SERIES[i], type: s.dashed ? 'dashed' : 'solid' },
      itemStyle: { color: s.color || SERIES[i] },
      areaStyle: area && i === 0 ? { color: 'rgba(42,120,214,.08)' } : undefined,
      endLabel: series.length > 1 ? { show: true, color: INK2, fontSize: 11, formatter: () => s.name } : undefined,
    })),
    grid: { right: series.length > 1 ? 90 : 18 },
  };
}
