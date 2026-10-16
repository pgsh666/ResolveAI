// SQLite stores UTC without an offset; PostgreSQL retains it. Display both in shop time.
export function parseUtc(value: string) {
  return new Date(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`);
}
export function formatBusinessTime(value: string, options: Intl.DateTimeFormatOptions = {}) {
  return parseUtc(value).toLocaleString("zh-CN", {timeZone: "Asia/Shanghai", ...options});
}
