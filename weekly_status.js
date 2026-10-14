/* Shared, offline-capable weekly readiness calculation. */
function taipeiDate(now = new Date()) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit'
  }).formatToParts(now);
  const value = type => parts.find(part => part.type === type).value;
  return `${value('year')}-${value('month')}-${value('day')}`;
}

function weeklyReadiness(data, today = taipeiDate(), status = data.weekly_status || {}) {
  const day = new Date(today + 'T00:00:00Z');
  const year = today.slice(0, 4);
  if (!(data.calendar_years || []).includes(year)) {
    return {state: 'failed', title: '交易日曆尚未更新', detail: '目前無法確認本週調倉日，請等待更新。'};
  }
  const holidays = new Set(data.holidays || []);
  const iso = value => value.toISOString().slice(0, 10);
  const monday = new Date(day);
  monday.setUTCDate(day.getUTCDate() - ((day.getUTCDay() + 6) % 7));
  let expected = null;
  for (let offset = 4; offset >= 0; offset--) {
    const candidate = new Date(monday);
    candidate.setUTCDate(monday.getUTCDate() + offset);
    if (!holidays.has(iso(candidate))) { expected = iso(candidate); break; }
  }
  if (!expected) return {state: 'waiting', title: '本週休市，沒有調倉計畫', detail: '下方保留最近一期資料。'};
  let prior = new Date(expected + 'T00:00:00Z');
  do { prior.setUTCDate(prior.getUTCDate() - 1); }
  while ([0, 6].includes(prior.getUTCDay()) || holidays.has(iso(prior)));
  const basis = iso(prior);
  const current = status.rebalance_date === expected;
  const suffix = `調倉日 ${expected}｜資料基準 ${basis}。`;
  if (current && status.state === 'failed') {
    return {state: 'failed', expected, title: '本週更新失敗', detail: suffix + '本次流程尚未完成，請等待恢復確認。'};
  }
  if (current && status.state === 'retrying') {
    return {state: 'waiting', expected, title: '更新遇到問題，正在自動重試', detail: suffix + `已嘗試 ${status.attempt || 1}/${status.max_attempts || 3} 次。`};
  }
  const ready = data.rebal_date === expected && data.signal_mode === 'PRE_CLOSE_PLAN'
    && data.price_basis_date === basis && data.sig_date === basis && data.sig_validated === true;
  if (ready) return {state: 'ready', expected, title: '本週計畫已完成', detail: suffix + '最後交易日收盤執行；未成交部分下週首個交易日補單。'};
  if (current && status.state === 'ready') {
    return {state: 'waiting', expected, title: '本週計畫已更新，請重新整理', detail: suffix + '目前這個頁面仍是舊版本。'};
  }
  return {state: 'waiting', expected, title: today < expected ? '等待本週資料' : '等待本週更新完成',
    detail: suffix + `下方仍是 ${data.rebal_date || '尚無日期'} 的資料，尚非本週新計畫。`};
}

if (typeof module !== 'undefined' && module.exports) module.exports = {taipeiDate, weeklyReadiness};
