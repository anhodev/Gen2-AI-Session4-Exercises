"use client";

// Bài 04 - Màn hình lập thứ tự công việc từ biên bản họp.
// Luồng: (1) dán biên bản -> GenAI đọc  (2) xem/sửa việc và quan hệ trước-sau, mã do backend cấp và khóa
//        (3) code tính thứ tự bằng topological sort, hoặc báo vòng chờ nhau  (4) lịch sử các lần tính.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRight, CheckCircle2, GitBranch, History, ListOrdered, Loader2, Plus, Quote, RefreshCw, Repeat, Sparkles, Trash2, Upload,
} from "lucide-react";
import clsx from "clsx";
import { api, ApiError } from "@/lib/api";
import { shortTime } from "@/lib/format";
import type { Issue, OrderOut, OrderSummary, Plan, PlanDependency, PlanExtractResponse, PlanSample } from "@/lib/types";
import { Empty, FieldIssues, JsonBox, Metric, PanelHeader } from "./ui";

/** Một dòng đang sửa. key chỉ dùng trong trình duyệt để giữ đúng dòng khi thêm/xóa; mã thật là id do backend cấp. */
interface TaskRow { key: string; id: string | null; title: string; owner: string }
interface DepRow { key: string; before: string; after: string; evidence: string | null }
interface Draft { tasks: TaskRow[]; dependencies: DepRow[] }

const pairKey = (d: { before: string; after: string }) => `${d.before}→${d.after}`;

function toPlan(draft: Draft): Plan {
  return {
    // Người phụ trách để trống = không biết = null
    tasks: draft.tasks.map((t) => ({ id: t.id, title: t.title, owner: t.owner.trim() === "" ? null : t.owner })),
    dependencies: draft.dependencies.map((d) => ({ before: d.before, after: d.after, evidence: d.evidence })),
  };
}

/** Hai bản giống nhau không (quan hệ ghi lặp tính một lần, như backend). */
function samePlan(a: Plan, b: Plan): boolean {
  const dedupe = (deps: PlanDependency[]) => [...new Map(deps.map((d) => [pairKey(d), d])).keys()];
  return JSON.stringify(a.tasks) === JSON.stringify(b.tasks) && JSON.stringify(dedupe(a.dependencies)) === JSON.stringify(dedupe(b.dependencies));
}

/**
 * Backend trả lỗi theo vị trí ("tasks.2.title", "dependencies.0.after"). Đổi sang key của dòng
 * ("tasks:r7.title") để lỗi vẫn nằm đúng dòng khi người dùng thêm/xóa dòng khác trước khi kết quả về.
 */
function keyIssues(issues: Issue[], draft: Draft): Issue[] {
  return issues.map((issue) => {
    const match = issue.field.match(/^(tasks|dependencies)\.(\d+)(\..+)?$/);
    if (!match) return issue;
    const rows = match[1] === "tasks" ? draft.tasks : draft.dependencies;
    const row = rows[Number(match[2])];
    return row ? { ...issue, field: `${match[1]}:${row.key}${match[3] ?? ""}` } : issue;
  });
}

const errorIssues = (error: unknown): Issue[] => {
  const err = error as ApiError;
  return err.issues?.length ? err.issues : [{ field: "(toàn bộ)", message: err.message, level: "error" }];
};

