const isPaged = p => p && (p.page !== undefined || p.pageSize !== undefined);
const pageFromQuery = q => {
  const value = q && q.page;
  return typeof value === 'string' && /^[1-9]\d*$/.test(value) && Number.isSafeInteger(Number(value)) && Number.isSafeInteger((Number(value) - 1) * 50) ? Number(value) : 1;
};
function listQuery(params, fields) {
  const p = params || {};
  return fields.filter(k => p[k] !== undefined && p[k] !== '').map(k => `${k}=${encodeURIComponent(p[k])}`).join('&');
}
function decodeList(payload, key, params, identityKeys = []) {
  const paged = isPaged(params);
  const rows = key ? payload && payload[key] : payload;
  const items = paged && !key ? payload && payload.items : rows;
  if (!Array.isArray(items) || items.some(r => !r || typeof r !== 'object' || Array.isArray(r))) throw new Error('Invalid list payload');
  if (identityKeys.length && items.some(r => !identityKeys.some(k => typeof r[k] === 'string' && r[k].length))) throw new Error('Missing list identity');
  if (!paged) {
    if (payload && payload.pagination !== undefined) throw new Error('Unexpected page payload');
    return { rows: items, pagination: null };
  }
  const m = payload && payload.pagination;
  if (!m || !['page', 'pageSize', 'total', 'totalPages'].every(k => Number.isSafeInteger(m[k]))
    || m.page !== Number(params.page || 1) || m.pageSize !== Number(params.pageSize || 50)
    || m.page < 1 || m.pageSize < 1 || m.pageSize > 200 || m.total < 0
    || m.totalPages !== Math.ceil(m.total / m.pageSize) || items.length > m.pageSize || items.length > m.total
    || items.length !== Math.min(m.pageSize, Math.max(0, m.total - (m.page - 1) * m.pageSize))) throw new Error('Invalid pagination payload');
  return { rows: items, pagination: { ...m } };
}
module.exports = { isPaged, pageFromQuery, listQuery, decodeList };
