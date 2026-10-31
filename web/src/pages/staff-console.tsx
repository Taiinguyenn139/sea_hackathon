import { BriefView, TraceView } from "../brief-view";
import { useEffect, useState, type FormEvent } from "react";
import {
  ArrowRight,
  Check,
  ClipboardList,
  Clock3,
  FileSearch,
  Inbox,
  LogOut,
  RefreshCw,
  SearchCheck,
  ShieldCheck,
} from "lucide-react";
import {
  ApiError,
  loadTurns,
  errorMessage,
  request,
  type Callback,
  type Handoff,
  type Turn,
} from "../api";
import { useStaff } from "../staff-auth";
import { Dialog, Empty, ErrorBanner, PageHeading, Spinner } from "../ui";
export function StaffConsole() {
  const { csrf, logout, expire } = useStaff();
  const [items, setItems] = useState<Handoff[]>([]);
  const [callbacks, setCallbacks] = useState<Callback[]>([]);
  const [active, setActive] = useState<Handoff | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [notes, setNotes] = useState("");
  const [commitments, setCommitments] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [trace, setTrace] = useState<unknown>(null);
  const [traceOpen, setTraceOpen] = useState(false);
  const [tab, setTab] = useState<"handoffs" | "callbacks">("handoffs");
  function fail(e: unknown) {
    if (e instanceof ApiError && e.status === 401) {
      expire();
      return;
    }
    setError(errorMessage(e));
  }
  async function load() {
    setLoading(true);
    setError("");
    try {
      const [a, b] = await Promise.all([
        request<{ items: Handoff[] }>("/handoffs"),
        request<{ items: Callback[] }>("/callbacks"),
      ]);
      setItems(a.items);
      setCallbacks(b.items);
    } catch (e) {
      fail(e);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    void load();
  }, []);
  async function select(item: Handoff) {
    setBusy(true);
    setError("");
    setNotes("");
    setCommitments("");
    setTurns([]);
    try {
      const detail = await request<Handoff>(`/handoffs/${item.handoff_id}`);
      setActive(detail);
      const sessionId = detail.session_id ?? detail.from_session;
      if (sessionId) {
        const data = await loadTurns(sessionId);
        setTurns(data);
      }
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }
  async function complete(e: FormEvent) {
    e.preventDefault();
    if (!active) return;
    setBusy(true);
    setError("");
    try {
      await request(`/handoffs/${active.handoff_id}/complete`, {
        method: "POST",
        csrf,
        body: {
          notes,
          commitments: commitments
            .split("\n")
            .map((x) => x.trim())
            .filter(Boolean),
          idempotency_key: `complete-${active.handoff_id}`,
        },
      });
      setActive({ ...active, status: "completed" });
      setNotice("Đã hoàn tất và lưu nội dung bàn giao.");
      await load();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }
  async function completeCallback(item: Callback) {
    const id = item.callback_id ?? item.task_id ?? item.id;
    if (!id) return;
    setBusy(true);
    try {
      await request(`/callbacks/${id}/complete`, {
        method: "POST",
        csrf,
        body: { idempotency_key: `callback-${id}` },
      });
      setNotice("Đã đánh dấu cuộc hẹn hoàn tất.");
      await load();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }
  async function showTrace() {
    if (!active) return;
    const id = active.session_id ?? active.from_session;
    if (!id) return;
    setBusy(true);
    try {
      setTrace(await request(`/traces/${id}`));
      setTraceOpen(true);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(false);
    }
  }
  const pending = items.filter((x) => x.status !== "completed").length;
  return (
    <div className="workspace-page">
      <PageHeading
        eyebrow="KHÔNG GIAN CHUYÊN VIÊN"
        title="Mỗi khách hàng, một câu chuyện."
        description="Tiếp nhận đúng ngữ cảnh. Tiếp tục đúng điều khách hàng cần."
        action={
          <button
            className="button secondary"
            onClick={() => void logout().catch(fail)}
          >
            <LogOut size={16} /> Đăng xuất
          </button>
        }
      />
      <div className="workspace-toolbar">
        <div className="tabs">
          <button
            className={tab === "handoffs" ? "active" : ""}
            onClick={() => setTab("handoffs")}
          >
            <Inbox size={17} /> Yêu cầu chuyển tiếp <span>{pending}</span>
          </button>
          <button
            className={tab === "callbacks" ? "active" : ""}
            onClick={() => setTab("callbacks")}
          >
            <Clock3 size={17} /> Lịch gọi lại{" "}
            <span>
              {callbacks.filter((x) => x.status !== "completed").length}
            </span>
          </button>
        </div>
        <button
          className="icon-button"
          title="Làm mới"
          aria-label="Làm mới danh sách"
          onClick={load}
          disabled={loading}
        >
          <RefreshCw size={18} />
        </button>
      </div>
      {error && <ErrorBanner message={error} onRetry={load} />}
      {notice && (
        <div className="success-message" role="status">
          <Check size={17} />
          {notice}
        </div>
      )}
      {loading ? (
        <div className="page-loading">
          <Spinner label="Đang tải yêu cầu…" />
        </div>
      ) : tab === "callbacks" ? (
        <section className="panel">
          {!callbacks.length ? (
            <Empty icon={<Clock3 size={28} />} title="Chưa có lịch gọi lại">
              Những lời hẹn được ghi nhận trong cuộc trò chuyện sẽ xuất hiện tại
              đây.
            </Empty>
          ) : (
            <div className="callback-list">
              {callbacks.map((item) => (
                <article
                  className="callback-row"
                  key={item.callback_id ?? item.task_id ?? item.id}
                >
                  <span className="list-icon">
                    <Clock3 size={20} />
                  </span>
                  <div>
                    <strong>{item.customer_id ?? "Khách hàng"}</strong>
                    <p>
                      {item.due_at ?? item.time ?? "Chưa xác định thời gian"}
                    </p>
                  </div>
                  <span
                    className={`badge ${item.status === "completed" ? "success" : ""}`}
                  >
                    {item.status === "completed"
                      ? "Đã hoàn tất"
                      : "Cần gọi lại"}
                  </span>
                  <button
                    className="button secondary"
                    disabled={busy || item.status === "completed"}
                    onClick={() => void completeCallback(item)}
                  >
                    Hoàn tất
                  </button>
                </article>
              ))}
            </div>
          )}
        </section>
      ) : (
        <div className="staff-grid">
          <section className="handoff-list panel">
            <div className="panel-heading">
              <h2>Hàng đợi hỗ trợ</h2>
              <span className="mini-label">{items.length} YÊU CẦU</span>
            </div>
            {!items.length ? (
              <Empty icon={<Inbox size={28} />} title="Hàng đợi đang trống">
                Khi khách yêu cầu gặp chuyên viên, bản tóm tắt sẽ được chuyển
                tới đây.
              </Empty>
            ) : (
              items.map((item) => (
                <button
                  key={item.handoff_id}
                  className={`handoff-item ${active?.handoff_id === item.handoff_id ? "selected" : ""}`}
                  onClick={() => void select(item)}
                  disabled={busy}
                >
                  <div className="handoff-item-top">
                    <span
                      className={`badge ${item.status === "completed" ? "success" : "warning"}`}
                    >
                      {item.status === "completed"
                        ? "Đã hoàn tất"
                        : "Chờ tiếp nhận"}
                    </span>
                    <ArrowRight size={16} />
                  </div>
                  <strong>{item.reason || "Yêu cầu hỗ trợ"}</strong>
                  <span className="muted">
                    {item.customer_id ?? item.handoff_id.slice(0, 14)}
                  </span>
                </button>
              ))
            )}
          </section>
          <section className="handoff-detail panel">
            {!active ? (
              <Empty
                icon={<ClipboardList size={32} />}
                title="Sẵn sàng tiếp nối"
              >
                Chọn một yêu cầu để xem nhu cầu, những điểm cần xác nhận và bước
                tiếp theo.
              </Empty>
            ) : (
              <>
                <div className="panel-heading">
                  <div>
                    <div className="mini-label">CALL BRIEF</div>
                    <h2>Điều cần biết trước khi bắt đầu</h2>
                  </div>
                  <button
                    className="button secondary"
                    onClick={showTrace}
                    disabled={busy}
                  >
                    <FileSearch size={16} /> Xem nguồn
                  </button>
                </div>
                <div className="brief-content">
                  <div className="brief-reason">
                    <ShieldCheck size={18} />
                    {active.reason}
                  </div>
                  <BriefView brief={active.brief} />
                  {Boolean(active.open_questions?.length) && (
                    <div className="confirmation-box">
                      <strong>Cần xác nhận</strong>
                      <ul>
                        {active.open_questions?.map((q, i) => (
                          <li key={i}>{q}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {active.next_step && (
                    <div className="next-step">
                      <SearchCheck size={20} />
                      <div>
                        <span className="mini-label">BƯỚC TIẾP THEO</span>
                        <p>{active.next_step}</p>
                      </div>
                    </div>
                  )}
                  <details className="transcript">
                    <summary>
                      Hội thoại trong phiên <span>{turns.length} lượt</span>
                    </summary>
                    {turns.map((turn, i) => (
                      <div className="transcript-turn" key={i}>
                        <strong>
                          {turn.role === "user" ? "Khách hàng" : "Trợ lý"}
                        </strong>
                        <p>{turn.text}</p>
                      </div>
                    ))}
                  </details>
                  {active.status === "completed" ? (
                    <p className="success-message">
                      <Check size={16} /> Yêu cầu đã được hoàn tất.
                    </p>
                  ) : (
                    <form className="completion-form" onSubmit={complete}>
                      <h3>Hoàn tất hỗ trợ</h3>
                      <label htmlFor="notes">Ghi chú trao đổi</label>
                      <textarea
                        id="notes"
                        value={notes}
                        onChange={(e) => setNotes(e.target.value)}
                        required
                        rows={3}
                        placeholder="Tóm tắt những gì đã thống nhất với khách…"
                      />
                      <label htmlFor="commitments">
                        Cam kết tiếp theo{" "}
                        <span className="muted">
                          · không bắt buộc, mỗi dòng một mục
                        </span>
                      </label>
                      <textarea
                        id="commitments"
                        value={commitments}
                        onChange={(e) => setCommitments(e.target.value)}
                        rows={2}
                      />
                      <button
                        className="button primary"
                        disabled={busy || !notes.trim()}
                      >
                        {busy ? (
                          <Spinner label="Đang lưu…" />
                        ) : (
                          <>
                            Hoàn tất bàn giao <Check size={17} />
                          </>
                        )}
                      </button>
                    </form>
                  )}
                </div>
              </>
            )}
          </section>
        </div>
      )}
      <Dialog
        open={traceOpen}
        onClose={() => setTraceOpen(false)}
        title="Nguồn thông tin và dấu vết xử lý"
      >
        <p className="muted">
          Dữ liệu dành cho chuyên viên. Các tham chiếu giúp kiểm tra thông tin
          được sử dụng trong từng lượt.
        </p>
        <TraceView value={trace} />
      </Dialog>
    </div>
  );
}
