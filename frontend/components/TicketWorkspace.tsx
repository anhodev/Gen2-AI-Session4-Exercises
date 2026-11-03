"use client";

// Bài 01 - Màn hình hàng đợi ticket.
// Luồng: (1) nhập tin nhắn -> GenAI đọc  (2) xem/sửa JSON -> xác nhận  (3) hàng đợi do code xếp.
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CheckCircle2, Clock3, Hourglass, Inbox, ListOrdered, Loader2, PlayCircle, RefreshCw, Send, Sparkles, UserRoundCheck,
} from "lucide-react";
import clsx from "clsx";
import { api, ApiError } from "@/lib/api";
import { dayTime, gmtLabel, isOverdue, nowVietnamIso, shortTime } from "@/lib/format";
import type { ConfirmResponse, ExtractResponse, Intent, Issue, QueueItem, QueueResponse, Sample, Severity } from "@/lib/types";
import { Empty, FieldIssues, JsonBox, Metric, PanelHeader, SeverityPill } from "./ui";

const INTENTS: Intent[] = ["billing", "technical", "account", "other"];
const SEVERITIES: Severity[] = ["critical", "high", "medium", "low"];
const SUMMARY_MAX = 200;
const FORM_FIELDS = ["ticket_id", "intent", "severity", "product", "summary", "needs_human_review"];

/**
 * Bản người dùng đang sửa. Giữ dạng chuỗi để hiện được cả giá trị sai GenAI trả (vd "urgent").
 * Không có ticket_id: mã do backend cấp, người dùng không sửa được.
 */
interface Draft {
  intent: string;
  severity: string;
  product: string;
  summary: string;
  needs_human_review: boolean;
}

function toDraft(raw: Record<string, unknown> | null): Draft {
  const text = (value: unknown) => (value === null || value === undefined ? "" : String(value));
  return {
    intent: text(raw?.intent),
    severity: text(raw?.severity),
    product: text(raw?.product),
    summary: text(raw?.summary),
    needs_human_review: Boolean(raw?.needs_human_review),
  };
}

