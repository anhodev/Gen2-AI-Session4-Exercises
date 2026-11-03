"use client";

// Bài 03 - Màn hình kiểm tra chứng từ có thể đã được gửi trước đó.
// Luồng: (1) nhập văn bản / tải PDF -> GenAI đọc  (2) xem/sửa -> code tìm trùng -> người duyệt quyết định
//        (3) danh sách chứng từ đã kiểm tra.
// Hệ thống chỉ giúp người duyệt: không tự kết luận gian lận, không tự từ chối hoàn tiền.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CheckCircle2, ClipboardCheck, Copy, FileSearch, FileText, FileUp, Hash, Loader2, RefreshCw, RotateCcw, ShieldQuestion, Sparkles, XCircle,
} from "lucide-react";
import clsx from "clsx";
import { api, ApiError } from "@/lib/api";
import type {
  CheckOut, CheckStatus, ExpenseExtraction, ExpenseExtractResponse, ExpenseMissingField, ExpenseSample, Issue, MatchOut, ReviewAction,
  ReviewStatus, SubmissionOut,
} from "@/lib/types";
import { Empty, FieldIssues, JsonBox, Metric, PanelHeader } from "./ui";

const CURRENCIES = ["VND", "USD"];
const MINOR_DIGITS: Record<string, number> = { VND: 0, USD: 2 };
const FORM_FIELDS = ["submission_id", "vendor", "invoice_number", "receipt_id", "transaction_date", "amount_minor", "currency"];

const STATUS: Record<CheckStatus, { label: string; tone: string; card: string }> = {
  duplicate_candidate: { label: "Ứng viên trùng", tone: "amber", card: "dup" },
  needs_review: { label: "Cần người xem lại", tone: "blue", card: "review" },
  unique_under_current_rules: { label: "Chưa trùng theo quy tắc hiện tại", tone: "green", card: "unique" },
};
const REVIEW: Record<ReviewStatus, { label: string; tone: string }> = {
  pending: { label: "Chờ duyệt", tone: "amber" },
  confirmed_duplicate: { label: "Đã xác nhận trùng", tone: "red" },
  dismissed: { label: "Đã bỏ nghi vấn", tone: "gray" },
};
const KEY_LABEL = { receipt: "mã biên nhận", invoice: "nơi bán + số hóa đơn" };

/** Bản người dùng đang sửa, giữ dạng chuỗi để hiện được cả giá trị sai GenAI trả (vd tiền âm, "EUR"). */
interface Draft {
  vendor: string;
  invoice_number: string;
  receipt_id: string;
  transaction_date: string;
  amount_minor: string;
  currency: string;
}

function toDraft(data: ExpenseExtractResponse): Draft {
  // Ưu tiên bản đã kiểm tra lớp 1; GenAI trả sai khuôn thì lấy JSON gốc để sửa
  const source = (data.extraction ?? data.llm_output ?? {}) as Record<string, unknown>;
  const text = (value: unknown) => (value === null || value === undefined ? "" : String(value));
  return {
    vendor: text(source.vendor),
    invoice_number: text(source.invoice_number),
    receipt_id: text(source.receipt_id),
    transaction_date: text(source.transaction_date).slice(0, 10),
    amount_minor: text(source.amount_minor),
    currency: text(source.currency),
  };
}

function toExpense(draft: Draft, submissionId: string): ExpenseExtraction {
  // Ô trống (hoặc chỉ có dấu cách) = không đọc được = null. Không tự sửa chữ hoa/dấu cách: việc đó của thuật toán.
  const value = (s: string) => (s.trim() === "" ? null : s);
  const amountText = draft.amount_minor.trim();
  // Không phải số thì vẫn gửi nguyên chuỗi để backend báo lỗi rõ ràng (422), không tự đoán
  const amount = amountText === "" ? null : Number.isFinite(Number(amountText)) ? Number(amountText) : amountText;
  const expense = {
    submission_id: submissionId,
    vendor: value(draft.vendor),
    invoice_number: value(draft.invoice_number),
    transaction_date: value(draft.transaction_date),
    amount_minor: amount as number | null,
    currency: value(draft.currency),
    receipt_id: value(draft.receipt_id),
  };
  const missing = (Object.keys(expense) as (keyof typeof expense)[])
    .filter((k) => k !== "submission_id" && expense[k] === null) as ExpenseMissingField[];
  return { ...expense, missing_fields: missing };
}

