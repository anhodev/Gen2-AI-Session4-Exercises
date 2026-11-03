"use client";

// Khung trang lấy từ buổi 4: sidebar tối + topbar + nội dung theo từng màn hình.
import { useEffect, useState } from "react";
import {
  AlertTriangle, Bot, Command, Database, FileSearch, GitBranch, History, Inbox, Menu, Truck, X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { api } from "@/lib/api";
import type { Health } from "@/lib/types";
import TicketWorkspace from "@/components/TicketWorkspace";
import DockWorkspace from "@/components/DockWorkspace";
import ExpenseWorkspace from "@/components/ExpenseWorkspace";
import PlanWorkspace from "@/components/PlanWorkspace";
import RunHistory from "@/components/RunHistory";

type View = "tickets" | "docks" | "expenses" | "plans" | "history";

const NAV: { id: View; label: string; icon: LucideIcon; ready: boolean }[] = [
  { id: "tickets", label: "01 · Ưu tiên ticket", icon: Inbox, ready: true },
  { id: "docks", label: "02 · Giờ nhận xe", icon: Truck, ready: true },
  { id: "expenses", label: "03 · Hóa đơn trùng", icon: FileSearch, ready: true },
  { id: "plans", label: "04 · Thứ tự công việc", icon: GitBranch, ready: true },
];

const TITLES: Record<View, [string, string]> = {
  tickets: ["Khách hàng nào cần được hỗ trợ trước?", "GenAI đọc tin nhắn và điền mẫu JSON. Code tính hạn xử lý (SLA) và xếp hàng đợi bằng min-heap."],
  docks: ["Xe giao hàng nên vào cửa kho lúc mấy giờ?", "GenAI đọc email đặt lịch. Code gộp khoảng bận của từng cửa và chọn cửa trống sớm nhất. Giờ hiển thị theo múi giờ kho."],
  expenses: ["Hóa đơn này đã được gửi trước đó chưa?", "GenAI đọc chứng từ. Code tạo nhãn tra cứu và dùng hash map tìm bản có thể trùng. Người duyệt quyết định, hệ thống không tự kết luận gian lận."],
  plans: ["Việc nào phải làm trước?", "GenAI đọc biên bản họp, tìm việc và quan hệ trước-sau kèm câu bằng chứng. Code xếp thứ tự bằng topological sort và báo khi các việc chờ nhau thành vòng."],
  history: ["Lịch sử xử lý", "Nội dung gốc, JSON GenAI trả, bản người dùng sửa, kết quả cuối và thời gian xử lý."],
};

export default function Home() {
  const [view, setView] = useState<View>("tickets");
  const [mobileNav, setMobileNav] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [connected, setConnected] = useState(true);

  useEffect(() => {
    api.health().then((h) => { setHealth(h); setConnected(true); }).catch(() => setConnected(false));
  }, []);

  const navigate = (next: View) => { setView(next); setMobileNav(false); };

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileNav ? "open" : ""}`}>
        <div className="brand">
          <div className="brand-mark"><Command size={20} /></div>
          <div><strong>Ho Xuan An</strong><span>BÀI TẬP BUỔI 4</span></div>
          <button className="sidebar-close" onClick={() => setMobileNav(false)}><X size={18} /></button>
        </div>
        <nav>
          <p className="nav-heading">BÀI TẬP</p>
          {NAV.map(({ id, label, icon: Icon, ready }) => (
            <button key={id} onClick={() => ready && navigate(id)} className={view === id ? "active" : ""} disabled={!ready}>
              <Icon size={18} /><span>{label}</span>{!ready && <em>Sắp có</em>}
            </button>
          ))}
          <p className="nav-heading">THEO DÕI</p>
          <button onClick={() => navigate("history")} className={view === "history" ? "active" : ""}>
            <History size={18} /><span>Lịch sử xử lý</span>
          </button>
        </nav>
        <div className="sidebar-spacer" />
        <div className="upgrade-card">
          <div className="glow" />
          <Bot size={22} />
          <strong>GenAI: {health?.llm ?? "…"}</strong>
          <span><Database size={11} /> Database: {health?.database ?? "…"}</span>
          <span>GenAI chỉ đọc và điền mẫu. Code kiểm tra và xếp thứ tự.</span>
        </div>
      </aside>
      {mobileNav && <button className="sidebar-overlay" onClick={() => setMobileNav(false)} aria-label="Đóng menu" />}

      <main className="main-content">
        <header className="topbar">
          <button className="icon-button mobile-menu" onClick={() => setMobileNav(true)} aria-label="Mở menu"><Menu size={20} /></button>
          <div>
            <p className="eyebrow">STRUCTURED OUTPUT × THUẬT TOÁN</p>
            <h1>{TITLES[view][0]}</h1>
            <p className="subtitle">{TITLES[view][1]}</p>
          </div>
        </header>
        {!connected && (
          <div className="offline-banner"><AlertTriangle size={16} /> Chưa kết nối được backend. Chạy FastAPI ở cổng 8000: <code>cd backend && myenv/bin/uvicorn app.main:app --reload --reload-dir app</code></div>
        )}
        {view === "tickets" && <TicketWorkspace llmMode={health?.llm ?? "mock"} />}
        {view === "docks" && <DockWorkspace llmMode={health?.llm ?? "mock"} />}
        {view === "expenses" && <ExpenseWorkspace llmMode={health?.llm ?? "mock"} />}
        {view === "plans" && <PlanWorkspace llmMode={health?.llm ?? "mock"} />}
        {view === "history" && <RunHistory />}
      </main>
    </div>
  );
}
