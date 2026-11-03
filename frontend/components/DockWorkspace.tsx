"use client";

// Bài 02 - Màn hình giờ nhận xe ở cửa kho.
// Luồng: (1) nhập email -> GenAI đọc  (2) xem/sửa -> code tìm giờ -> đặt lịch  (3) lịch các cửa trong ngày.
import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, CalendarClock, CalendarDays, CheckCircle2, Hash, Loader2, RefreshCw, Search, Send, Sparkles, Truck } from "lucide-react";
import clsx from "clsx";
import { api, ApiError } from "@/lib/api";
import { clock, gmtLabel, nowVietnamIso } from "@/lib/format";
import type {
  BookingOut, DockExtractResponse, DockMissingField, DockRequestExtraction, DockSample, DockType, Issue, ScheduleOut, SlotOut,
} from "@/lib/types";
import { Empty, FieldIssues, JsonBox, Metric, PanelHeader } from "./ui";

const DOCK_TYPES: DockType[] = ["standard", "refrigerated"];
const FORM_FIELDS = ["carrier", "earliest_start", "duration_minutes", "dock_type"];
const SEED_DAY = "2026-10-21"; // ngày có lịch mẫu trong đề

/** Bản người dùng đang sửa. Giờ dạng "YYYY-MM-DDTHH:MM" theo giờ kho (ô datetime-local). */
interface Draft {
  carrier: string;
  earliest_start: string;
  duration_minutes: string;
  dock_type: string;
}

function toDraft(data: DockExtractResponse): Draft {
  // Ưu tiên bản đã kiểm tra (giờ đã đổi về giờ kho). GenAI trả sai khuôn thì lấy JSON gốc để sửa.
  const source = (data.extraction ?? data.llm_output ?? {}) as Record<string, unknown>;
  const text = (value: unknown) => (value === null || value === undefined ? "" : String(value));
  return {
    carrier: text(source.carrier),
    earliest_start: text(source.earliest_start).slice(0, 16),
    duration_minutes: text(source.duration_minutes),
    dock_type: text(source.dock_type) || "standard",
  };
}

function toRequest(draft: Draft, requestId: string): DockRequestExtraction {
  const carrier = draft.carrier.trim() || null;
  // Không gửi múi giờ: backend hiểu là giờ kho (APP_TIMEZONE)
  const earliest = draft.earliest_start ? `${draft.earliest_start}:00` : null;
  const duration = draft.duration_minutes.trim() === "" ? null : Number(draft.duration_minutes);
  const missing: DockMissingField[] = [];
  if (carrier === null) missing.push("carrier");
  if (earliest === null) missing.push("earliest_start");
  if (duration === null) missing.push("duration_minutes");
  return {
    request_id: requestId,
    carrier,
    earliest_start: earliest,
    duration_minutes: duration,
    dock_type: draft.dock_type as DockType,
    missing_fields: missing,
  };
}

const errorIssues = (error: unknown): Issue[] => {
  const err = error as ApiError;
  return err.issues?.length ? err.issues : [{ field: "(toàn bộ)", message: err.message, level: "error" }];
};