/** 1250000 VND -> "1.250.000 VND"; 12550 USD -> "125.50 USD" (giống algo.format_amount ở backend). */
function formatAmount(minor: number | null, currency: string | null): string {
  if (minor === null || !Number.isInteger(minor)) return "—";
  const digits = MINOR_DIGITS[currency ?? ""] ?? 0;
  if (digits === 0) return `${minor.toLocaleString("de-DE")} ${currency ?? ""}`.trim();
  const sign = minor < 0 ? "-" : "";
  const abs = Math.abs(minor);
  const major = Math.floor(abs / 10 ** digits).toLocaleString("en-US");
  return `${sign}${major}.${String(abs % 10 ** digits).padStart(digits, "0")} ${currency}`;
}

const errorIssues = (error: unknown): Issue[] => {
  const err = error as ApiError;
  return err.issues?.length ? err.issues : [{ field: "(toàn bộ)", message: err.message, level: "error" }];
};

export default function ExpenseWorkspace({ llmMode }: { llmMode: string }) {
  // Bước 1: nhập
  const [samples, setSamples] = useState<ExpenseSample[]>([]);
  const [text, setText] = useState("");
  const [pdfName, setPdfName] = useState("");
  const [inputIssues, setInputIssues] = useState<Issue[]>([]);
  const [inputBusy, setInputBusy] = useState<"pdf" | "extract" | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  // Bước 2: xem/sửa, kiểm tra trùng, duyệt
  const [extraction, setExtraction] = useState<ExpenseExtractResponse | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [checked, setChecked] = useState<CheckOut | null>(null);
  const [checkedDraft, setCheckedDraft] = useState<Draft | null>(null); // bản đã gửi kiểm tra, để biết người dùng có sửa sau đó không
  const [reviewStatus, setReviewStatus] = useState<ReviewStatus | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<"check" | ReviewAction | null>(null);

  // Bước 3: danh sách đã kiểm tra
  const [submissions, setSubmissions] = useState<SubmissionOut[]>([]);
  const [listError, setListError] = useState("");

  const loadSubmissions = useCallback(async () => {
    try {
      setSubmissions(await api.expenseSubmissions(20));
      setListError("");
    } catch (error) {
      setListError(error instanceof Error ? error.message : "Không tải được danh sách");
    }
  }, []);

  useEffect(() => { api.expenseSamples().then(setSamples).catch(() => setSamples([])); }, []);
  useEffect(() => { loadSubmissions(); }, [loadSubmissions]);

  const reset = () => {
    setExtraction(null);
    setDraft(null);
    setIssues([]);
    setChecked(null);
    setCheckedDraft(null);
    setReviewStatus(null);
    setNote("");
  };

  const pickSample = (sample: ExpenseSample) => {
    setText(sample.text);
    setPdfName("");
    setInputIssues([]);
    reset();
  };

  // PDF -> văn bản. Chưa gọi GenAI: người dùng đọc lại văn bản trước.
  const readPdf = async (file: Blob, name: string) => {
    setInputBusy("pdf");
    setInputIssues([]);
    reset();
    try {
      const result = await api.expensePdfText(file, name);
      setText(result.text);
      setPdfName(result.filename ?? name);
    } catch (error) {
      setPdfName("");
      setInputIssues(errorIssues(error).map((i) => ({ ...i, field: "pdf" })));
    } finally {
      setInputBusy(null);
    }
  };

  const trySamplePdf = async () => {
    try {
      await readPdf(await api.expenseSamplePdf(), "sunrise-hotel.pdf");
    } catch (error) {
      setInputIssues(errorIssues(error).map((i) => ({ ...i, field: "pdf" })));
    }
  };

  const runExtract = async () => {
    setInputBusy("extract");
    setInputIssues([]);
    reset();
    try {
      const result = await api.expenseExtract(text);
      setExtraction(result);
      setDraft(toDraft(result));
      setIssues(result.issues);
    } catch (error) {
      setInputIssues(errorIssues(error));
    } finally {
      setInputBusy(null);
    }
  };

  // Lần đầu: /check. Đã kiểm tra rồi mà sửa thông tin: /review với action "recheck".
  const runCheck = async () => {
    if (!extraction?.run_id || !draft) return;
    setBusy("check");
    try {
      const expense = toExpense(draft, extraction.submission_id);
      let result: CheckOut;
      if (checked) {
        const out = await api.expenseReview(extraction.submission_id, "recheck", expense, note);
        result = out.result!;
      } else {
        result = await api.expenseCheck(extraction.run_id, expense);
      }
      setChecked(result);
      setCheckedDraft(draft);
      setReviewStatus(result.review_status);
      setIssues(result.warnings);
      setNote("");
      await loadSubmissions();
    } catch (error) {
      setIssues(errorIssues(error));
    } finally {
      setBusy(null);
    }
  };

  const runReview = async (submissionId: string, action: ReviewAction, reviewNote = "") => {
    setBusy(action);
    try {
      const out = await api.expenseReview(submissionId, action, null, reviewNote);
      if (submissionId === extraction?.submission_id) setReviewStatus(out.submission.review_status);
      await loadSubmissions();
    } catch (error) {
      if (submissionId === extraction?.submission_id) setIssues(errorIssues(error));
      else setListError(error instanceof Error ? error.message : "Không duyệt được");
    } finally {
      setBusy(null);
    }
  };

  const update = (patch: Partial<Draft>) => setDraft((d) => (d ? { ...d, ...patch } : d));

  const original = useMemo(() => (extraction ? toDraft(extraction) : null), [extraction]);
  const edited = (field: keyof Draft) => Boolean(original && draft && original[field] !== draft[field]);
  // Đã kiểm tra rồi mà sửa tiếp: kết luận cũ không còn đúng, phải kiểm tra lại trước khi duyệt
  const dirty = Boolean(checked && draft && checkedDraft && JSON.stringify(draft) !== JSON.stringify(checkedDraft));
  const issuesFor = (field: string) => issues.filter((i) => i.field === field);
  const generalIssues = issues.filter((i) => !FORM_FIELDS.includes(i.field));
  const current = checkedDraft && extraction ? toExpense(checkedDraft, extraction.submission_id) : null;
  const pending = submissions.filter((s) => s.review_status === "pending").length;
  const amountPreview = draft ? formatAmount(draft.amount_minor.trim() === "" ? null : Number(draft.amount_minor), draft.currency || null) : "—";

  return (
    <div className="page-stack">
      <section className="metric-grid">
        <Metric icon={Hash} label="Mã chứng từ" value={extraction?.submission_id ?? "—"} hint="backend cấp" tone="violet" />
        <Metric icon={FileSearch} label="Kết quả kiểm tra" value={checked ? STATUS[checked.status].label : "—"} tone={checked?.status === "duplicate_candidate" ? "amber" : checked?.status === "needs_review" ? "blue" : "green"} />
        <Metric icon={ShieldQuestion} label="Chờ người duyệt" value={String(pending)} hint="trong 20 chứng từ gần nhất" tone="amber" />
        <Metric icon={ClipboardCheck} label="Đã kiểm tra" value={String(submissions.length)} hint="gần nhất" tone="green" />
      </section>

      <div className="work-grid">
        {/* Bước 1 */}
        <section className="panel">
          <PanelHeader step={1} title="Nhập chứng từ" subtitle="Dán văn bản đã OCR hoặc tải PDF có sẵn lớp chữ. Backend tự cấp mã chứng từ." />
          <div className="panel-body">
            {samples.length > 0 && (
              <div className="field">
                <label>Chứng từ mẫu {llmMode === "mock" && <em>chế độ mock chỉ đọc được các mẫu này</em>}</label>
                <div className="suggestions">
                  {samples.map((s) => (
                    <button key={s.label} title={s.note} onClick={() => pickSample(s)} className={clsx(s.label.includes("[Lỗi]") && "bad")}>{s.label}</button>
                  ))}
                </div>
              </div>
            )}
            <div className="field">
              <label>PDF <em>chỉ PDF có lớp chữ; ảnh scan cần OCR trước</em></label>
              <div className="toolbar">
                <input
                  ref={fileInput} type="file" accept="application/pdf,.pdf" hidden
                  onChange={(e) => { const f = e.target.files?.[0]; if (f) readPdf(f, f.name); e.target.value = ""; }}
                />
                <button className="secondary-button" onClick={() => fileInput.current?.click()} disabled={inputBusy !== null}>
                  {inputBusy === "pdf" ? <Loader2 className="spin" size={14} /> : <FileUp size={14} />} Tải PDF lên
                </button>
                <button className="secondary-button" onClick={trySamplePdf} disabled={inputBusy !== null}><FileText size={14} /> Thử PDF mẫu</button>
                {pdfName && <span className="file-chip"><FileText size={12} /> {pdfName}</span>}
              </div>
              <FieldIssues issues={inputIssues.filter((i) => i.field === "pdf")} />
            </div>
            <div className="field">
              <label>Văn bản chứng từ {pdfName && <em>lấy từ {pdfName}, đọc lại trước khi gửi</em>}</label>
              <textarea rows={8} value={text} onChange={(e) => setText(e.target.value)} placeholder={"Ví dụ:\nABC TRAVEL\nSố hóa đơn: INV-0042\nMã biên nhận: R-42\nTổng cộng: 1.250.000 VND"} />
              <FieldIssues issues={inputIssues.filter((i) => i.field !== "pdf")} />
            </div>
            <button className="primary-button" onClick={runExtract} disabled={inputBusy !== null || !text.trim()}>
              {inputBusy === "extract" ? <Loader2 className="spin" size={16} /> : <Sparkles size={16} />} GenAI đọc chứng từ
            </button>
          </div>
        </section>

        {/* Bước 2 */}
        <section className="panel">
          <PanelHeader step={2} title="Xem & sửa, rồi kiểm tra trùng" subtitle="Code kiểm tra mã, ngày, tiền, loại tiền. Dữ liệu chưa đủ tin cậy thì không trả “chưa trùng”." />
          <div className="panel-body">
            {!draft || !extraction ? (
              <Empty icon={Sparkles} text="Chưa có kết quả. Nhập chứng từ rồi bấm “GenAI đọc chứng từ”." />
            ) : (
              <>
                <div className="run-meta">
                  <span>run #{extraction.run_id}</span>
                  <span>mã <strong>{extraction.submission_id}</strong></span>
                  <span className={clsx("pill", extraction.can_check ? "green" : "red")}>
                    {extraction.can_check ? "Đủ điều kiện kiểm tra" : "Cần sửa trước khi kiểm tra"}
                  </span>
                </div>
                <FieldIssues issues={generalIssues} />

                <div className="form-row">
                  <Field label="submission_id (backend cấp, khóa)" edited={false} issues={issuesFor("submission_id")}>
                    <input value={extraction.submission_id} readOnly disabled title="Mã do backend cấp. Nếu GenAI trả mã khác, hệ thống vẫn dùng mã này." />
                  </Field>
                  <Field label="vendor (nơi bán)" edited={edited("vendor")} issues={issuesFor("vendor")}>
                    <input value={draft.vendor} onChange={(e) => update({ vendor: e.target.value })} placeholder="null" />
                  </Field>
                </div>
                <div className="form-row">
                  <Field label="invoice_number (số hóa đơn)" edited={edited("invoice_number")} issues={issuesFor("invoice_number")}>
                    <input value={draft.invoice_number} onChange={(e) => update({ invoice_number: e.target.value })} placeholder="null" />
                  </Field>
                  <Field label="receipt_id (mã biên nhận)" edited={edited("receipt_id")} issues={issuesFor("receipt_id")}>
                    <input value={draft.receipt_id} onChange={(e) => update({ receipt_id: e.target.value })} placeholder="null" />
                  </Field>
                </div>
                <div className="form-row">
                  <Field label="transaction_date" edited={edited("transaction_date")} issues={issuesFor("transaction_date")}>
                    <input type="date" value={draft.transaction_date} onChange={(e) => update({ transaction_date: e.target.value })} />
                  </Field>
                  <Field label="currency" edited={edited("currency")} issues={issuesFor("currency")}>
                    <select value={draft.currency} onChange={(e) => update({ currency: e.target.value })}>
                      <option value="">— null —</option>
                      {draft.currency && !CURRENCIES.includes(draft.currency) && <option value={draft.currency}>{draft.currency} (chưa hỗ trợ)</option>}
                      {CURRENCIES.map((c) => <option key={c} value={c}>{c}</option>)}
                    </select>
                  </Field>
                </div>
                <Field label={`amount_minor (số nguyên, đơn vị nhỏ nhất) = ${amountPreview}`} edited={edited("amount_minor")} issues={issuesFor("amount_minor")}>
                  <input inputMode="numeric" value={draft.amount_minor} onChange={(e) => update({ amount_minor: e.target.value })} placeholder="null" />
                </Field>

                <details className="raw-json">
                  <summary>JSON gốc GenAI trả về</summary>
                  <JsonBox title="llm_output" value={extraction.llm_output} />
                </details>

                {checked && current && (
                  <ResultCard
                    result={checked} current={current} reviewStatus={reviewStatus} dirty={dirty}
                    note={note} onNote={setNote} busy={busy}
                    onReview={(action) => runReview(extraction.submission_id, action, note)}
                  />
                )}

                {(!checked || dirty) && (
                  <button className="primary-button" onClick={runCheck} disabled={busy !== null}>
                    {busy === "check" ? <Loader2 className="spin" size={16} /> : checked ? <RotateCcw size={16} /> : <FileSearch size={16} />}
                    {checked ? "Kiểm tra lại với thông tin đã sửa" : "Kiểm tra trùng"}
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
          title="Chứng từ đã kiểm tra"
          subtitle="Bảng tra cứu dùng hash map: nhãn = loại tiền + mã biên nhận, và loại tiền + nơi bán + số hóa đơn. Bỏ nghi vấn không xóa nhãn."
          action={<button className="secondary-button" onClick={loadSubmissions}><RefreshCw size={14} /> Làm mới</button>}
        />
        {listError && <p className="queue-message">{listError}</p>}
        {!submissions.length ? (
          <Empty icon={FileSearch} text="Chưa có chứng từ nào được kiểm tra." />
        ) : (
          <div className="table-wrap">
            <table className="data-table expense-table">
              <thead>
                <tr><th>Mã</th><th>Nơi bán</th><th>Số hóa đơn</th><th>Biên nhận</th><th>Ngày</th><th>Số tiền</th><th>Kết quả</th><th>Duyệt</th></tr>
              </thead>
              <tbody>
                {submissions.map((s) => (
                  <tr key={s.submission_id} className={clsx(s.submission_id === extraction?.submission_id && "selected")}>
                    <td><strong>{s.submission_id}</strong></td>
                    <td>{s.vendor ?? <em className="null">null</em>}</td>
                    <td className="mono">{s.invoice_number ?? <em className="null">null</em>}</td>
                    <td className="mono">{s.receipt_id ?? <em className="null">null</em>}</td>
                    <td>{s.transaction_date ?? "—"}</td>
                    <td className="amount">{s.amount_text ?? "—"}</td>
                    <td>
                      {s.check_status && <span className={`pill ${STATUS[s.check_status].tone}`}>{STATUS[s.check_status].label}</span>}
                      {s.matched_ids.length > 0 && <small>trùng {s.matched_ids.join(", ")}</small>}
                    </td>
                    <td>
                      {s.review_status ? <span className={`pill ${REVIEW[s.review_status].tone}`}>{REVIEW[s.review_status].label}</span> : <small>không cần</small>}
                      {s.review_status === "pending" && s.submission_id !== extraction?.submission_id && (
                        <div className="row-actions">
                          {s.check_status === "duplicate_candidate" && (
                            <button onClick={() => runReview(s.submission_id, "confirm_duplicate")} disabled={busy !== null}>Xác nhận trùng</button>
                          )}
                          <button onClick={() => runReview(s.submission_id, "dismiss")} disabled={busy !== null}>
                            {s.check_status === "duplicate_candidate" ? "Bỏ nghi vấn" : "Đã xem lại"}
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function ResultCard({
  result, current, reviewStatus, dirty, note, onNote, busy, onReview,
}: {
  result: CheckOut;
  current: ExpenseExtraction;
  reviewStatus: ReviewStatus | null;
  dirty: boolean;
  note: string;
  onNote: (v: string) => void;
  busy: string | null;
  onReview: (action: ReviewAction) => void;
}) {
  const status = STATUS[result.status];
  return (
    <div className={clsx("result-card", status.card, dirty && "stale")}>
      <div className="result-head">
        {result.status === "duplicate_candidate" ? <Copy size={18} /> : result.status === "needs_review" ? <ShieldQuestion size={18} /> : <CheckCircle2 size={18} />}
        <div>
          <strong>{status.label}</strong>
          <span>{result.reason}</span>
          {result.keys.length > 0 && (
            <small>Nhãn tra cứu: {result.keys.map((k) => <code key={k.join("|")}>{k.join(" · ")}</code>)}</small>
          )}
        </div>
        {reviewStatus && <span className={`pill ${REVIEW[reviewStatus].tone}`}>{REVIEW[reviewStatus].label}</span>}
      </div>

      {result.matches.map((m) => <CompareTable key={m.submission_id} match={m} current={current} />)}

      {dirty ? (
        <p className="stale-note">Bạn đã sửa thông tin sau lần kiểm tra này. Hãy kiểm tra lại trước khi duyệt.</p>
      ) : reviewStatus === "pending" && (
        <div className="review-box">
          <input value={note} onChange={(e) => onNote(e.target.value)} placeholder="Ghi chú cho người duyệt (không bắt buộc)" maxLength={1000} />
          <div className="toolbar">
            {result.status === "duplicate_candidate" && (
              <button className="danger-button" onClick={() => onReview("confirm_duplicate")} disabled={busy !== null}>
                {busy === "confirm_duplicate" ? <Loader2 className="spin" size={14} /> : <Copy size={14} />} Xác nhận trùng
              </button>
            )}
            <button className="secondary-button" onClick={() => onReview("dismiss")} disabled={busy !== null}>
              {busy === "dismiss" ? <Loader2 className="spin" size={14} /> : <XCircle size={14} />}
              {result.status === "duplicate_candidate" ? " Bỏ nghi vấn" : " Đã xem lại, bỏ nghi vấn"}
            </button>
          </div>
          <small>Xác nhận trùng chỉ ghi nhận ý kiến người duyệt, hệ thống không tự từ chối hoàn tiền.</small>
        </div>
      )}
    </div>
  );
}

/** So từng ô giữa chứng từ đang kiểm tra và chứng từ cũ. Ô khác nhau được tô màu, kèm ghi chú. */
function CompareTable({ match, current }: { match: MatchOut; current: ExpenseExtraction }) {
  const diff = Object.fromEntries(match.differences.map((d) => [d.field, d]));
  const prev = match.previous;
  const rows: [string, string, string | null, string | null][] = [
    ["vendor", "Nơi bán", current.vendor, prev.vendor],
    ["invoice_number", "Số hóa đơn", current.invoice_number, prev.invoice_number],
    ["receipt_id", "Mã biên nhận", current.receipt_id, prev.receipt_id],
    ["transaction_date", "Ngày", current.transaction_date, prev.transaction_date],
    ["amount_minor", "Số tiền", formatAmount(current.amount_minor, current.currency), prev.amount_text],
  ];
  return (
    <div className="compare">
      <p>Trùng <strong>{match.matched_by.map((k) => KEY_LABEL[k]).join(" và ")}</strong> với <strong>{match.submission_id}</strong></p>
      <table className="compare-table">
        <colgroup><col className="label" /><col className="value" /><col className="value" /><col className="note" /></colgroup>
        <thead><tr><th>Ô</th><th>{current.submission_id} (đang kiểm tra)</th><th>{match.submission_id} (đã gửi trước)</th><th /></tr></thead>
        <tbody>
          {rows.map(([field, label, a, b]) => {
            const d = diff[field];
            const soft = d?.note === "chỉ khác chữ hoa/dấu cách";
            return (
              <tr key={field} className={clsx(d && (soft ? "soft" : "diff"))}>
                <td>{label}</td>
                <td className="mono">{show(a)}</td>
                <td className="mono">{show(b)}</td>
                <td className="note">{d?.note || (d ? "khác" : "giống")}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// Hiện cả dấu cách thừa (vd "  abc   travel ") để người duyệt thấy vì sao vẫn coi là trùng
const show = (value: string | null) => (value === null ? "null" : `“${value}”`);

function Field({ label, edited, issues, children }: { label: string; edited: boolean; issues: Issue[]; children: React.ReactNode }) {
  return (
    <div className={clsx("field", issues.some((i) => i.level === "error") && "has-error")}>
      <label>{label} {edited && <em className="edited">đã sửa</em>}</label>
      {children}
      <FieldIssues issues={issues} />
    </div>
  );
}
