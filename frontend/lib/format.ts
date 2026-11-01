// Hiển thị thời gian đúng như backend trả (đã ở múi giờ APP_TIMEZONE, mặc định GMT+7),
// không để trình duyệt tự đổi sang múi giờ máy người xem. Luôn ghi kèm múi giờ.

/** "2026-10-21T09:50:00+07:00" -> "GMT+7"; "+05:30" -> "GMT+5:30"; "Z" hoặc "+00:00" -> "GMT+0" */
export function gmtLabel(iso: string): string {
  const match = iso.match(/([+-])(\d{2}):(\d{2})$|Z$/);
  if (!match) return "";
  if (!match[1]) return "GMT+0";
  const [, sign, hours, minutes] = match;
  return `GMT${sign}${Number(hours)}${minutes !== "00" ? `:${minutes}` : ""}`;
}

/** "2026-10-21T09:50:00+07:00" -> "21/10 09:50" (không kèm múi giờ, dùng khi múi giờ đã ghi ở chỗ khác) */
export function dayTime(iso: string): string {
  const match = iso.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
  if (!match) return iso;
  const [, , month, day, hour, minute] = match;
  return `${day}/${month} ${hour}:${minute}`;
}

/** "2026-10-21T09:50:00+07:00" -> "09:50" */
export function clock(iso: string): string {
  const match = iso.match(/T(\d{2}):(\d{2})/);
  return match ? `${match[1]}:${match[2]}` : iso;
}

/** "2026-10-21T09:50:00+07:00" -> "21/10 09:50 GMT+7" */
export function shortTime(iso: string): string {
  const label = gmtLabel(iso);
  return label ? `${dayTime(iso)} ${label}` : dayTime(iso);
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
