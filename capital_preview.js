/* Whole-share target configuration, not the user's brokerage cash or orders. */
function previewAllocation(targets, capital, buyFee = .0004) {
  if (!Number.isFinite(capital) || capital <= 0 || !Number.isFinite(buyFee) || buyFee < 0 || buyFee >= 1) throw new Error('Invalid capital or fee');
  const totalWeight = targets.reduce((sum, row) => sum + Number(row.target_weight ?? 0), 0);
  if (!Number.isFinite(totalWeight) || totalWeight > 1 + 1e-9) throw new Error('Target weights exceed capital');
  let cash = capital, invested = 0, fees = 0;
  const quantities = {}, unaffordable = [], missing = [], details = [];
  for (const row of targets) {
    const code = String(row.code), weight = Number(row.target_weight ?? 0), price = Number(row.price);
    if (Object.hasOwn(quantities, code) || !Number.isFinite(weight) || weight < 0) throw new Error('Invalid target list');
    let qty = 0;
    if (weight > 0) {
      if (!Number.isFinite(price) || price <= 0) missing.push(code);
      else {
        qty = Math.max(0, Math.min(Math.floor(capital * weight / price), Math.floor(Math.max(0, cash) / (price * (1 + buyFee)))));
        if (!qty) unaffordable.push(code);
        const amount = qty * price, fee = amount * buyFee;
        cash -= amount + fee;
        invested += amount;
        fees += fee;
      }
    }
    quantities[code] = qty;
    const targetAmount = capital * weight;
    const amount = qty && Number.isFinite(price) ? qty * price : 0;
    const reason = weight <= 0 ? null : (!Number.isFinite(price) || price <= 0) ? 'missing_price' :
      targetAmount < price ? 'under_one_share' : qty < Math.floor(targetAmount / price) ? 'cash_limit' : 'rounding';
    details.push({code, quantity:qty, target_amount:targetAmount, invested:amount,
                  shortfall:Math.max(0, targetAmount - amount), reason});
  }
  const nav = cash + invested;
  return {quantities, cash: Math.max(0, cash), invested, fees, nav,
          exposure: nav ? invested / nav : 0, target_weight_total:totalWeight, unaffordable, missing,
          details, shortfall:Math.max(0, capital * totalWeight - invested),
          below_band_amount:Math.max(0, .90 * nav - invested)};
}
if (typeof module !== 'undefined' && module.exports) module.exports = {previewAllocation};