export default function TicketWorkspace({ llmMode }: { llmMode: string }) {
  // Bước 1: nhập
  const [samples, setSamples] = useState<Sample[]>([]);
  const [input, setInput] = useState({ received_at: nowVietnamIso(), message: "" });
  const [inputIssues, setInputIssues] = useState<Issue[]>([]);
  const [extracting, setExtracting] = useState(false);

  // Bước 2: xem/sửa
  const [extraction, setExtraction] = useState<ExtractResponse | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [confirming, setConfirming] = useState(false);
  const [confirmed, setConfirmed] = useState<ConfirmResponse | null>(null);

  // Bước 3: hàng đợi
  const [queue, setQueue] = useState<QueueResponse | null>(null);
  const [limit, setLimit] = useState(5);
  const [current, setCurrent] = useState<QueueItem | null>(null);
  const [queueMessage, setQueueMessage] = useState("");
  const [popping, setPopping] = useState(false);

  const loadQueue = useCallback(async () => {
    try {
      setQueue(await api.queue(limit));
    } catch (error) {
      setQueueMessage(error instanceof Error ? error.message : "Không tải được hàng đợi");
    }
  }, [limit]);

  useEffect(() => { api.samples().then(setSamples).catch(() => setSamples([])); }, []);
  useEffect(() => { loadQueue(); }, [loadQueue]);

  const pickSample = (sample: Sample) => {
    setInput({ received_at: sample.received_at, message: sample.message });
    setInputIssues([]);
    setExtraction(null);
    setDraft(null);
    setConfirmed(null);
  };

  const runExtract = async () => {
    setExtracting(true);
    setInputIssues([]);
    setConfirmed(null);
    try {
      const result = await api.extract(input);
      setExtraction(result);
      setDraft(toDraft(result.llm_output));
      setIssues(result.issues);
    } catch (error) {
      const err = error as ApiError;
      setInputIssues(err.issues?.length ? err.issues : [{ field: "(toàn bộ)", message: err.message, level: "error" }]);
    } finally {
      setExtracting(false);
    }
  };

  const runConfirm = async () => {
    if (!extraction?.run_id || !draft) return;
    setConfirming(true);
    try {
      const result = await api.confirm(extraction.run_id, {
        ...draft,
        ticket_id: extraction.ticket_id, // luôn gửi mã hệ thống đã cấp, không lấy mã GenAI trả
        intent: draft.intent as Intent,
        severity: draft.severity as Severity,
        product: draft.product.trim() ? draft.product : null, // ô trống -> null
      });
      setConfirmed(result);
      setIssues(result.warnings);
      await loadQueue();
    } catch (error) {
      const err = error as ApiError;
      setIssues(err.issues?.length ? err.issues : [{ field: "(toàn bộ)", message: err.message, level: "error" }]);
    } finally {
      setConfirming(false);
    }
  };

  const popNext = async () => {
    setPopping(true);
    setQueueMessage("");
    try {
      setCurrent(await api.next());
      await loadQueue();
    } catch (error) {
      setQueueMessage(error instanceof Error ? error.message : "Không lấy được ticket");
    } finally {
      setPopping(false);
    }
  };

  const issuesFor = (field: string) => issues.filter((i) => i.field === field);
  const generalIssues = issues.filter((i) => !FORM_FIELDS.includes(i.field));
  const original = useMemo(() => (extraction ? toDraft(extraction.llm_output) : null), [extraction]);
  const edited = (field: keyof Draft) => Boolean(original && draft && original[field] !== draft[field]);
  const update = (patch: Partial<Draft>) => setDraft((d) => (d ? { ...d, ...patch } : d));
  const head = queue?.items[0];

  return (
    <div className="page-stack">
      <section className="metric-grid">
        <Metric icon={Inbox} label="Ticket đang chờ" value={String(queue?.total ?? 0)} hint="trong hàng đợi" tone="violet" />
        <Metric icon={ListOrdered} label="Đứng đầu hàng đợi" value={head?.ticket_id ?? "—"} hint={head?.severity} tone="blue" />
        <Metric icon={Hourglass} label="Hạn gần nhất" value={head ? dayTime(head.deadline_at) : "—"} hint={head ? [gmtLabel(head.deadline_at), isOverdue(head.deadline_at) ? "đã quá hạn" : ""].filter(Boolean).join(" · ") : undefined} tone="amber" />
        <Metric icon={UserRoundCheck} label="Vừa lấy ra xử lý" value={current?.ticket_id ?? "—"} hint={current?.severity} tone="green" />
      </section>

      <div className="work-grid">
        {/* Bước 1 */}
        <section className="panel">
          <PanelHeader step={1} title="Nhập tin nhắn" subtitle="Backend tự cấp mã ticket khi gửi. GenAI chỉ đọc nội dung." />
          <div className="panel-body">
            {samples.length > 0 && (
              <div className="field">
                <label>Tin nhắn mẫu {llmMode === "mock" && <em>chế độ mock chỉ đọc được các mẫu này</em>}</label>
                <div className="suggestions">
                  {samples.map((s) => (
                    <button key={s.label} title={s.note} onClick={() => pickSample(s)} className={clsx(s.label.includes("[Lỗi]") && "bad")}>{s.label}</button>
                  ))}
                </div>
              </div>
            )}
            <div className="form-row">
              <div className="field">
                <label>Mã ticket (backend cấp)</label>
                <input value={extraction?.ticket_id ?? ""} placeholder="Tự cấp khi bấm gửi" readOnly disabled />
              </div>
              <div className="field">
                <label>Nhận lúc (ISO, có múi giờ)</label>
                <input value={input.received_at} onChange={(e) => setInput({ ...input, received_at: e.target.value })} placeholder="2026-10-21T09:00:00+07:00" />
                <FieldIssues issues={inputIssues.filter((i) => i.field === "received_at")} />
              </div>
            </div>
            <div className="field">
              <label>Tin nhắn của khách</label>
              <textarea rows={5} value={input.message} onChange={(e) => setInput({ ...input, message: e.target.value })} placeholder="Ví dụ: Tôi bị trừ tiền hai lần khi thanh toán..." />
              <FieldIssues issues={inputIssues.filter((i) => i.field !== "received_at")} />
            </div>
            <button className="primary-button" onClick={runExtract} disabled={extracting || !input.message.trim()}>
              {extracting ? <Loader2 className="spin" size={16} /> : <Sparkles size={16} />} GenAI đọc tin nhắn
            </button>
          </div>
        </section>

        {/* Bước 2 */}
        <section className="panel">
          <PanelHeader step={2} title="Xem & sửa thông tin GenAI đọc ra" subtitle="Code kiểm tra lại. Còn lỗi thì chưa đưa vào hàng đợi." />
          <div className="panel-body">
            {!draft || !extraction ? (
              <Empty icon={Sparkles} text="Chưa có kết quả. Nhập tin nhắn rồi bấm “GenAI đọc tin nhắn”." />
            ) : (
              <>
                <div className="run-meta">
                  <span>mã <strong>{extraction.ticket_id}</strong></span>
                  <span>run #{extraction.run_id}</span>
                  <span>nhận lúc {shortTime(extraction.received_at)}</span>
                  <span className={clsx("pill", extraction.can_confirm ? "green" : "red")}>
                    {extraction.can_confirm ? "GenAI trả đúng mẫu" : "GenAI trả có lỗi"}
                  </span>
                </div>
                <FieldIssues issues={generalIssues} />

                <div className="form-row">
                  <Field label="ticket_id (backend cấp, khóa)" edited={false} issues={issuesFor("ticket_id")}>
                    <input value={extraction.ticket_id} readOnly disabled title="Mã do backend cấp. Nếu GenAI trả mã khác, hệ thống vẫn dùng mã này." />
                  </Field>
                  <Field label="intent" edited={edited("intent")} issues={issuesFor("intent")}>
                    <Select value={draft.intent} options={INTENTS} onChange={(intent) => update({ intent })} />
                  </Field>
                </div>
                <div className="form-row">
                  <Field label="severity" edited={edited("severity")} issues={issuesFor("severity")}>
                    <Select value={draft.severity} options={SEVERITIES} onChange={(severity) => update({ severity })} />
                  </Field>
                  <Field label="product (để trống = null)" edited={edited("product")} issues={issuesFor("product")}>
                    <input value={draft.product} onChange={(e) => update({ product: e.target.value })} placeholder="null" />
                  </Field>
                </div>
                <Field label={`summary (${draft.summary.length}/${SUMMARY_MAX})`} edited={edited("summary")} issues={issuesFor("summary")}>
                  <textarea rows={3} value={draft.summary} onChange={(e) => update({ summary: e.target.value })} className={clsx(draft.summary.length > SUMMARY_MAX && "too-long")} />
                </Field>
                <label className="checkbox">
                  <input type="checkbox" checked={draft.needs_human_review} onChange={(e) => update({ needs_human_review: e.target.checked })} />
                  needs_human_review {edited("needs_human_review") && <em className="edited">đã sửa</em>}
                </label>
                <FieldIssues issues={issuesFor("needs_human_review")} />

                <details className="raw-json">
                  <summary>JSON gốc GenAI trả về</summary>
                  <JsonBox title="llm_output" value={extraction.llm_output} />
                </details>

                {confirmed ? (
                  <div className="success-card">
                    <CheckCircle2 size={18} />
                    <div>
                      <strong>Đã đưa {confirmed.ticket_id} vào hàng đợi</strong>
                      <span>
                        Hạn xử lý {shortTime(confirmed.deadline_at)} = nhận lúc {shortTime(confirmed.received_at)} + SLA {confirmed.sla_minutes} phút ({confirmed.severity}).
                        {confirmed.edited_fields.length > 0 && ` Đã sửa: ${confirmed.edited_fields.join(", ")}.`}
                      </span>
                    </div>
                  </div>
                ) : (
                  <button className="primary-button" onClick={runConfirm} disabled={confirming}>
                    {confirming ? <Loader2 className="spin" size={16} /> : <Send size={16} />} Xác nhận & đưa vào hàng đợi
                  </button>
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
          title="Hàng đợi ưu tiên"
          subtitle="Code xếp bằng min-heap: hạn sớm nhất → khẩn cấp hơn → nhận trước → mã ticket. Xem top K không lấy ticket ra."
          action={
            <div className="toolbar">
              <label>Top K
                <select value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
                  {[3, 5, 10, 20, 50].map((k) => <option key={k} value={k}>{k}</option>)}
                </select>
              </label>
              <button className="secondary-button" onClick={loadQueue}><RefreshCw size={14} /> Làm mới</button>
              <button className="primary-button" onClick={popNext} disabled={popping || !queue?.total}>
                {popping ? <Loader2 className="spin" size={16} /> : <PlayCircle size={16} />} Lấy ticket kế tiếp
              </button>
            </div>
          }
        />
        {current && (
          <div className="current-card">
            <UserRoundCheck size={18} />
            <div>
              <strong>Đang xử lý: {current.ticket_id} · <SeverityPill value={current.severity} /></strong>
              <span>{current.summary}</span>
              <small>{current.reason}</small>
            </div>
          </div>
        )}
        {queueMessage && <p className="queue-message">{queueMessage}</p>}
        {!queue?.items.length ? (
          <Empty text="Hàng đợi trống. Xác nhận ticket ở bước 2 để thêm vào." />
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr><th>#</th><th>Ticket</th><th>Mức</th><th>Hạn xử lý</th><th>Tóm tắt</th><th>Lý do xếp hạng</th></tr>
              </thead>
              <tbody>
                {queue.items.map((item) => (
                  <tr key={item.ticket_id}>
                    <td className="pos">{item.position}</td>
                    <td><strong>{item.ticket_id}</strong><small>{item.intent}{item.product ? ` · ${item.product}` : ""}</small></td>
                    <td><SeverityPill value={item.severity} /></td>
                    <td>
                      <span className="deadline"><Clock3 size={13} />{shortTime(item.deadline_at)}</span>
                      <small>nhận {shortTime(item.received_at)}</small>
                      {isOverdue(item.deadline_at) && <span className="pill red">quá hạn</span>}
                    </td>
                    <td className="summary">{item.summary}</td>
                    <td className="reason">{item.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {queue.total > queue.items.length && <p className="table-note">Đang xem {queue.items.length}/{queue.total} ticket.</p>}
          </div>
        )}
      </section>
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

/** Ô chọn chỉ có giá trị hợp lệ. Nếu GenAI trả giá trị lạ, vẫn hiện để người dùng thấy và sửa. */
function Select({ value, options, onChange }: { value: string; options: string[]; onChange: (value: string) => void }) {
  const invalid = value !== "" && !options.includes(value);
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      {value === "" && <option value="">— chọn —</option>}
      {invalid && <option value={value}>{value} (không hợp lệ)</option>}
      {options.map((o) => <option key={o} value={o}>{o}</option>)}
    </select>
  );
}
