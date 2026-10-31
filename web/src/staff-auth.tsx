import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
  type FormEvent,
} from "react";
import { ArrowRight, LockKeyhole, ShieldCheck } from "lucide-react";
import { ApiError, errorMessage, request } from "./api";
import { BrandMark, ErrorBanner, Spinner } from "./ui";
type Auth = { csrf: string; logout: () => Promise<void>; expire: () => void };
const Context = createContext<Auth>({
  csrf: "",
  logout: async () => {},
  expire: () => {},
});
export function useStaff() {
  return useContext(Context);
}
export function StaffBoundary({ children }: { children: ReactNode }) {
  const [csrf, setCsrf] = useState("");
  const [loading, setLoading] = useState(true);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    request<{ csrf_token: string }>("/staff/me")
      .then((data) => setCsrf(data.csrf_token))
      .catch((e) => {
        if (!(e instanceof ApiError) || e.status !== 401)
          setError(errorMessage(e));
      })
      .finally(() => setLoading(false));
  }, []);
  async function login(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const data = await request<{ csrf_token: string }>("/staff/login", {
        method: "POST",
        body: { password },
      });
      setCsrf(data.csrf_token);
      setPassword("");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function logout() {
    await request("/staff/logout", { method: "POST", body: {}, csrf });
    setCsrf("");
  }
  if (loading)
    return (
      <div className="page-loading">
        <Spinner label="Đang kiểm tra phiên đăng nhập…" />
      </div>
    );
  if (!csrf)
    return (
      <div className="staff-entry">
        <section className="staff-entry-copy">
          <div className="eyebrow">KHÔNG GIAN CHUYÊN VIÊN</div>
          <h1>
            Tiếp nối câu chuyện.
            <br />
            <span>Hiểu trọn nhu cầu.</span>
          </h1>
          <p>
            Một bản tóm tắt rõ ràng, nguồn thông tin có thể kiểm tra và những
            việc cần làm tiếp theo.
          </p>
          <div className="entry-detail">
            <ShieldCheck size={22} />
            <span>Hồ sơ khách hàng chỉ dành cho người có quyền truy cập.</span>
          </div>
        </section>
        <form className="login-panel" onSubmit={login}>
          <BrandMark />
          <h2>Chào mừng trở lại</h2>
          <p>Đăng nhập để tiếp nhận yêu cầu và xem kết quả đánh giá.</p>
          <label htmlFor="staff-password">Mật khẩu chuyên viên</label>
          <div className="password-field">
            <LockKeyhole size={18} />
            <input
              id="staff-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              placeholder="Nhập mật khẩu được cấp"
            />
          </div>
          {error && <ErrorBanner message={error} />}
          <button className="button primary full" disabled={busy || !password}>
            {busy ? (
              <Spinner label="Đang đăng nhập…" />
            ) : (
              <>
                Đăng nhập <ArrowRight size={17} />
              </>
            )}
          </button>
          <small>Mật khẩu do người quản trị demo cấu hình.</small>
        </form>
      </div>
    );
  return (
    <Context.Provider value={{ csrf, logout, expire: () => setCsrf("") }}>
      {children}
    </Context.Provider>
  );
}