export default function PlanWorkspace({ llmMode }: { llmMode: string }) {
  const nextKey = useRef(0);
  const newKey = () => `r${++nextKey.current}`;

  // Bước 1
  const [samples, setSamples] = useState<PlanSample[]>([]);
  const [text, setText] = useState("");
  const [inputIssues, setInputIssues] = useState<Issue[]>([]);
  const [extracting, setExtracting] = useState(false);

  // Bước 2
  const [extraction, setExtraction] = useState<PlanExtractResponse | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [checking, setChecking] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newOwner, setNewOwner] = useState("");
  const validateRequest = useRef(0);

  // Bước 3, 4
  const [result, setResult] = useState<OrderOut | null>(null);
  const [ordering, setOrdering] = useState(false);
  const [history, setHistory] = useState<OrderSummary[]>([]);

  const runId = extraction?.run_id ?? null;

  useEffect(() => { api.planSamples().then(setSamples).catch(() => setSamples([])); }, []);

  const loadHistory = useCallback(async (id: number | null) => {
    if (id === null) return setHistory([]);
    try { setHistory(await api.planOrders(id)); } catch { setHistory([]); }
  }, []);

  const fromPlan = (plan: Plan | null): Draft => ({
    tasks: (plan?.tasks ?? []).map((t) => ({ key: newKey(), id: t.id, title: t.title, owner: t.owner ?? "" })),
    dependencies: (plan?.dependencies ?? []).map((d) => ({ key: newKey(), ...d })),
  });

  const runExtract = async () => {
    setExtracting(true);
    setInputIssues([]);
    setResult(null);
    try {
      const data = await api.planExtract(text);
      const rows = fromPlan(data.plan);
      setExtraction(data);
      setDraft(rows);
      setIssues(keyIssues(data.issues, rows));
      setHistory([]);
    } catch (error) {
      setInputIssues(errorIssues(error));
    } finally {
      setExtracting(false);
    }
  };

  // Mỗi lần sửa: đợi người dùng ngừng gõ một chút rồi cho backend kiểm tra lại và cấp mã cho việc mới
  useEffect(() => {
    if (!draft || runId === null) return;
    const snapshot = draft;
    const request = ++validateRequest.current;
    const timer = setTimeout(async () => {
      setChecking(true);
      try {
        const out = await api.planValidate(runId, toPlan(snapshot));
        if (request !== validateRequest.current) return; // đã có lần sửa mới hơn
        setIssues(keyIssues(out.issues, snapshot));
        // Việc mới thêm (chưa có mã) nhận mã backend vừa cấp. Danh sách việc trả về cùng thứ tự với bản đã gửi.
        const assigned = new Map(snapshot.tasks.map((t, i) => [t.key, out.plan?.tasks[i]?.id ?? null]));
        if (snapshot.tasks.some((t) => t.id === null && assigned.get(t.key))) {
          setDraft((d) => d && {
            ...d,
            tasks: d.tasks.map((t) => (t.id === null && assigned.get(t.key) ? { ...t, id: assigned.get(t.key)! } : t)),
          });
        }
      } catch (error) {
        if (request === validateRequest.current) setIssues(keyIssues(errorIssues(error), snapshot));
      } finally {
        if (request === validateRequest.current) setChecking(false);
      }
    }, 350);
    return () => clearTimeout(timer);
  }, [draft, runId]);

  const runOrder = async () => {
    if (!draft || runId === null) return;
    setOrdering(true);
    const snapshot = draft;
    try {
      const out = await api.planOrder(runId, toPlan(snapshot));
      setResult(out);
      setIssues(keyIssues(out.warnings, snapshot));
      await loadHistory(runId);
    } catch (error) {
      setIssues(keyIssues(errorIssues(error), snapshot));
    } finally {
      setOrdering(false);
    }
  };

  const openOrder = async (orderId: number) => {
    try { setResult(await api.planOrderDetail(orderId)); } catch {}
  };

  // --- Sửa bản nháp ---
  const updateTask = (key: string, patch: Partial<TaskRow>) =>
    setDraft((d) => d && { ...d, tasks: d.tasks.map((t) => (t.key === key ? { ...t, ...patch } : t)) });
  const updateDep = (key: string, patch: Partial<DepRow>) =>
    setDraft((d) => d && { ...d, dependencies: d.dependencies.map((x) => (x.key === key ? { ...x, ...patch } : x)) });
  const removeDep = (key: string) =>
    setDraft((d) => d && { ...d, dependencies: d.dependencies.filter((x) => x.key !== key) });
  const removePair = (pair: string) =>
    setDraft((d) => d && { ...d, dependencies: d.dependencies.filter((x) => pairKey(x) !== pair) });
  // Xóa việc thì xóa luôn các quan hệ dùng mã đó, để không còn mũi tên trỏ vào việc không tồn tại
  const removeTask = (row: TaskRow) =>
    setDraft((d) => d && {
      tasks: d.tasks.filter((t) => t.key !== row.key),
      dependencies: row.id ? d.dependencies.filter((x) => x.before !== row.id && x.after !== row.id) : d.dependencies,
    });
  const addTask = () => {
    if (!newTitle.trim()) return;
    setDraft((d) => d && { ...d, tasks: [...d.tasks, { key: newKey(), id: null, title: newTitle.trim(), owner: newOwner.trim() }] });
    setNewTitle("");
    setNewOwner("");
  };
  const addDep = () =>
    setDraft((d) => {
      if (!d) return d;
      const ids = d.tasks.map((t) => t.id).filter(Boolean) as string[];
      return { ...d, dependencies: [...d.dependencies, { key: newKey(), before: ids[0] ?? "", after: ids[1] ?? ids[0] ?? "", evidence: null }] };
    });
  const loadIntoDraft = (plan: Plan) => setDraft(fromPlan(plan));

  // --- Hiển thị ---
  const original = useMemo(() => {
    const plan = extraction?.plan;
    return {
      tasks: new Map((plan?.tasks ?? []).map((t) => [t.id, t])),
      pairs: new Set((plan?.dependencies ?? []).map(pairKey)),
    };
  }, [extraction]);
  const taskIds = draft?.tasks.map((t) => t.id).filter(Boolean) as string[] | undefined;
  const titles = new Map(draft?.tasks.map((t) => [t.id, t.title]) ?? []);
  const rowIssues = (prefix: string) => issues.filter((i) => i.field === prefix || i.field.startsWith(`${prefix}.`));
  const hasError = (field: string) => issues.some((i) => i.field === field && i.level === "error");
  const generalIssues = issues.filter((i) => !i.field.includes(":"));
  const blocking = issues.some((i) => i.level === "error");
  const waitingIds = draft?.tasks.some((t) => t.id === null) ?? false;
  const stale = Boolean(result && draft && !samePlan(toPlan(draft), result.plan));
  const cyclePairs = new Set(result?.status === "cycle_detected" ? result.cycle_dependencies.map(pairKey) : []);

  return (
    <div className="page-stack">
      <section className="metric-grid">
        <Metric icon={ListOrdered} label="Công việc" value={String(draft?.tasks.length ?? 0)} hint="mã do backend cấp" tone="violet" />
        <Metric icon={GitBranch} label="Quan hệ trước-sau" value={String(draft?.dependencies.length ?? 0)} tone="blue" />
        <Metric
          icon={result?.status === "cycle_detected" ? Repeat : CheckCircle2} label="Kết quả"
          value={!result ? "—" : result.status === "ordered" ? "Đã xếp thứ tự" : "Có vòng chờ nhau"}
          tone={!result ? "green" : result.status === "ordered" ? "green" : "red"}
        />
        <Metric icon={History} label="Số lần tính" value={String(history.length)} hint="của biên bản này" tone="amber" />
      </section>

      <div className="work-grid">
        {/* Bước 1 */}
        <section className="panel">
          <PanelHeader step={1} title="Dán biên bản họp" subtitle="GenAI tìm công việc và quan hệ trước-sau, kèm câu bằng chứng chép từ biên bản. GenAI không xếp thứ tự." />
          <div className="panel-body">
            {samples.length > 0 && (
              <div className="field">
                <label>Biên bản mẫu {llmMode === "mock" && <em>chế độ mock chỉ đọc được các mẫu này</em>}</label>
                <div className="suggestions">
                  {samples.map((s) => (
                    <button key={s.label} title={s.note} onClick={() => { setText(s.text); setInputIssues([]); }} className={clsx(s.label.includes("[Lỗi]") && "bad")}>{s.label}</button>
                  ))}
                </div>
              </div>
            )}
            <div className="field">
              <label>Biên bản</label>
              <textarea rows={12} value={text} onChange={(e) => setText(e.target.value)} placeholder={"Ví dụ:\n- Product chốt yêu cầu API.\n- Engineering tích hợp frontend. Sau khi chốt API mới tích hợp frontend."} />
              <FieldIssues issues={inputIssues} />
            </div>
            <button className="primary-button" onClick={runExtract} disabled={extracting || !text.trim()}>
              {extracting ? <Loader2 className="spin" size={16} /> : <Sparkles size={16} />} GenAI đọc biên bản
            </button>
          </div>
        </section>

        {/* Bước 2 */}
        <section className="panel">
          <PanelHeader step={2} title="Xem & sửa việc và quan hệ" subtitle="Mã T01… do backend cấp và không sửa được. Quan hệ A → C nghĩa là A phải xong rồi mới làm C." />
          <div className="panel-body">
            {!draft || !extraction ? (
              <Empty icon={Sparkles} text="Chưa có kết quả. Dán biên bản rồi bấm “GenAI đọc biên bản”." />
            ) : (
              <>
                <div className="run-meta">
                  <span>run #{extraction.run_id}</span>
                  <span className={clsx("pill", blocking ? "red" : "green")}>{blocking ? "Cần sửa trước khi tính" : "Dữ liệu hợp lệ"}</span>
                  {checking && <span><Loader2 className="spin" size={12} /> đang kiểm tra</span>}
                </div>
                {extraction.llm_issues.some((i) => i.level === "error") && !extraction.plan && (
                  <p className="form-error">GenAI chưa đọc được biên bản. Bạn vẫn có thể tự thêm việc và quan hệ bên dưới.</p>
                )}
                <FieldIssues issues={generalIssues} />

                <div className="plan-section">
                  <h4>Công việc</h4>
                  {draft.tasks.map((t) => {
                    const before = t.id ? original.tasks.get(t.id) : undefined;
                    const edited = before && (before.title !== t.title || (before.owner ?? "") !== t.owner);
                    return (
                      <div key={t.key} className="plan-row">
                        <div className="task-line">
                          <span className={clsx("task-id", !t.id && "pending")} title="Mã do backend cấp, không sửa được">{t.id ?? "…"}</span>
                          <input className={clsx(hasError(`tasks:${t.key}.title`) && "invalid")} value={t.title} onChange={(e) => updateTask(t.key, { title: e.target.value })} placeholder="Tên công việc" aria-label="Tên công việc" />
                          <input className={clsx("owner", hasError(`tasks:${t.key}.owner`) && "invalid")} value={t.owner} onChange={(e) => updateTask(t.key, { owner: e.target.value })} placeholder="phụ trách: null" aria-label="Người phụ trách" />
                          <button className="icon-only" onClick={() => removeTask(t)} title="Xóa việc này và các quan hệ của nó" aria-label="Xóa việc"><Trash2 size={14} /></button>
                        </div>
                        {(!before || edited) && <em className="edited">{before ? "đã sửa" : "mới thêm"}</em>}
                        <FieldIssues issues={rowIssues(`tasks:${t.key}`)} />
                      </div>
                    );
                  })}
                  <div className="task-line add">
                    <span className="task-id pending">mới</span>
                    <input value={newTitle} onChange={(e) => setNewTitle(e.target.value)} onKeyDown={(e) => e.key === "Enter" && addTask()} placeholder="Thêm việc: tên công việc" aria-label="Tên việc mới" />
                    <input className="owner" value={newOwner} onChange={(e) => setNewOwner(e.target.value)} onKeyDown={(e) => e.key === "Enter" && addTask()} placeholder="phụ trách (không bắt buộc)" aria-label="Người phụ trách việc mới" />
                    <button className="icon-only" onClick={addTask} disabled={!newTitle.trim()} title="Thêm việc" aria-label="Thêm việc"><Plus size={14} /></button>
                  </div>
                </div>

                <div className="plan-section">
                  <h4>Quan hệ trước-sau</h4>
                  {!draft.dependencies.length && <p className="hint">Chưa có quan hệ nào: mọi việc đều bắt đầu được ngay.</p>}
                  {draft.dependencies.map((d) => {
                    const added = !original.pairs.has(pairKey(d)) && d.evidence === null;
                    return (
                      <div key={d.key} className={clsx("plan-row", cyclePairs.has(pairKey(d)) && !stale && "in-cycle")}>
                        <div className="dep-line">
                          <TaskSelect value={d.before} ids={taskIds ?? []} titles={titles} invalid={hasError(`dependencies:${d.key}.before`)} onChange={(v) => updateDep(d.key, { before: v })} label="Việc phải xong trước" />
                          <ArrowRight size={15} className="arrow" />
                          <TaskSelect value={d.after} ids={taskIds ?? []} titles={titles} invalid={hasError(`dependencies:${d.key}.after`)} onChange={(v) => updateDep(d.key, { after: v })} label="Việc làm sau" />
                          <button className="icon-only" onClick={() => removeDep(d.key)} title="Xóa quan hệ" aria-label="Xóa quan hệ"><Trash2 size={14} /></button>
                        </div>
                        {d.evidence !== null ? (
                          <p className="evidence"><Quote size={12} /> {d.evidence}</p>
                        ) : (
                          <p className="evidence none">{added ? "Người dùng tự thêm, không có câu trong biên bản" : "Không có câu bằng chứng"}</p>
                        )}
                        <FieldIssues issues={rowIssues(`dependencies:${d.key}`)} />
                      </div>
                    );
                  })}
                  <button className="secondary-button" onClick={addDep} disabled={(taskIds?.length ?? 0) < 2}><Plus size={14} /> Thêm quan hệ</button>
                </div>

                {/* JSON gốc có lỗi (vd mã trùng) thì mở sẵn: lý do nằm ở đây, theo mã tạm của GenAI */}
                <details className="raw-json" open={extraction.llm_issues.some((i) => i.level === "error")}>
                  <summary>JSON gốc GenAI trả về (mã tạm của GenAI)</summary>
                  <FieldIssues issues={extraction.llm_issues} />
                  <JsonBox title="llm_output" value={extraction.llm_output} />
                </details>

                <button className="primary-button" onClick={runOrder} disabled={ordering || checking || blocking || waitingIds || !draft.tasks.length}>
                  {ordering ? <Loader2 className="spin" size={16} /> : <ListOrdered size={16} />} {result ? "Tính lại thứ tự" : "Tính thứ tự"}
                </button>
              </>
            )}
          </div>
        </section>
      </div>

      {/* Bước 3 */}
      <section className="panel">
        <PanelHeader
          step={3}
          title={result ? `Kết quả lần tính ${result.version}` : "Kết quả"}
          subtitle="Topological sort (Kahn + min-heap): việc nào hết phải chờ thì được làm; nhiều việc cùng sẵn sàng thì chọn mã đứng trước."
        />
        <div className="panel-body">
          {!result ? <Empty icon={ListOrdered} text="Chưa tính. Sửa xong bước 2 rồi bấm “Tính thứ tự”." /> : (
            <>
              {stale && (
                <div className="stale-bar">
                  <span>Bản đang sửa khác bản đã dùng cho lần tính này.</span>
                  <button className="secondary-button" onClick={() => loadIntoDraft(result.plan)}><Upload size={14} /> Nạp lại bản của lần tính này</button>
                </div>
              )}
              {result.status === "ordered" ? (
                <OrderView result={result} />
              ) : (
                <CycleView result={result} titles={new Map(result.plan.tasks.map((t) => [t.id, t.title]))} onRemove={stale ? undefined : removePair} />
              )}
              <FieldIssues issues={result.warnings} />
            </>
          )}
        </div>
      </section>

      {/* Bước 4 */}
      <section className="panel">
        <PanelHeader
          step={4} title="Các lần tính của biên bản này" subtitle="Mỗi lần tính lưu riêng: việc, quan hệ đã xác nhận và kết quả. Bấm một dòng để xem lại."
          action={<button className="secondary-button" onClick={() => loadHistory(runId)} disabled={runId === null}><RefreshCw size={14} /> Làm mới</button>}
        />
        {!history.length ? <Empty icon={History} text="Chưa có lần tính nào." /> : (
          <div className="table-wrap">
            <table className="data-table selectable">
              <thead><tr><th>Lần</th><th>Kết quả</th><th>Việc</th><th>Quan hệ</th><th>Lý do</th><th>Lúc</th></tr></thead>
              <tbody>
                {history.map((h) => (
                  <tr key={h.order_id} onClick={() => openOrder(h.order_id)} className={clsx(result?.order_id === h.order_id && "selected")}>
                    <td><strong>#{h.version}</strong></td>
                    <td><span className={`pill ${h.status === "ordered" ? "green" : "red"}`}>{h.status === "ordered" ? "Đã xếp" : "Có vòng"}</span></td>
                    <td>{h.task_count}</td>
                    <td>{h.dependency_count}</td>
                    <td className="reason">{h.reason}</td>
                    <td>{shortTime(h.created_at)}</td>
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

function TaskSelect({ value, ids, titles, invalid, onChange, label }: {
  value: string; ids: string[]; titles: Map<string | null, string>; invalid: boolean; onChange: (v: string) => void; label: string;
}) {
  return (
    <select className={clsx(invalid && "invalid")} value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}>
      {/* Mã không còn trong danh sách (vd GenAI trả mã lạ) vẫn hiện để người dùng thấy và chọn lại */}
      {!ids.includes(value) && <option value={value}>{value || "—"} (không có trong danh sách)</option>}
      {ids.map((id) => <option key={id} value={id}>{id} · {titles.get(id)}</option>)}
    </select>
  );
}

function OrderView({ result }: { result: OrderOut }) {
  return (
    <>
      <div className="result-card unique">
        <div className="result-head">
          <CheckCircle2 size={18} />
          <div><strong>Thứ tự thực hiện</strong><span>{result.reason}</span></div>
        </div>
      </div>
      <div className="table-wrap">
        <table className="data-table">
          <thead><tr><th>#</th><th>Mã</th><th>Công việc</th><th>Phụ trách</th><th>Vì sao ở vị trí này</th></tr></thead>
          <tbody>
            {result.steps.map((s) => (
              <tr key={s.task.id}>
                <td className="pos">{s.position}</td>
                <td><span className="task-id">{s.task.id}</span></td>
                <td>{s.task.title}</td>
                <td>{s.task.owner ?? <em className="null">null</em>}</td>
                <td className="reason">{s.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function CycleView({ result, titles, onRemove }: { result: OrderOut; titles: Map<string | null, string>; onRemove?: (pair: string) => void }) {
  return (
    <div className="result-card cycle">
      <div className="result-head">
        <Repeat size={18} />
        <div>
          <strong>Có vòng chờ nhau: chưa lập được thứ tự</strong>
          <span>{result.reason}</span>
        </div>
      </div>
      <div className="cycle-path">
        {result.cycle.map((id, i) => (
          <span key={i} className="cycle-node">
            {i > 0 && <ArrowRight size={14} />}
            <span className="task-id" title={titles.get(id)}>{id}</span>
          </span>
        ))}
      </div>
      <div className="compare">
        <p>Các quan hệ tạo thành vòng. Sửa hoặc xóa ít nhất một quan hệ rồi tính lại:</p>
        <ul className="cycle-deps">
          {result.cycle_dependencies.map((d) => (
            <li key={pairKey(d)}>
              <div>
                <strong>{d.before} · {titles.get(d.before)}</strong> <ArrowRight size={12} /> <strong>{d.after} · {titles.get(d.after)}</strong>
                <p className="evidence">{d.evidence !== null ? <><Quote size={12} /> {d.evidence}</> : "Người dùng tự thêm, không có câu trong biên bản"}</p>
              </div>
              {onRemove && <button className="secondary-button" onClick={() => onRemove(pairKey(d))}><Trash2 size={13} /> Xóa quan hệ này</button>}
            </li>
          ))}
        </ul>
        <p className="blocked">Chưa xếp được: {result.blocked.map((t) => `${t.id} ${t.title}`).join(", ")}</p>
      </div>
    </div>
  );
}
