// Component nhỏ dùng chung, lấy từ buổi 4 (PanelHeader, Empty, Metric) và thêm vài cái mới.
import type { LucideIcon } from "lucide-react";
import { AlertTriangle, CircleAlert, Inbox } from "lucide-react";
import clsx from "clsx";
import type { Issue, Severity } from "@/lib/types";

export function PanelHeader({ step, title, subtitle, action }: { step?: number; title: string; subtitle: string; action?: React.ReactNode }) {
  return (
    <div className="panel-header">
      <div className="panel-title">
        {step !== undefined && <span className="step">{step}</span>}
        <div><h3>{title}</h3><p>{subtitle}</p></div>
      </div>
      {action}
    </div>
  );
}

export function Empty({ text, icon: Icon = Inbox }: { text: string; icon?: LucideIcon }) {
  return <div className="empty"><Icon size={25} /><span>{text}</span></div>;
}

export function Metric({ icon: Icon, label, value, hint, tone }: { icon: LucideIcon; label: string; value: string; hint?: string; tone: "violet" | "blue" | "green" | "amber" | "red" }) {
  return (
    <article className="metric-card">
      <div className={`metric-icon ${tone}`}><Icon size={20} /></div>
      <p>{label}</p>
      <div className="metric-value"><strong>{value}</strong>{hint && <span>{hint}</span>}</div>
    </article>
  );
}

const SEVERITY_TONE: Record<Severity, string> = { critical: "red", high: "amber", medium: "blue", low: "gray" };

export function SeverityPill({ value }: { value: string }) {
  const tone = SEVERITY_TONE[value as Severity] ?? "red";
  return <span className={`pill ${tone}`}>{value || "—"}</span>;
}

/** Danh sách lỗi/cảnh báo hiện ngay dưới một ô. */
export function FieldIssues({ issues }: { issues: Issue[] }) {
  if (!issues.length) return null;
  return (
    <ul className="field-issues">
      {issues.map((issue, i) => (
        <li key={i} className={clsx("issue", issue.level)}>
          {issue.level === "error" ? <CircleAlert size={13} /> : <AlertTriangle size={13} />}
          {issue.message}
        </li>
      ))}
    </ul>
  );
}

export function JsonBox({ title, value }: { title: string; value: unknown }) {
  return (
    <div className="json-box">
      <span>{title}</span>
      <pre>{value === null || value === undefined ? "null" : JSON.stringify(value, null, 2)}</pre>
    </div>
  );
}
