"use client";

// Bài 01 - Làm thêm: giao ticket cho nhân viên theo kỹ năng và số việc tối đa.
// Chỉ xem: không lấy ticket nào ra khỏi hàng đợi. Code xét từ ticket gấp nhất; hết chỗ thì thử đổi người
// giữa các ticket đã giao trước khi báo "chưa có nhân viên phù hợp".
import { useCallback, useEffect, useRef, useState } from "react";
import { Clock3, Loader2, Plus, RefreshCw, Save, Trash2, UserX, Users } from "lucide-react";
import clsx from "clsx";
import { api, ApiError } from "@/lib/api";
import { shortTime } from "@/lib/format";
import type { AssignmentResponse, Intent, Issue, StaffMember } from "@/lib/types";
import { Empty, FieldIssues, PanelHeader, SeverityPill } from "./ui";

const INTENTS: Intent[] = ["billing", "technical", "account", "other"];

/** Một dòng đang sửa. key chỉ dùng trong trình duyệt; mã thật (NV…) do backend cấp khi lưu. */
interface Row extends StaffMember { key: string }

export default function TicketAssignments({ refreshKey }: { refreshKey: unknown }) {
  const nextKey = useRef(0);
  const toRows = (staff: StaffMember[]): Row[] => staff.map((s) => ({ ...s, key: `s${++nextKey.current}` }));

  const [rows, setRows] = useState<Row[]>([]);
  const [saved, setSaved] = useState<StaffMember[]>([]);
  const [issues, setIssues] = useState<Issue[]>([]); // field đã đổi sang key của dòng, vd "s3.name"
  const [saving, setSaving] = useState(false);
  const [data, setData] = useState<AssignmentResponse | null>(null);
  const [message, setMessage] = useState("");

  const loadAssignments = useCallback(async () => {
    try {
      setData(await api.assignments());
      setMessage("");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Không tải được bảng giao việc");
    }
  }, []);

  useEffect(() => {
    api.staff().then((staff) => { setSaved(staff); setRows(toRows(staff)); }).catch(() => setRows([]));
  }, []);
  // Hàng đợi đổi (xác nhận ticket mới, lấy ticket ra) thì giao lại
  useEffect(() => { loadAssignments(); }, [loadAssignments, refreshKey]);

  const strip = (r: Row[]): StaffMember[] => r.map(({ key: _key, ...s }) => s);
  const dirty = JSON.stringify(strip(rows)) !== JSON.stringify(saved);

  const update = (key: string, patch: Partial<StaffMember>) =>
    setRows((r) => r.map((row) => (row.key === key ? { ...row, ...patch } : row)));
  const toggleSkill = (row: Row, skill: Intent) =>
    update(row.key, { skills: row.skills.includes(skill) ? row.skills.filter((s) => s !== skill) : INTENTS.filter((s) => s === skill || row.skills.includes(s)) });

  const save = async () => {
    setSaving(true);
    const snapshot = rows;
    try {
      const staff = await api.saveStaff(strip(snapshot));
      setSaved(staff);
      setRows(toRows(staff));
      setIssues([]);
      await loadAssignments();
    } catch (error) {
      const err = error as ApiError;
      const list = err.issues?.length ? err.issues : [{ field: "(toàn bộ)", message: err.message, level: "error" as const }];
      // Lỗi trả theo vị trí ("staff.1.name"): đổi sang key của dòng để vẫn đúng dòng nếu người dùng thêm/xóa tiếp
      setIssues(list.map((i) => {
        const m = i.field.match(/^staff\.(\d+)(\..+)?$/);
        const row = m ? snapshot[Number(m[1])] : undefined;
        return row ? { ...i, field: `${row.key}${m![2] ?? ""}` } : i;
      }));
    } finally {
      setSaving(false);
    }
  };

  const rowIssues = (key: string) => issues.filter((i) => i.field === key || i.field.startsWith(`${key}.`));
  const generalIssues = issues.filter((i) => !rows.some((r) => i.field === r.key || i.field.startsWith(`${r.key}.`)));

  return (
    <section className="panel">
      <PanelHeader
        step={4}
        title="Giao ticket cho nhân viên (làm thêm)"
        subtitle="Xét từ ticket gấp nhất: giao cho người đúng kỹ năng, còn chỗ, ít việc nhất. Hết chỗ thì thử đổi người giữa các ticket đã giao. Chỉ xem, không lấy ticket ra."
        action={<button className="secondary-button" onClick={loadAssignments}><RefreshCw size={14} /> Giao lại</button>}
      />
      <div className="panel-body">
        {message && <p className="form-error">{message}</p>}

        {/* Mỗi nhân viên: đang nhận bao nhiêu / tối đa */}
        {data && data.staff.length > 0 && (
          <div className="staff-loads">
            {data.staff.map((s) => (
              <div key={s.staff_id} className={clsx("staff-load", s.ticket_ids.length >= s.max_tickets && "full")}>
                <div className="staff-load-head">
                  <span className="task-id">{s.staff_id}</span>
                  <strong>{s.name}</strong>
                  <span className="staff-count">{s.ticket_ids.length}/{s.max_tickets}</span>
                </div>
                <div className="load-bar"><i style={{ width: `${s.max_tickets ? (100 * s.ticket_ids.length) / s.max_tickets : 0}%` }} /></div>
                <small>{s.skills.join(", ") || "chưa có kỹ năng"}{s.ticket_ids.length > 0 && ` · ${s.ticket_ids.join(", ")}`}</small>
              </div>
            ))}
          </div>
        )}

        {data && data.unassigned > 0 && (
          <p className="unassigned-note"><UserX size={14} /> {data.unassigned}/{data.total} ticket chưa có nhân viên phù hợp để nhận.</p>
        )}

        {!data?.items.length ? (
          <Empty icon={Users} text="Hàng đợi trống, chưa có ticket nào để giao." />
        ) : (
          <div className="table-wrap">
            <table className="data-table assign-table">
              <thead><tr><th>#</th><th>Ticket</th><th>Hạn xử lý</th><th>Người nhận</th><th>Lý do</th></tr></thead>
              <tbody>
                {data.items.map((item) => (
                  <tr key={item.ticket_id} className={clsx(!item.staff_id && "unassigned")}>
                    <td className="pos">{item.position}</td>
                    <td><strong>{item.ticket_id}</strong> <SeverityPill value={item.severity} /><small>{item.intent}</small></td>
                    <td><span className="deadline"><Clock3 size={13} />{shortTime(item.deadline_at)}</span></td>
                    <td>{item.staff_id ? <><strong>{item.staff_name}</strong><small>{item.staff_id}</small></> : <span className="pill red">chưa có người</span>}</td>
                    <td className="reason">{item.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Sửa danh sách nhân viên */}
        <details className="staff-editor" open={rows.length === 0}>
          <summary>Danh sách nhân viên: kỹ năng và số việc tối đa {dirty && <em className="edited">chưa lưu</em>}</summary>
          <FieldIssues issues={generalIssues} />
          {rows.map((row) => (
            <div key={row.key} className="staff-row">
              <div className="staff-line">
                <span className={clsx("task-id", !row.staff_id && "pending")} title="Mã do backend cấp khi lưu">{row.staff_id ?? "mới"}</span>
                <input value={row.name} onChange={(e) => update(row.key, { name: e.target.value })} placeholder="Tên nhân viên" aria-label="Tên nhân viên" />
                <label className="max-input">tối đa
                  <input type="number" min={0} max={50} value={row.max_tickets} onChange={(e) => update(row.key, { max_tickets: Number(e.target.value) })} aria-label="Số việc tối đa" />
                </label>
                <button className="icon-only" onClick={() => setRows((r) => r.filter((x) => x.key !== row.key))} title="Xóa nhân viên" aria-label="Xóa nhân viên"><Trash2 size={14} /></button>
              </div>
              <div className="skill-chips">
                {INTENTS.map((skill) => (
                  <button key={skill} className={clsx(row.skills.includes(skill) && "on")} onClick={() => toggleSkill(row, skill)}>{skill}</button>
                ))}
              </div>
              <FieldIssues issues={rowIssues(row.key)} />
            </div>
          ))}
          <div className="toolbar">
            <button className="secondary-button" onClick={() => setRows((r) => [...r, ...toRows([{ staff_id: null, name: "", skills: [], max_tickets: 1 }])])}>
              <Plus size={14} /> Thêm nhân viên
            </button>
            <button className="primary-button" onClick={save} disabled={saving || !dirty}>
              {saving ? <Loader2 className="spin" size={16} /> : <Save size={16} />} Lưu và giao lại
            </button>
          </div>
        </details>
      </div>
    </section>
  );
}
