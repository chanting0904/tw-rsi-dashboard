(function (root) {
  'use strict';

  function calculateRange(perf, capital, startYear, endYear) {
    if (!Number.isFinite(capital) || capital <= 0 || capital > Number.MAX_SAFE_INTEGER)
      throw Error('請輸入大於 0 的有效本金。');
    if (!Number.isInteger(startYear) || !Number.isInteger(endYear) || startYear > endYear)
      throw Error('起始年份不能晚於結束年份。');
    const nav = perf.nav || [];
    if (!nav.length) throw Error('尚無歷史績效資料。');
    let previousDate = '';
    for (const [date, value] of nav) {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || date <= previousDate || !Number.isFinite(value) || value <= 0)
        throw Error('歷史資產資料不完整，暫停試算。');
      previousDate = date;
    }
    const first = nav.findIndex(row => Number(row[0].slice(0, 4)) >= startYear);
    let last = nav.length - 1;
    while (last >= 0 && Number(nav[last][0].slice(0, 4)) > endYear) last--;
    if (first < 0 || last < first) throw Error('所選年份沒有回測資料。');
    const basis = first > 0 ? nav[first - 1][1] : Number(perf.start_capital);
    if (!(basis > 0) || !Number.isFinite(basis)) throw Error('缺少有效的回測期初本金。');
    const basisDate = first > 0 ? nav[first - 1][0] : nav[first][0];
    const endDate = nav[last][0];
    const years = (Date.parse(endDate) - Date.parse(basisDate)) / 86400000 / 365.25;
    const growth = nav[last][1] / basis;
    const endAsset = capital * growth;
    if (!Number.isFinite(endAsset)) throw Error('本金超出可計算範圍。');
    const navSeries = [[basisDate, capital]], ddSeries = [[basisDate, 0]], returnSeries = [[basisDate, 0]];
    let peak = basis, mdd = 0;
    for (let i = first; i <= last; i++) {
      const [date, value] = nav[i];
      peak = Math.max(peak, value);
      const dd = value / peak - 1;
      mdd = Math.min(mdd, dd);
      navSeries.push([date, capital * value / basis]);
      ddSeries.push([date, dd * 100]);
      returnSeries.push([date, (value / basis - 1) * 100]);
    }
    return {capital, startYear, endYear, basisDate, endDate, years, growth, endAsset,
      returnRate: growth - 1, cagr: years > 0 ? growth ** (1 / years) - 1 : 0,
      mdd, navSeries, ddSeries, returnSeries, observations: last - first + 1};
  }

  function init({perf, document: doc = root.document, charts = root.echarts, onRange = () => {}}) {
    const $ = id => doc.getElementById(id);
    const money = value => 'NT$' + Math.round(value).toLocaleString('zh-TW');
    const percent = value => (value * 100).toFixed(2) + '%';
    const years = [...new Set((perf.nav || []).map(row => Number(row[0].slice(0, 4))))].sort((a, b) => a - b);
    const chartIds = ['chart-nav', 'chart-dd'];
    const instances = [];
    let result = null;
    const text = (id, value) => { $(id).textContent = value; };
    const colored = (id, value, number) => {
      text(id, value); $(id).className = 'v ' + (number >= 0 ? 'up' : 'down');
    };
    function clearCharts(message) {
      for (const instance of instances) instance.dispose();
      instances.length = 0;
      for (const id of chartIds) text(id, message);
    }
    function draw() {
      if (!result) return;
      if (!charts) {
        clearCharts('圖表暫時無法載入；上方績效數字、買賣清單與股數試算仍可使用。');
        return;
      }
      try {
        if (!instances.length) {
          for (const id of chartIds) {
            text(id, ''); instances.push(charts.init($(id)));
          }
          instances.forEach(instance => { instance.group = 'v5-performance'; });
          charts.connect('v5-performance');
        }
        const returns = $('pc-view').value === 'return';
        const axisColor = '#91a5c4';
        const base = {
          animation: false, backgroundColor: 'transparent',
          grid: {left: 12, right: 24, top: 20, bottom: 12, containLabel: true},
          tooltip: {trigger: 'axis', confine: true, backgroundColor: '#18263b', borderColor: '#405876',
            textStyle: {color: '#edf4ff'}, axisPointer: {type: 'line', lineStyle: {color: '#a1bde0', type: 'dashed'}}},
          xAxis: {type: 'time', boundaryGap: false, min: result.basisDate, max: result.endDate,
            axisLine: {lineStyle: {color: '#34465e'}}, axisTick: {show: false},
            axisLabel: {color: axisColor, hideOverlap: true}},
          yAxis: {type: 'value', axisLabel: {color: axisColor}, splitNumber: 4,
            splitLine: {lineStyle: {color: '#34465e', type: 'dashed', opacity: .5}}}
        };
        const compact = v => Math.abs(v) >= 100000000 ? (v / 100000000).toFixed(1) + '億'
          : Math.abs(v) >= 10000 ? (v / 10000).toFixed(1).replace(/\.0$/, '') + '萬' : Math.round(v).toLocaleString('zh-TW');
        instances[0].setOption({...base,
          tooltip: {...base.tooltip, valueFormatter: v => returns ? Number(v).toFixed(2) + '%' : money(Number(v))},
          yAxis: {...base.yAxis, axisLabel: {color: axisColor, formatter: v => returns ? Number(v).toLocaleString('zh-TW', {maximumFractionDigits: 2}) + '%' : compact(v)}},
          series: [{name: returns ? '累積報酬' : '模擬資產', type: 'line', smooth: false,
            data: returns ? result.returnSeries : result.navSeries, showSymbol: false,
            lineStyle: {color: '#78b5ff', width: 2},
            areaStyle: {color: {type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [{offset: 0, color: 'rgba(91,165,255,.25)'}, {offset: 1, color: 'rgba(91,165,255,.01)'}]}}}]
        }, true);
        instances[1].setOption({...base,
          tooltip: {...base.tooltip, valueFormatter: v => Number(v).toFixed(2) + '%'},
          yAxis: {...base.yAxis, max: 0, axisLabel: {color: axisColor, formatter: v => Number(v).toFixed(0) + '%'}},
          series: [{name: '距離區間高點', type: 'line', smooth: false, data: result.ddSeries,
            showSymbol: false, lineStyle: {color: '#62cdb0', width: 1.5}, areaStyle: {color: 'rgba(98,205,176,.16)'}}]
        }, true);
      } catch (_) {
        clearCharts('圖表暫時無法載入；上方績效數字、買賣清單與股數試算仍可使用。');
      }
    }
    function update() {
      try {
        result = calculateRange(perf, Number($('pc-cap').value), Number($('pc-y0').value), Number($('pc-y1').value));
      } catch (error) {
        result = null;
        for (const id of ['pc-start', 'pc-ret', 'pc-profit', 'pc-end', 'pc-cagr', 'pc-mdd']) text(id, '—');
        for (const id of ['pc-start-date', 'pc-end-date', 'pc-years', 'pc-chart-period', 'pc-chart-end']) text(id, '');
        text('pc-range', error.message); $('pc-range').className = 'perf-status invalid';
        clearCharts('請先完成有效的本金與年份設定。');
        onRange(null);
        return;
      }
      text('pc-start', money(result.capital));
      text('pc-start-date', '計算起點 ' + result.basisDate);
      colored('pc-ret', percent(result.returnRate), result.returnRate);
      text('pc-years', '期間 ' + result.years.toFixed(1) + ' 年');
      colored('pc-profit', money(result.endAsset - result.capital), result.returnRate);
      text('pc-end', money(result.endAsset)); text('pc-end-date', '截至 ' + result.endDate);
      colored('pc-cagr', percent(result.cagr), result.cagr); text('pc-mdd', percent(result.mdd));
      text('pc-range', result.startYear + '～' + result.endYear + ' 年｜計算起點 ' + result.basisDate + ' → ' + result.endDate
        + '｜' + result.observations.toLocaleString('zh-TW') + ' 筆每日資料');
      $('pc-range').className = 'perf-status';
      text('pc-chart-period', result.basisDate + ' — ' + result.endDate);
      text('pc-chart-end', '期末 ' + money(result.endAsset));
      onRange(result);
      draw();
    }
    for (const id of ['pc-y0', 'pc-y1']) {
      for (const year of years) {
        const option = doc.createElement('option'); option.value = year; option.textContent = year + '年';
        $(id).appendChild(option);
      }
      $(id).addEventListener('change', update);
    }
    $('pc-cap').addEventListener('input', update);
    $('pc-apply').addEventListener('click', update);
    $('pc-demo').addEventListener('click', () => {
      $('pc-y0').value = years[0] ?? ''; $('pc-y1').value = years.at(-1) ?? ''; update();
    });
    $('pc-view').addEventListener('change', draw);
    $('pc-y0').value = years[0] ?? ''; $('pc-y1').value = years.at(-1) ?? '';
    $('pc-cap').value = 100000;
    if (root.addEventListener) root.addEventListener('resize', () => instances.forEach(instance => instance.resize()));
    if (root.ResizeObserver) {
      const observer = new root.ResizeObserver(() => instances.forEach(instance => instance.resize()));
      chartIds.forEach(id => observer.observe($(id)));
    }
    update();
    return {update};
  }

  const api = {calculateRange, init};
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.HistoricalPerformance = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
