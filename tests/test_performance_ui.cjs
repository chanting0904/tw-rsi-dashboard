const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {calculateRange, init} = require('../performance_ui.js');

const realPerf = JSON.parse(fs.readFileSync(path.join(__dirname, '../output/perf.json'), 'utf8'));
const sample = {start_capital: 100, nav: [
  ['2023-12-28', 100], ['2023-12-29', 200],
  ['2024-01-02', 150], ['2024-06-28', 240], ['2024-12-31', 220],
  ['2025-01-02', 110], ['2025-12-31', 330]
]};

function fixture({perf = sample, library = 'working'} = {}) {
  const elements = {}, chartCalls = [], connected = [], ranges = [];
  function element(id) {
    return elements[id] ??= {
      id, value: '', textContent: '', className: '', children: [], handlers: {},
      appendChild(child) { this.children.push(child); },
      addEventListener(type, handler) { (this.handlers[type] ??= []).push(handler); },
      dispatch(type) { for (const handler of this.handlers[type] || []) handler(); }
    };
  }
  const document = {getElementById: element, createElement: () => ({value: '', textContent: ''})};
  const charts = library === 'missing' ? null : {
    init(node) {
      if (library === 'throwing' || (library === 'partial' && chartCalls.length % 2 === 1)) throw Error('chart renderer failed');
      const instance = {id: node.id, options: [], disposed: false,
        setOption(option) { if (library === 'draw-throwing') throw Error('chart update failed'); this.options.push(option); },
        dispose() { this.disposed = true; }, resize() {}};
      chartCalls.push(instance); return instance;
    },
    connect(group) { connected.push(group); }
  };
  element('cap').value = '100037'; element('cap-previous').value = '100000';
  const controller = init({perf, document, charts, onRange: result => ranges.push(result)});
  return {element, elements, chartCalls, connected, ranges, controller};
}

test('published full-period daily NAV reproduces CAGR and MDD within publication rounding', () => {
  const first = Number(realPerf.nav[0][0].slice(0, 4));
  const last = Number(realPerf.nav.at(-1)[0].slice(0, 4));
  const result = calculateRange(realPerf, realPerf.start_capital, first, last);
  assert.ok(Math.abs(result.cagr - realPerf.cagr) < .000051);
  assert.ok(Math.abs(result.mdd - realPerf.mdd) < .000051);
  assert.equal(result.observations, realPerf.nav.length);
  assert.equal(result.endDate, realPerf.completed_through);
  assert.equal(result.navSeries.at(-1)[1], realPerf.nav.at(-1)[1]);
});

test('every published calendar-year return and drawdown matches the selected daily range', () => {
  for (const year of realPerf.yearly) {
    const result = calculateRange(realPerf, realPerf.start_capital, year.year, year.year);
    assert.ok(Math.abs(result.returnRate * 100 - year.ret) < .0051, `${year.year} return`);
    assert.ok(Math.abs(result.mdd * 100 - year.mdd) < .0051, `${year.year} MDD`);
  }
});

test('first-day fees are included rather than rebasing on the first already-reduced NAV', () => {
  const result = calculateRange({start_capital: 100, nav: [['2024-01-02', 99], ['2024-01-03', 110]]}, 1000, 2024, 2024);
  assert.equal(result.navSeries[0][1], 1000);
  assert.equal(result.navSeries[1][1], 990);
  assert.ok(Math.abs(result.returnRate - .10) < 1e-12);
  assert.ok(Math.abs(result.mdd + .01) < 1e-12);
  assert.ok(Math.abs(result.ddSeries[1][1] + 1) < 1e-12);
});

test('a later year uses the preceding observed close and resets its own drawdown peak', () => {
  const result = calculateRange(sample, 1000, 2024, 2024);
  assert.equal(result.basisDate, '2023-12-29');
  assert.deepEqual(result.navSeries[0], ['2023-12-29', 1000]);
  assert.deepEqual(result.navSeries.at(-1), ['2024-12-31', 1100]);
  assert.ok(Math.abs(result.returnRate - .1) < 1e-12);
  assert.equal(result.mdd, -.25);
  const next = calculateRange(sample, 1000, 2025, 2025);
  assert.equal(next.basisDate, '2024-12-31');
  assert.equal(next.mdd, -.5);
  assert.equal(next.returnRate, .5);
});

test('bad capital, reversed years, missing ranges and invalid NAV refuse a result', () => {
  for (const capital of [0, -1, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1])
    assert.throws(() => calculateRange(sample, capital, 2024, 2025), /本金/);
  assert.throws(() => calculateRange(sample, 100, 2025, 2024), /年份/);
  assert.throws(() => calculateRange(sample, 100, 2010, 2011), /沒有回測/);
  assert.throws(() => calculateRange({start_capital: 100, nav: []}, 100, 2024, 2025), /尚無/);
  assert.throws(() => calculateRange({start_capital: 100, nav: [['2024-01-02', 100], ['2024-01-02', 101]]}, 100, 2024, 2024), /不完整/);
  assert.throws(() => calculateRange({start_capital: 100, nav: [['2024-01-02', NaN]]}, 100, 2024, 2024), /不完整/);
});