export default function DockWorkspace({ llmMode }: { llmMode: string }) {
  // Bước 1: nhập
  const [samples, setSamples] = useState<DockSample[]>([]);
  const [email, setEmail] = useState("");
  const [receivedAt, setReceivedAt] = useState(nowVietnamIso());
  const [inputIssues, setInputIssues] = useState<Issue[]>([]);
  const [extracting, setExtracting] = useState(false);

  // Bước 2: xem/sửa, tìm giờ, đặt lịch
  const [extraction, setExtraction] = useState<DockExtractResponse | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [slot, setSlot] = useState<SlotOut | null>(null);
  const [conflict, setConflict] = useState("");
  const [busy, setBusy] = useState<"suggest" | "confirm" | null>(null);
  const [booking, setBooking] = useState<BookingOut | null>(null);

  // Bước 3: lịch trong ngày
  const [day, setDay] = useState(SEED_DAY);
  const [schedule, setSchedule] = useState<ScheduleOut | null>(null);
  const [scheduleError, setScheduleError] = useState("");

  const loadSchedule = useCallback(async () => {
    try {
      setSchedule(await api.dockSchedule(day));
      setScheduleError("");
    } catch (error) {
      setScheduleError(error instanceof Error ? error.message : "Không tải được lịch");
    }
  }, [day]);

  useEffect(() => { api.dockSamples().then(setSamples).catch(() => setSamples([])); }, []);
  useEffect(() => { loadSchedule(); }, [loadSchedule]);

  const reset = () => {
    setExtraction(null);
    setDraft(null);
    setIssues([]);
    setSlot(null);
    setConflict("");
    setBooking(null);
  };

  const pickSample = (sample: DockSample) => {
    setEmail(sample.email);
    setReceivedAt(sample.received_at);
    setInputIssues([]);
    reset();
  };

  const runExtract = async () => {
    setExtracting(true);
    setInputIssues([]);
    reset();
    try {
      const result = await api.dockExtract(email, receivedAt);
      const next = toDraft(result);
      setExtraction(result);
      setDraft(next);
      setIssues(result.issues);
      if (next.earliest_start) setDay(next.earliest_start.slice(0, 10)); // xem lịch đúng ngày xe đến
    } catch (error) {
      setInputIssues(errorIssues(error));
    } finally {
      setExtracting(false);
    }
  };

  const runSuggest = async () => {
    if (!extraction?.run_id || !draft) return;
    setBusy("suggest");
    setConflict("");
    try {
      const result = await api.dockSuggest(extraction.run_id, toRequest(draft, extraction.request_id));
      setSlot(result);
      setIssues([]);
      setDay(draft.earliest_start.slice(0, 10));
    } catch (error) {
      setSlot(null);
      setIssues(errorIssues(error));
    } finally {
      setBusy(null);
    }
  };

  const runConfirm = async () => {
    if (!extraction?.run_id || !draft || !slot?.dock_id || !slot.start) return;
    setBusy("confirm");
    setConflict("");
    try {
      setBooking(await api.dockConfirm(extraction.run_id, toRequest(draft, extraction.request_id), slot.dock_id, slot.start));
      setSlot(null);
    } catch (error) {
      const err = error as ApiError;
      const suggestion = (err.detail as { suggestion?: SlotOut } | null)?.suggestion;
      if (err.status === 409 && suggestion) {
        // Có người vừa đặt giờ này: hiện gợi ý mới tính trên lịch mới nhất
        setConflict(err.message);
        setSlot(suggestion);
      } else {
        setIssues(errorIssues(error));
      }
    } finally {
      setBusy(null);
      await loadSchedule();
    }
  };

  // Sửa bất kỳ ô nào thì gợi ý cũ không còn đúng
  const update = (patch: Partial<Draft>) => {
    setDraft((d) => (d ? { ...d, ...patch } : d));
    setSlot(null);
    setConflict("");
  };

  const original = useMemo(() => (extraction ? toDraft(extraction) : null), [extraction]);
  const edited = (field: keyof Draft) => Boolean(original && draft && original[field] !== draft[field]);
  const issuesFor = (field: string) => issues.filter((i) => i.field === field);
  const generalIssues = issues.filter((i) => !FORM_FIELDS.includes(i.field));
  const tz = schedule?.timezone ?? "GMT+7";
  const bookingCount = schedule?.docks.reduce((n, d) => n + d.bookings.length, 0) ?? 0;

  return (
    <div className="page-stack">
      <section className="metric-grid">
        <Metric icon={Hash} label="Mã yêu cầu" value={extraction?.request_id ?? "—"} hint="backend cấp" tone="violet" />
        <Metric icon={Search} label="Gợi ý" value={slot?.status === "ok" && slot.start ? `${slot.dock_id} · ${clock(slot.start)}` : "—"} hint={slot?.start ? gmtLabel(slot.start) : undefined} tone="blue" />
        <Metric icon={CalendarDays} label="Lịch trong ngày" value={String(bookingCount)} hint={schedule?.date} tone="amber" />
        <Metric icon={CheckCircle2} label="Vừa đặt" value={booking ? `${booking.dock_id} · ${clock(booking.start)}` : "—"} hint={booking ? gmtLabel(booking.start) : undefined} tone="green" />
      </section>

      <div className="work-grid">
        {/* Bước 1 */}
        <section className="panel">
          <PanelHeader step={1} title="Nhập email đặt lịch" subtitle="Backend tự cấp mã yêu cầu. GenAI chỉ đọc email, không chọn cửa." />
          <div className="panel-body">
            {samples.length > 0 && (
              <div className="field">
                <label>Email mẫu {llmMode === "mock" && <em>chế độ mock chỉ đọc được các mẫu này</em>}</label>
                <div className="suggestions">
                  {samples.map((s) => (
                    <button key={s.label} title={s.note} onClick={() => pickSample(s)} className={clsx(s.label.includes("[Lỗi]") && "bad")}>{s.label}</button>
                  ))}
                </div>
              </div>
            )}
            <div className="field">
              <label>Email nhận lúc <em>để GenAI hiểu “sáng mai”, “thứ Tư này”. ISO có múi giờ</em></label>
              <input value={receivedAt} onChange={(e) => setReceivedAt(e.target.value)} placeholder="2026-10-20T16:00:00+07:00" />
              <FieldIssues issues={inputIssues.filter((i) => i.field === "received_at")} />
            </div>
            <div className="field">
              <label>Email của nhà vận chuyển</label>
              <textarea rows={6} value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Ví dụ: Xe của ACME đến lúc 9 giờ sáng 21/10, cần khoảng một tiếng để dỡ hàng..." />
              <FieldIssues issues={inputIssues.filter((i) => i.field !== "received_at")} />
            </div>
            <button className="primary-button" onClick={runExtract} disabled={extracting || !email.trim()}>
              {extracting ? <Loader2 className="spin" size={16} /> : <Sparkles size={16} />} GenAI đọc email
            </button>
          </div>
        </section>

        {/* Bước 2 */}
        <section className="panel">
          <PanelHeader step={2} title="Xem & sửa, rồi tìm giờ" subtitle="Code kiểm tra lại. Thiếu thời lượng thì phải nhập, hệ thống không đoán." />
          <div className="panel-body">
            {!draft || !extraction ? (
              <Empty icon={Sparkles} text="Chưa có kết quả. Nhập email rồi bấm “GenAI đọc email”." />
            ) : (
              <>
                <div className="run-meta">
                  <span>run #{extraction.run_id}</span>
                  <span>mã <strong>{extraction.request_id}</strong></span>
                  <span className={clsx("pill", extraction.can_suggest ? "green" : "red")}>
                    {extraction.can_suggest ? "Đủ thông tin" : "Cần sửa trước khi tìm giờ"}
                  </span>
                </div>
                <FieldIssues issues={generalIssues} />

                <div className="form-row">
                  <Field label="carrier (để trống = null)" edited={edited("carrier")} issues={issuesFor("carrier")}>
                    <input value={draft.carrier} onChange={(e) => update({ carrier: e.target.value })} placeholder="null" disabled={!!booking} />
                  </Field>
                  <Field label="dock_type" edited={edited("dock_type")} issues={issuesFor("dock_type")}>
                    <select value={draft.dock_type} onChange={(e) => update({ dock_type: e.target.value })} disabled={!!booking}>
                      {!DOCK_TYPES.includes(draft.dock_type as DockType) && <option value={draft.dock_type}>{draft.dock_type} (không hợp lệ)</option>}
                      {DOCK_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                    </select>
                  </Field>
                </div>
                <div className="form-row">
                  <Field label={`earliest_start (giờ kho, ${tz})`} edited={edited("earliest_start")} issues={issuesFor("earliest_start")}>
                    <input type="datetime-local" value={draft.earliest_start} onChange={(e) => update({ earliest_start: e.target.value })} disabled={!!booking} />
                  </Field>
                  <Field label="duration_minutes (phút)" edited={edited("duration_minutes")} issues={issuesFor("duration_minutes")}>
                    <input type="number" min={1} step={5} value={draft.duration_minutes} onChange={(e) => update({ duration_minutes: e.target.value })} placeholder="null - hỏi lại nhà vận chuyển" disabled={!!booking} />
                  </Field>
                </div>

                <details className="raw-json">
                  <summary>JSON gốc GenAI trả về</summary>
                  <JsonBox title="llm_output" value={extraction.llm_output} />
                </details>

                {booking ? (
                  <div className="success-card">
                    <CheckCircle2 size={18} />
                    <div>
                      <strong>Đã đặt {booking.request_id}: Cửa {booking.dock_id} {clock(booking.start)}–{clock(booking.end)} ({gmtLabel(booking.start)})</strong>
                      <span>
                        Ngày {booking.start.slice(0, 10)}{booking.carrier ? ` · ${booking.carrier}` : ""}.
                        {booking.cleanup_minutes > 0 && ` Cửa còn bận tới ${clock(addMinutes(booking.end, booking.cleanup_minutes))} để dọn.`}
                        {booking.edited_fields.length > 0 && ` Đã sửa: ${booking.edited_fields.join(", ")}.`}
                      </span>
                    </div>
                  </div>
                ) : (
                  <>
                    {conflict && (
                      <div className="conflict-card">
                        <AlertTriangle size={18} />
                        <div><strong>Giờ vừa chọn đã có người đặt</strong><span>{conflict}. Dưới đây là gợi ý mới tính trên lịch mới nhất.</span></div>
                      </div>
                    )}
                    {slot && <SlotCard slot={slot} />}
                    <div className="toolbar">
                      <button className={slot?.status === "ok" ? "secondary-button" : "primary-button"} onClick={runSuggest} disabled={busy !== null}>
                        {busy === "suggest" ? <Loader2 className="spin" size={16} /> : <Search size={16} />} {slot ? "Tìm lại" : "Tìm giờ sớm nhất"}
                      </button>
                      {slot?.status === "ok" && (
                        <button className="primary-button" onClick={runConfirm} disabled={busy !== null}>
                          {busy === "confirm" ? <Loader2 className="spin" size={16} /> : <Send size={16} />} Đặt Cửa {slot.dock_id} lúc {clock(slot.start!)} ({gmtLabel(slot.start!)})
                        </button>
                      )}
                    </div>
                  </>
                )}
              </>
            )}
          </div>
        </section>
      </div>

      {/* Bước 3 */}
      <section className="panel">
        <PanelHeader
          step={3}
          title={`Lịch các cửa ngày ${schedule?.date ?? day} (${tz})`}
          subtitle={
            schedule?.cleanup_minutes
              ? `Sau mỗi xe cửa cần ${schedule.cleanup_minutes} phút dọn (ô sọc): xe kết thúc 10:00 thì xe sau vào được từ ${clock(addMinutes("2000-01-01T10:00:00", schedule.cleanup_minutes))}. Ô nét đứt là gợi ý chưa đặt.`
              : "Khoảng [bắt đầu, kết thúc): xe kết thúc 10:00 thì xe khác vào được đúng 10:00. Ô nét đứt là gợi ý chưa đặt."
          }
          action={
            <div className="toolbar">
              <label>Ngày <input type="date" value={day} onChange={(e) => e.target.value && setDay(e.target.value)} /></label>
              <button className="secondary-button" onClick={loadSchedule}><RefreshCw size={14} /> Làm mới</button>
            </div>
          }
        />
        {scheduleError && <p className="queue-message">{scheduleError}</p>}
        {!schedule ? <Empty icon={CalendarClock} text="Đang tải lịch…" /> : <Timeline schedule={schedule} slot={slot} />}
      </section>
    </div>
  );
}

function SlotCard({ slot }: { slot: SlotOut }) {
  if (slot.status !== "ok" || !slot.start || !slot.end) {
    return (
      <div className="conflict-card">
        <AlertTriangle size={18} />
        <div><strong>Không còn giờ trống trong ngày</strong><span>{slot.reason}</span></div>
      </div>
    );
  }
  return (
    <div className="slot-card">
      <Truck size={18} />
      <div>
        <strong>Gợi ý: Cửa {slot.dock_id} · {clock(slot.start)}–{clock(slot.end)} ({gmtLabel(slot.start)})</strong>
        <span>{slot.reason}</span>
        <ul>
          {slot.checks.map((c) => (
            <li key={c.dock_id} className={clsx(c.dock_id === slot.dock_id && "chosen")}>
              <b>{c.dock_id}</b> bận {c.busy.length ? c.busy.map((b) => `${clock(b.start)}–${clock(b.end)}`).join(", ") : "không có lịch"} · trống sớm nhất {c.free_from ? clock(c.free_from) : "hết chỗ"}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/** Đổi "…T10:15:00+07:00" thành số phút trong ngày. Giờ backend trả đã ở múi giờ kho. */
const minutesOf = (iso: string) => {
  const [h, m] = clock(iso).split(":").map(Number);
  return h * 60 + m;
};

/** Cộng thêm số phút, giữ nguyên phần ngày và múi giờ: "…T10:15:00+07:00" + 15 -> "…T10:30:00+07:00". */
function addMinutes(iso: string, minutes: number): string {
  const total = minutesOf(iso) + minutes;
  const hhmm = `${String(Math.floor(total / 60) % 24).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
  return iso.slice(0, 11) + hhmm + iso.slice(16);
}

function Timeline({ schedule, slot }: { schedule: ScheduleOut; slot: SlotOut | null }) {
  const open = minutesOf(schedule.open_at);
  const close = minutesOf(schedule.close_at);
  const span = close - open;
  const pos = (start: string, end: string) => {
    // Lịch tràn ngoài giờ mở cửa thì cắt cho vừa khung
    const s = Math.max(open, minutesOf(start));
    const e = Math.min(close, end.slice(0, 10) > start.slice(0, 10) ? 24 * 60 : minutesOf(end));
    return { left: `${((s - open) / span) * 100}%`, width: `${(Math.max(e - s, 0) / span) * 100}%` };
  };
  // Đoạn dọn cửa ngay sau giờ kết thúc, cắt ở giờ đóng cửa
  const cleanupPos = (end: string) => {
    const s = minutesOf(end);
    const e = Math.min(close, s + schedule.cleanup_minutes);
    return e > s ? { left: `${((s - open) / span) * 100}%`, width: `${((e - s) / span) * 100}%` } : null;
  };
  const cleanupBlock = (end: string, key: string) => {
    const style = schedule.cleanup_minutes > 0 ? cleanupPos(end) : null;
    return style && <div key={key} className="block cleanup" style={style} title={`Dọn cửa ${schedule.cleanup_minutes} phút`} />;
  };
  const hours = Array.from({ length: Math.floor(span / 60) + 1 }, (_, i) => open + i * 60);
  const showSlot = slot?.status === "ok" && slot.start && slot.end && slot.start.slice(0, 10) === schedule.date;

  return (
    <div className="timeline">
      <div className="timeline-row timeline-axis">
        <div className="timeline-label">{schedule.timezone}</div>
        <div className="timeline-track">
          {hours.map((m) => (
            <span key={m} style={{ left: `${((m - open) / span) * 100}%` }}>{String(Math.floor(m / 60)).padStart(2, "0")}:00</span>
          ))}
        </div>
      </div>
      {schedule.docks.map((dock) => (
        <div className="timeline-row" key={dock.dock_id}>
          <div className="timeline-label"><strong>Cửa {dock.dock_id}</strong><small>{dock.dock_type}</small></div>
          <div className="timeline-track">
            {hours.map((m) => <i key={m} style={{ left: `${((m - open) / span) * 100}%` }} />)}
            {dock.bookings.map((b, i) => cleanupBlock(b.end, `c${i}`))}
            {dock.bookings.map((b, i) => (
              <div
                key={i}
                className={clsx("block", b.request_id ? "booked" : "seed")}
                style={pos(b.start, b.end)}
                title={`${clock(b.start)}–${clock(b.end)} ${gmtLabel(b.start)} · ${b.request_id ?? "lịch có sẵn"}${b.carrier ? ` · ${b.carrier}` : ""}`}
              >
                <span>{b.request_id ?? `${clock(b.start)}–${clock(b.end)}`}</span>
              </div>
            ))}
            {showSlot && slot!.dock_id === dock.dock_id && cleanupBlock(slot!.end!, "c-slot")}
            {showSlot && slot!.dock_id === dock.dock_id && (
              <div className="block suggested" style={pos(slot!.start!, slot!.end!)} title={`Gợi ý ${clock(slot!.start!)}–${clock(slot!.end!)}`}>
                <span>gợi ý {clock(slot!.start!)}</span>
              </div>
            )}
          </div>
        </div>
      ))}
      <div className="timeline-legend">
        <span><i className="seed" /> lịch có sẵn</span>
        <span><i className="booked" /> đã đặt qua app</span>
        <span><i className="suggested" /> gợi ý chưa đặt</span>
        {schedule.cleanup_minutes > 0 && <span><i className="cleanup" /> dọn cửa {schedule.cleanup_minutes} phút</span>}
        <span className="timeline-tz">Giờ mở cửa {clock(schedule.open_at)}–{clock(schedule.close_at)} ({schedule.timezone})</span>
      </div>
    </div>
  );
}

function Field({ label, edited, issues, children }: { label: string; edited: boolean; issues: Issue[]; children: React.ReactNode }) {
  return (
    <div className={clsx("field", issues.some((i) => i.level === "error") && "has-error")}>
      <label>{label} {edited && <em className="edited">đã sửa</em>}</label>
      {children}
      <FieldIssues issues={issues} />
    </div>
  );
}
