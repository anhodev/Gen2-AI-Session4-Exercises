// Hiển thị thời gian đúng như backend trả (đã ở múi giờ APP_TIMEZONE),
// không để trình duyệt tự đổi sang múi giờ máy người xem.

/** "2026-10-21T09:50:00+07:00" -> "21/10 09:50" */
export function shortTime(iso: string): string {
  const match = iso.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
  if (!match) return iso;
  const [, , month, day, hour, minute] = match;
  return `${day}/${month} ${hour}:${minute}`;
}

/** "2026-10-21T09:50:00+07:00" -> "+07:00" */
export function offsetOf(iso: string): string {
  const match = iso.match(/([+-]\d{2}:\d{2}|Z)$/);
  return match ? match[1] : "";
}

/** Thời điểm hiện tại theo giờ Việt Nam, dạng ISO có múi giờ. */
export function nowVietnamIso(): string {
  const vn = new Date(Date.now() + 7 * 60 * 60 * 1000);
  return `${vn.toISOString().slice(0, 19)}+07:00`;
}

export function isOverdue(iso: string): boolean {
  const time = Date.parse(iso);
  return !Number.isNaN(time) && time < Date.now();
}
