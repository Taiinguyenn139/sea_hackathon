import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  ArrowLeft,
  ArrowUp,
  Check,
  CheckCheck,
  CircleHelp,
  CreditCard,
  Home,
  MessageSquarePlus,
  ShieldCheck,
  UserRound,
  Users,
  Wallet,
  Sparkles,
  PanelRight,
  MessageCircle,
} from "lucide-react";
import {
  ApiError,
  createChatSession,
  loadTurns,
  errorMessage,
  request,
  type ChatSession,
  type Reply,
  type Turn,
} from "../api";
import { BrandMark, ErrorBanner, Spinner } from "../ui";
import { BankingScene, ServiceArt } from "../banking-art";
const STORAGE = "bankwise-chat-session";
const LEGACY_STORAGE = "mach-chat-session";
const DRAFT_STORAGE = "bankwise-chat-draft";
function savedSession(): ChatSession | null {
  try {
    return JSON.parse(
      sessionStorage.getItem(STORAGE) ??
        sessionStorage.getItem(LEGACY_STORAGE) ??
        "null",
    );
  } catch {
    return null;
  }
}
const suggestions = [
  {
    icon: Home,
    title: "Vay mua nhà",
    subtitle: "Điều kiện và hồ sơ vay",
    message: "Tôi muốn tìm hiểu vay mua nhà.",
  },
  {
    icon: Wallet,
    title: "Vay tiêu dùng",
    subtitle: "Tư vấn khoản vay cá nhân",
    message: "Tôi muốn tìm hiểu vay tiêu dùng.",
  },
  {
    icon: CreditCard,
    title: "Thẻ tín dụng",
    subtitle: "Thông tin và đăng ký thẻ",
    message: "Tôi muốn tìm hiểu thẻ tín dụng.",
  },
];
export function CustomerChat() {
  const [view, setView] = useState<"home" | "conversation">(() =>
    window.location.hash === "#conversation" ? "conversation" : "home",
  );
  const [session, setSession] = useState<ChatSession | null>(savedSession);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState(
    () => sessionStorage.getItem(DRAFT_STORAGE) ?? "",
  );
  const [pending, setPending] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [lastFailed, setLastFailed] = useState<{
    text: string;
    id: string;
  } | null>(null);
  const [hint, setHint] = useState("");
  const [railOpen, setRailOpen] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const currentSession = useRef(session);
  const sending = useRef(false);
  const freshSession = useRef<string | null>(null);
  const verified = ["verified", "identified"].includes(
    session?.identity_status ?? "",
  );
  const waiting =
    Boolean(session?.handoff_id) && session?.status !== "completed";
  const isHome = view === "home";
  function navigate(next: "home" | "conversation") {
    setView(next);
    window.location.hash = next;
  }
  useEffect(() => {
    const syncView = () => {
      if (window.location.hash === "#conversation") setView("conversation");
      else if (["", "#home"].includes(window.location.hash)) setView("home");
    };
    window.addEventListener("hashchange", syncView);
    return () => window.removeEventListener("hashchange", syncView);
  }, []);
  useEffect(() => {
    if (isHome) document.getElementById("chat-home-title")?.focus();
    else input.current?.focus();
  }, [isHome]);
  useEffect(() => {
    if (!input.current) return;
    input.current.style.height = "auto";
    input.current.style.height = `${Math.min(input.current.scrollHeight, 150)}px`;
  }, [draft, verified, isHome]);
  function save(next: ChatSession | null) {
    currentSession.current = next;
    setSession(next);
    if (next) sessionStorage.setItem(STORAGE, JSON.stringify(next));
    else {
      sessionStorage.removeItem(STORAGE);
      sessionStorage.removeItem(LEGACY_STORAGE);
    }
  }
  useEffect(() => {
    if (draft) sessionStorage.setItem(DRAFT_STORAGE, draft);
    else sessionStorage.removeItem(DRAFT_STORAGE);
  }, [draft]);
  useEffect(() => {
    if (!session || freshSession.current === session.session_id) {
      setLoading(false);
      return;
    }
    let live = true;
    setLoading(true);
    loadTurns(session.session_id, session.session_token)
      .then((data) => {
        if (live) setTurns(data);
      })
      .catch((e) => {
        if (live) setError(errorMessage(e));
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [session?.session_id]);
  useEffect(() => {
    if (!isHome && (turns.length || pending))
      end.current?.scrollIntoView({
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "instant"
          : "smooth",
        block: "nearest",
      });
  }, [turns, pending, isHome]);
  useEffect(() => {
    if (!session?.handoff_id) return;
    const timer = setInterval(() => {
      request<ChatSession>(`/sessions/${session.session_id}`, {
        token: session.session_token,
      })
        .then((next) => {
          if (next.status === "completed" || next.status === "active") {
            save({
              ...session,
              ...next,
              session_token: session.session_token,
              handoff_id:
                next.status === "active" ? undefined : session.handoff_id,
            });
          }
        })
        .catch(() => {});
    }, 5000);
    return () => clearInterval(timer);
  }, [session?.handoff_id]);
  async function send(text = draft, id: string = crypto.randomUUID()) {
    if (!text.trim() || sending.current || pending || loading || waiting)
      return;
    sending.current = true;
    setPending(true);
    setError("");
    setLastFailed(null);
    navigate("conversation");
    try {
      let activeSession = currentSession.current;
      if (!activeSession) {
        activeSession = await createChatSession();
        freshSession.current = activeSession.session_id;
        save(activeSession);
        setTurns([]);
      }
      const data = await request<Reply>(
        `/sessions/${activeSession.session_id}/messages`,
        {
          method: "POST",
          body: { text: text.trim(), client_message_id: id },
          token: activeSession.session_token,
        },
      );
      setTurns((prev) => [
        ...prev,
        { role: "user", text: text.trim() },
        { role: "assistant", text: data.reply },
      ]);
      setDraft("");
      if (
        data.identity_status ||
        data.user_id !== undefined ||
        data.handoff_id ||
        data.brief
      )
        save({
          ...activeSession,
          identity_status:
            data.identity_status ?? activeSession.identity_status,
          user_id:
            data.user_id !== undefined ? data.user_id : activeSession.user_id,
          brief: data.brief ?? activeSession.brief,
          handoff_id: data.handoff_id ?? activeSession.handoff_id,
          status: data.handoff_id ? "waiting_human" : activeSession.status,
        });
    } catch (e) {
      setError(errorMessage(e));
      setLastFailed({ text, id });
    } finally {
      sending.current = false;
      setPending(false);
    }
  }
  async function handoff() {
    if (!session) return;
    setPending(true);
    setError("");
    try {
      const data = await request<{ handoff_id: string }>(
        `/sessions/${session.session_id}/handoff`,
        {
          method: "POST",
          body: { reason: "Khách yêu cầu gặp chuyên viên" },
          token: session.session_token,
        },
      );
      save({
        ...session,
        handoff_id: data.handoff_id,
        status: "waiting_human",
      });
      setHint("Đã chuyển yêu cầu và bản tóm tắt tới chuyên viên.");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setPending(false);
    }
  }
  async function newChat() {
    if (session) {
      setPending(true);
      try {
        await request(`/sessions/${session.session_id}/end`, {
          method: "POST",
          body: {},
          token: session.session_token,
        });
      } catch (e) {
        if (!(e instanceof ApiError) || ![401, 403, 404].includes(e.status)) {
          setError(errorMessage(e));
          setPending(false);
          return;
        }
      }
    }
    save(null);
    freshSession.current = null;
    setTurns([]);
    setLoading(false);
    setDraft("");
    setError("");
    setHint("");
    setLastFailed(null);
    setPending(false);
    navigate("home");
  }
  function selectPrompt(message: string) {
    setDraft(message);
    void send(message);
  }
  return (
    <div className={`chat-layout ${railOpen ? "rail-open" : ""}`}>
      <section
        className={`chat-main ${isHome ? "is-welcome" : ""}`}
        aria-label="Hỗ trợ khách hàng ngân hàng"
      >
        {!isHome && (
          <nav className="chat-navigation" aria-label="Điều hướng hội thoại">
            <a
              href="#home"
              className="home-return"
              aria-label="Về màn hình chính"
            >
              <ArrowLeft size={17} /> Trang chủ
            </a>
            <span>Cuộc trò chuyện của bạn</span>
          </nav>
        )}
        <header className="chat-heading">
          <div className="assistant-identity">
            <BrandMark small />
            <div>
              <h1>Trợ lý Bankwise</h1>
              <p>
                <span className="status-dot" />{" "}
                {waiting
                  ? "Đang chờ chuyên viên"
                  : verified
                    ? "Sẵn sàng tiếp nối cùng bạn"
                    : "Cùng bạn trong từng quyết định"}
              </p>
            </div>
          </div>
          <div className="chat-header-actions">
            <button
              className="icon-button context-toggle"
              onClick={() => setRailOpen(!railOpen)}
              aria-label={
                railOpen ? "Ẩn thông tin hỗ trợ" : "Hiện thông tin hỗ trợ"
              }
              aria-expanded={railOpen}
              aria-controls="support-context"
              title="Thông tin hỗ trợ"
            >
              <PanelRight size={20} />
            </button>
            <button
              className="icon-button"
              onClick={handoff}
              disabled={!verified || pending || waiting}
              aria-label="Gặp chuyên viên"
              title="Gặp chuyên viên"
            >
              <Users size={20} />
            </button>
            <button
              className="icon-button"
              onClick={newChat}
              disabled={pending || !session}
              aria-label="Bắt đầu phiên mới"
              title="Bắt đầu phiên mới"
            >
              <MessageSquarePlus size={20} />
            </button>
          </div>
        </header>
        {error && (
          <div className="chat-feedback">
            <ErrorBanner
              message={error}
              onRetry={
                lastFailed
                  ? () => void send(lastFailed.text, lastFailed.id)
                  : undefined
              }
            />
          </div>
        )}
        <div className="conversation-scroll">
          {isHome && (
            <section className="welcome">
              <div className="welcome-hero">
                <div className="hero-copy">
                  <div className="eyebrow welcome-tag">
                    <span /> TÀI CHÍNH CÁ NHÂN, THẬT GẦN GŨI
                  </div>
                  <h2 id="chat-home-title" tabIndex={-1}>
                    Mỗi dự định.
                    <br />
                    <em>Một khởi đầu mới.</em>
                  </h2>
                  <p>
                    Từ ngôi nhà đầu tiên đến những kế hoạch tiếp theo.
                    <br className="desktop-break" /> Cùng trò chuyện để tìm
                    hướng đi phù hợp với bạn.
                  </p>
                  <div className="hero-signature">
                    <span className="signature-mark">
                      <Sparkles size={15} />
                    </span>{" "}
                    Lắng nghe hôm nay. Tiếp nối ngày mai.
                  </div>
                </div>
                <BankingScene />
              </div>
              <div className="services-heading">
                <div>
                  <span className="section-index">01 / KHÁM PHÁ</span>
                  <h3>Chúng tôi có thể hỗ trợ gì cho bạn?</h3>
                </div>
                <span className="services-hint">
                  Chọn điều bạn quan tâm <ArrowRight size={15} />
                </span>
              </div>
              <div className="suggestions">
                {suggestions.map(
                  ({ icon: Icon, title, subtitle, message }, index) => (
                    <button
                      key={title}
                      className={`suggestion suggestion-${index} ${draft === message ? "is-selected" : ""}`}
                      onClick={() => selectPrompt(message)}
                      aria-pressed={draft === message}
                      disabled={pending || loading || waiting}
                    >
                      <span className="service-category">
                        <Icon size={15} />
                        <span>0{index + 1}</span>
                      </span>
                      <ServiceArt kind={index} />
                      <span className="service-copy">
                        <strong>{title}</strong>
                        <span>{subtitle}</span>
                      </span>
                      <span className="service-cta">
                        {draft === message ? "Đã chọn" : "Khám phá"}
                        <ArrowRight size={17} />
                      </span>
                    </button>
                  ),
                )}
              </div>
            </section>
          )}
          {isHome && session && (
            <section
              className="session-resume"
              aria-label="Cuộc trò chuyện hiện tại"
            >
              <div className="resume-icon">
                <MessageCircle size={24} />
              </div>
              <div>
                <span className="section-index">TIẾP NỐI CUỘC TRÒ CHUYỆN</span>
                <h3>
                  {waiting
                    ? "Yêu cầu của bạn đang chờ chuyên viên."
                    : "Sẵn sàng tiếp tục khi bạn muốn."}
                </h3>
                <p>Nội dung hội thoại và câu hỏi đang soạn vẫn được giữ lại.</p>
              </div>
              <button
                className="button primary"
                onClick={() => navigate("conversation")}
              >
                Tiếp tục trò chuyện <ArrowRight size={16} />
              </button>
            </section>
          )}
          {!isHome && !turns.length && !loading && (
            <div className="conversation-empty">
              <span className="resume-icon">
                <MessageCircle size={26} />
              </span>
              <h2>Bạn muốn trao đổi điều gì?</h2>
              <p>Gửi câu hỏi để bắt đầu. Trợ lý đã sẵn sàng hỗ trợ bạn.</p>
            </div>
          )}
          {!isHome && loading && <Spinner label="Đang khôi phục hội thoại…" />}
          {!isHome && !!turns.length && (
            <div className="messages" role="log" aria-label="Lịch sử hội thoại">
              {turns.map((turn, index) => (
                <article
                  className={`message ${turn.role === "user" ? "from-user" : "from-assistant"}`}
                  key={turn.turn_id ?? turn.id ?? index}
                >
                  {turn.role !== "user" && <BrandMark small />}
                  <div>
                    <div className="message-label">
                      {turn.role === "user" ? "Bạn" : "Trợ lý Bankwise"}
                    </div>
                    <div className="message-bubble">{turn.text}</div>
                    {turn.role === "user" && (
                      <span className="delivered">
                        <CheckCheck size={13} /> Đã gửi
                      </span>
                    )}
                  </div>
                </article>
              ))}
            </div>
          )}
          {!isHome && pending && (
            <div className="typing" role="status">
              <BrandMark small />
              <span className="typing-dots" aria-hidden="true">
                <i />
                <i />
                <i />
              </span>
              <span>Trợ lý đang xử lý yêu cầu…</span>
            </div>
          )}
          {!isHome && waiting && (
            <div className="handoff-notice">
              <Users size={20} />
              <div>
                <strong>Đang chờ chuyên viên tiếp nhận</strong>
                <p>
                  Yêu cầu của bạn đã được gửi. Vui lòng chờ chuyên viên hỗ trợ.
                </p>
              </div>
            </div>
          )}
          {!isHome && hint && (
            <p className="success-message" role="status">
              <Check size={16} />
              {hint}
            </p>
          )}
          <div ref={end} />
        </div>
        {(!isHome || !session) && (
          <div className="composer-area">
            {isHome && (
              <div className="conversation-intro">
                <span className="section-index">02 / KẾT NỐI</span>
                <strong>Câu chuyện của bạn bắt đầu tại đây.</strong>
                <span>
                  <ShieldCheck size={14} /> Có thể hỏi thông tin công khai
                </span>
              </div>
            )}
            {verified && !waiting && !pending && turns.length > 0 && (
              <div
                className="quick-prompts"
                aria-label="Gợi ý tiếp tục hội thoại"
              >
                <button
                  onClick={() =>
                    selectPrompt("Tôi muốn hỏi thêm về lãi suất sau ưu đãi.")
                  }
                >
                  Lãi sau ưu đãi <ArrowRight size={13} />
                </button>
                <button
                  onClick={() =>
                    selectPrompt("Tôi cần chuẩn bị những giấy tờ nào?")
                  }
                >
                  Hồ sơ cần chuẩn bị <ArrowRight size={13} />
                </button>
              </div>
            )}
            <form
              className="composer"
              onSubmit={(e) => {
                e.preventDefault();
                void send();
              }}
            >
              <textarea
                ref={input}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (
                    e.key === "Enter" &&
                    !e.shiftKey &&
                    !e.nativeEvent.isComposing
                  ) {
                    e.preventDefault();
                    void send();
                  }
                }}
                rows={2}
                maxLength={4000}
                disabled={waiting || pending || loading}
                aria-label="Tin nhắn cho trợ lý"
                placeholder={
                  waiting
                    ? "Chuyên viên sẽ tiếp nhận yêu cầu của bạn…"
                    : "Nhập câu hỏi cần hỗ trợ…"
                }
              />
              <div className="composer-footer">
                <span>
                  <Sparkles size={14} />{" "}
                  {verified
                    ? "Enter để gửi · Shift + Enter để xuống dòng"
                    : "Câu chuyện của bạn bắt đầu từ đây"}
                </span>
                <button
                  className="send-button"
                  disabled={!draft.trim() || pending || loading || waiting}
                  aria-label="Gửi tin nhắn"
                >
                  <ArrowUp size={21} />
                </button>
              </div>
            </form>
            <p className="composer-disclaimer">
              Không chia sẻ mật khẩu, PIN hoặc OTP. Đây là ngân hàng giả định;
              mọi hồ sơ và sản phẩm đều hư cấu.
            </p>
          </div>
        )}
      </section>
      <aside
        className="context-rail"
        id="support-context"
        hidden={!railOpen}
        aria-label="Thông tin hỗ trợ"
      >
        <div className="rail-feature">
          <span className="rail-feature-icon">
            <MessageCircle size={23} />
          </span>
          <span className="mini-label">MỘT CUỘC TRÒ CHUYỆN LIỀN MẠCH</span>
          <h2>
            Thêm thấu hiểu.
            <br />
            Bớt những lần hỏi lại.
          </h2>
          <p>
            Tiếp tục điều bạn quan tâm, với thông tin được kết nối qua từng cuộc
            trò chuyện.
          </p>
          <div className="rail-feature-art" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
        </div>
        <div className="rail-heading">
          <span className="mini-label">KHÁM PHÁ DỊCH VỤ</span>
          <div className="line-accent" />
        </div>
        <div className="bank-services">
          {[
            {
              icon: Wallet,
              title: "Vay tiêu dùng",
              message: "Tôi muốn tìm hiểu vay tiêu dùng.",
            },
            {
              icon: CreditCard,
              title: "Dịch vụ thẻ",
              message: "Tôi cần hỗ trợ về dịch vụ thẻ.",
            },
            {
              icon: Home,
              title: "Sản phẩm vay",
              message: "Tôi muốn được tư vấn về các sản phẩm vay.",
            },
          ].map(({ icon: Icon, title, message }) => (
            <button
              key={title}
              onClick={() => selectPrompt(message)}
              disabled={pending || loading || waiting}
            >
              <Icon size={19} />
              <span>{title}</span>
              <ArrowRight size={15} />
            </button>
          ))}
        </div>
        <button
          className="human-help"
          onClick={handoff}
          disabled={!verified || pending || waiting}
        >
          <UserRound size={20} />
          <span>
            <strong>Liên hệ chuyên viên</strong>
            <small>Hỗ trợ yêu cầu của bạn</small>
          </span>
          <ArrowRight size={17} />
        </button>
        <div className="rail-bottom">
          <CircleHelp size={15} />
          <span>Không yêu cầu mật khẩu hay mã OTP trong chat.</span>
        </div>
      </aside>
    </div>
  );
}