test('typing twice the calculator capital doubles cards and asset chart without changing percentage results', () => {
  const f = fixture();
  const first = f.ranges.at(-1), beforeDD = f.chartCalls[1].options.at(-1).series[0].data;
  const beforePercent = [f.element('pc-ret').textContent, f.element('pc-cagr').textContent, f.element('pc-mdd').textContent];
  f.element('pc-cap').value = '200000'; f.element('pc-cap').dispatch('input');
  const after = f.ranges.at(-1);
  assert.equal(after.endAsset, first.endAsset * 2);
  assert.equal(f.element('pc-end').textContent, 'NT$660,000');
  assert.equal(f.element('pc-profit').textContent, 'NT$460,000');
  const asset = f.chartCalls[0].options.at(-1).series[0].data;
  assert.equal(asset[0][1], 200000); assert.equal(asset.at(-1)[1], 660000);
  assert.deepEqual(f.chartCalls[1].options.at(-1).series[0].data, beforeDD);
  assert.deepEqual([f.element('pc-ret').textContent, f.element('pc-cagr').textContent, f.element('pc-mdd').textContent], beforePercent);
  assert.equal(f.element('cap').value, '100037');
  assert.equal(f.element('cap-previous').value, '100000');
});

test('changing either year redraws both charts and sends the same range to the monthly-table callback', () => {
  const f = fixture();
  assert.deepEqual(f.element('pc-y0').children.map(x => Number(x.value)), [2023, 2024, 2025]);
  f.element('pc-y0').value = '2024'; f.element('pc-y0').dispatch('change');
  f.element('pc-y1').value = '2024'; f.element('pc-y1').dispatch('change');
  const current = f.ranges.at(-1);
  assert.equal(current.startYear, 2024); assert.equal(current.endYear, 2024);
  assert.equal(f.element('pc-end').textContent, 'NT$110,000');
  for (const chart of f.chartCalls) {
    const option = chart.options.at(-1);
    assert.equal(option.xAxis.min, '2023-12-29');
    assert.equal(option.xAxis.max, '2024-12-31');
    assert.equal(option.series[0].data.at(-1)[0], '2024-12-31');
  }
  assert.match(f.element('pc-chart-period').textContent, /2023-12-29.*2024-12-31/);
  assert.equal(f.ranges.length, 3);
});

test('return-view chart remains a percentage curve when capital changes', () => {
  const f = fixture();
  f.element('pc-view').value = 'return'; f.element('pc-view').dispatch('change');
  const before = f.chartCalls[0].options.at(-1).series[0].data;
  assert.equal(f.chartCalls[0].options.at(-1).series[0].name, '累積報酬');
  f.element('pc-cap').value = '200000'; f.element('pc-cap').dispatch('input');
  assert.deepEqual(f.chartCalls[0].options.at(-1).series[0].data, before);
  assert.equal(f.element('pc-end').textContent, 'NT$660,000');
});

test('invalid input clears stale values and charts, then a valid edit recreates both charts', () => {
  const f = fixture();
  f.element('pc-y0').value = '2025'; f.element('pc-y1').value = '2024'; f.element('pc-y0').dispatch('change');
  for (const id of ['pc-start', 'pc-ret', 'pc-profit', 'pc-end', 'pc-cagr', 'pc-mdd']) assert.equal(f.element(id).textContent, '—');
  assert.match(f.element('pc-range').textContent, /年份/);
  assert.ok(f.chartCalls.slice(0, 2).every(c => c.disposed));
  assert.match(f.element('chart-nav').textContent, /有效/);
  assert.match(f.element('chart-dd').textContent, /有效/);
  f.element('pc-y1').value = '2025'; f.element('pc-y1').dispatch('change');
  assert.equal(f.chartCalls.length, 4);
  assert.equal(f.element('pc-end').textContent, 'NT$150,000');
  assert.equal(f.chartCalls[2].options.at(-1).series[0].data.at(-1)[1], 150000);
  assert.equal(f.chartCalls[3].options.at(-1).series[0].data.at(-1)[0], '2025-12-31');
});

for (const library of ['missing', 'throwing', 'partial', 'draw-throwing']) {
  test(`${library} chart library leaves calculator cards and callbacks usable`, () => {
    let f;
    assert.doesNotThrow(() => { f = fixture({library}); });
    assert.match(f.element('chart-nav').textContent, /股數試算仍可使用/);
    assert.match(f.element('chart-dd').textContent, /股數試算仍可使用/);
    f.element('pc-cap').value = '200000';
    assert.doesNotThrow(() => f.element('pc-cap').dispatch('input'));
    assert.equal(f.element('pc-end').textContent, 'NT$660,000');
    assert.equal(f.ranges.at(-1).capital, 200000);
  });
}
