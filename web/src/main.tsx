import "@fontsource/be-vietnam-pro/400.css";
import "@fontsource/be-vietnam-pro/500.css";
import "@fontsource/be-vietnam-pro/600.css";
import "@fontsource/be-vietnam-pro/700.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import {
  ArrowUpRight,
  BarChart3,
  Headset,
  MessageCircle,
  ShieldCheck,
  Landmark,
  Sparkles,
} from "lucide-react";
import { CustomerChat } from "./pages/customer-chat";
import { StaffConsole } from "./pages/staff-console";
import { Benchmark } from "./pages/benchmark";
import { StaffBoundary } from "./staff-auth";
import { BrandMark } from "./ui";
import "./styles/tokens.css";
import "./styles/experience.css";
const pathname = window.location.pathname;
const page = pathname.startsWith("/staff")
  ? "staff"
  : pathname.startsWith("/benchmark")
    ? "benchmark"
    : "chat";
function App() {
  return (
    <div className={`app-shell ${page === "chat" ? "customer-shell" : ""}`}>
      <a className="skip-link" href="#main-content">
        Đến nội dung chính
      </a>
      <aside className="sidebar">
        <a
          href="/chat"
          className="brand"
          aria-label="Ngân hàng Tài Khoa Tuấn — hỗ trợ khách hàng"
        >
          <BrandMark />
          <div>
            <strong>TÀI KHOA TUẤN</strong>
            <small>NGÂN HÀNG</small>
          </div>
        </a>
        <div className="sidebar-content">
          <div className="nav-label">DỊCH VỤ KHÁCH HÀNG</div>
          <nav aria-label="Điều hướng chính">
            <a
              className={page === "chat" ? "nav-item active" : "nav-item"}
              href="/chat"
              aria-label="Hỗ trợ trực tuyến"
              aria-current={page === "chat" ? "page" : undefined}
            >
              <MessageCircle size={20} />
              <span>Hỗ trợ trực tuyến</span>
              {page === "chat" && <span className="nav-current" />}
            </a>
          </nav>
          <div className="sidebar-note">
            <div className="sidebar-note-icon"><Landmark size={22} /></div>
            <span className="sidebar-kicker">CHUYỆN CỦA BẠN</span>
            <p>
              Mỗi bước nhỏ.
              <br />
              Một tương lai lớn.
            </p>
            <div className="sidebar-sculpture" aria-hidden="true"><i /><i /><i /></div>
            <div className="continuity-dots" aria-hidden="true"><i /><i /><i /><span /></div>
          </div>
          {page !== "chat" && (
            <>
              <div className="nav-label team-label">DÀNH CHO ĐỘI NGŨ</div>
              <nav aria-label="Không gian chuyên viên">
                <a
                  className={page === "staff" ? "nav-item active" : "nav-item"}
                  href="/staff"
                  aria-label="Chuyên viên"
                  aria-current={page === "staff" ? "page" : undefined}
                >
                  <Headset size={20} />
                  <span>Chuyên viên</span>
                </a>
                <a
                  className={
                    page === "benchmark" ? "nav-item active" : "nav-item"
                  }
                  href="/benchmark"
                  aria-label="Đánh giá hệ thống"
                  aria-current={page === "benchmark" ? "page" : undefined}
                >
                  <BarChart3 size={20} />
                  <span>Đánh giá hệ thống</span>
                </a>
              </nav>
            </>
          )}
        </div>
        <div className="sidebar-bottom">
          <div className="sidebar-security">
            <ShieldCheck size={20} />
            <span>Bảo mật thông tin khách hàng</span>
          </div>
          <div className="demo-badge">
            <ShieldCheck size={14} />
            <span>Ngân hàng giả định</span>
          </div>
        </div>
      </aside>
      <div className="main-column">
        <header className="topbar">
          <div className="breadcrumb">
            Tài Khoa Tuấn <span>/</span>{" "}
            <strong>
              {page === "chat"
                ? "Hỗ trợ khách hàng"
                : page === "staff"
                  ? "Chuyên viên"
                  : "Đánh giá hệ thống"}
            </strong>
          </div>
          <div className="topbar-right">
            <span className="demo-top-label">
              <Sparkles size={14} /> Trợ lý tài chính cá nhân
            </span>
            <a
              href={page === "chat" ? "/staff" : "/chat"}
              className="topbar-link"
              aria-label={page === "chat" ? "Dành cho chuyên viên" : "Hỗ trợ khách hàng"}
              data-short-label={page === "chat" ? "Đội ngũ" : "Trò chuyện"}
            >
              <span>{page === "chat" ? "Dành cho chuyên viên" : "Hỗ trợ khách hàng"}</span>
              <ArrowUpRight size={15} />
            </a>
          </div>
        </header>
        <main id="main-content">
          {page === "chat" ? (
            <CustomerChat />
          ) : (
            <StaffBoundary>
              {page === "staff" ? <StaffConsole /> : <Benchmark />}
            </StaffBoundary>
          )}
        </main>
      </div>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
