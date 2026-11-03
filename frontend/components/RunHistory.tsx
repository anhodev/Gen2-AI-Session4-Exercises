"use client";

// Lịch sử xử lý: nội dung gốc -> JSON GenAI -> bản người dùng sửa -> kết quả cuối.
import { useCallback, useEffect, useState } from "react";
import { History, RefreshCw } from "lucide-react";
import clsx from "clsx";
import { api } from "@/lib/api";
import { shortTime } from "@/lib/format";
import type { Run } from "@/lib/types";
import { Empty, JsonBox, PanelHeader } from "./ui";

const STATUS_TONE: Record<Run["status"], string> = { extracted: "blue", confirmed: "green", rejected: "red" };
const MODULES = [
  { id: "tickets", label: "01 · Ticket" },
  { id: "docks", label: "02 · Giờ nhận xe" },
  { id: "expenses", label: "03 · Hóa đơn trùng" },
];

export default function RunHistory() {
  const [module, setModule] = useState("tickets");
  const [runs, setRuns] = useState<Run[]>([]);
  const [selected, setSelected] = useState<Run | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const data = await api.runs(module);
      setRuns(data);
      setSelected((current) => data.find((r) => r.id === current?.id) ?? data[0] ?? null);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không tải được lịch sử");
    }
  }, [module]);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="history-grid">
      <section className="panel">
        <PanelHeader title="Các lần xử lý" subtitle="Mới nhất trước" action={
          <div className="toolbar">
            <label>Bài
              <select value={module} onChange={(e) => { setModule(e.target.value); setSelected(null); }}>
                {MODULES.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
              </select>
            </label>
            <button className="secondary-button" onClick={load}><RefreshCw size={14} /> Làm mới</button>
          </div>
        } />
        {error && <p className="queue-message">{error}</p>}
        {!runs.length ? <Empty icon={History} text="Chưa có lần xử lý nào." /> : (
          <div className="table-wrap">
            <table className="data-table selectable">
              <thead><tr><th>Run</th><th>Trạng thái</th><th>Model</th><th>Thời gian GenAI</th><th>Lúc</th></tr></thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.id} onClick={() => setSelected(run)} className={clsx(selected?.id === run.id && "selected")}>
                    <td><strong>#{run.id}</strong><small>{String(run.raw_input.ticket_id ?? run.raw_input.request_id ?? run.raw_input.submission_id ?? "")}</small></td>
                    <td><span className={`pill ${STATUS_TONE[run.status]}`}>{run.status}</span></td>
                    <td>{run.model}</td>
                    <td>{run.latency_ms} ms</td>
                    <td>{shortTime(run.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel">
        <PanelHeader title={selected ? `Chi tiết run #${selected.id}` : "Chi tiết"} subtitle="Database lưu giờ theo UTC (GMT+0)" />
        {!selected ? <Empty text="Chọn một dòng để xem." /> : (
          <div className="panel-body">
            {selected.error && <p className="form-error">{selected.error}</p>}
            <JsonBox title="1. Nội dung gốc (raw_input)" value={selected.raw_input} />
            <JsonBox title="2. JSON GenAI trả về (llm_output)" value={selected.llm_output} />
            <JsonBox title="3. Bản người dùng sửa (user_output)" value={selected.user_output} />
            <JsonBox title="4. Kết quả cuối (result)" value={selected.result} />
            {!!selected.issues?.length && <JsonBox title="Lỗi/cảnh báo lúc kiểm tra (issues)" value={selected.issues} />}
          </div>
        )}
      </section>
    </div>
  );
}
