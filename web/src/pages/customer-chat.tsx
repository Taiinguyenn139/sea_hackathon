import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  ArrowRight,
  ArrowLeft,
  ArrowUp,
  Check,
  CheckCheck,
  Clock3,
  RotateCcw,
  History,
  X,
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
import {
  ChatHistory,
  CHAT_HISTORY,
  CURRENT_SESSION,
  conversation,
  readChatHistory,
  type Conversation,
} from "../chat-history";
const DRAFT_PREFIX = "bankwise-chat-draft:";
function readDraft(id?: string): string {
  try {
    return sessionStorage.getItem(DRAFT_PREFIX + (id ?? "anonymous")) ?? "";
  } catch {
    return "";
  }
}
type ChatTurn = Turn & {
  clientId?: string;
  delivery?: "sending" | "sent" | "failed";
};
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
  const [initial] = useState(() => readChatHistory(sessionStorage));
  const [entries, setEntries] = useState(initial.entries);
  const [historyHost, setHistoryHost] = useState<HTMLElement | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const historyDialog = useRef<HTMLDialogElement>(null);
  const historyTrigger = useRef<HTMLButtonElement>(null);
  const focusConversation = useRef(false);
  const [storageError, setStorageError] = useState("");
  const [historyError, setHistoryError] = useState("");
  const [historyRetry, setHistoryRetry] = useState(0);
  const [view, setView] = useState<"home" | "conversation">(() =>
    window.location.hash === "#conversation" ? "conversation" : "home",
  );
  const [session, setSession] = useState<ChatSession | null>(initial.current);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [draft, setDraft] = useState(() => {
    const saved = readDraft(initial.current?.session_id);
    try {
      return saved || sessionStorage.getItem("bankwise-chat-draft") || "";
    } catch {
      return saved;
    }
  });
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
  const sending = useRef(false);
  const currentSession = useRef(session);
  const freshSession = useRef<string | null>(null);
  const cached = useRef(
    new Map<
      string,
      {
        turns: ChatTurn[];
        draft: string;
        loaded: boolean;
        error: string;
        lastFailed: typeof lastFailed;
      }
    >(),
  );
  const verified = ["verified", "identified"].includes(
    session?.identity_status ?? "",
  );
  const waiting =
    Boolean(session?.handoff_id) && session?.status !== "completed";
  const ended = session?.status === "ended" || session?.status === "completed";
  const currentEntry = entries.find(
    (entry) => entry.session.session_id === session?.session_id,
  );
  const isHome = view === "home";
  useEffect(() => {
    setHistoryHost(document.getElementById("customer-history"));
  }, []);
  useEffect(() => {
    if (historyOpen) historyDialog.current?.showModal();
    else if (historyDialog.current?.open) {
      historyDialog.current.close();
      if (focusConversation.current)
        input.current?.focus({ preventScroll: true });
      else historyTrigger.current?.focus();
      focusConversation.current = false;
    }
  }, [historyOpen]);
  useEffect(() => {
    try {
      sessionStorage.setItem(CHAT_HISTORY, JSON.stringify(entries));
      if (session)
        sessionStorage.setItem(CURRENT_SESSION, JSON.stringify(session));
      else sessionStorage.removeItem(CURRENT_SESSION);
      sessionStorage.removeItem("mach-chat-session");
      setStorageError("");
    } catch {
      setStorageError(
        "Trình duyệt chưa lưu được danh sách phiên. Lịch sử có thể không khôi phục sau khi tải lại trang.",
      );
    }
  }, [entries, session]);
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
    if (isHome)
      document
        .getElementById("chat-home-title")
        ?.focus({ preventScroll: true });
    else if (
      !pending &&
      !loading &&
      !waiting &&
      !ended &&
      !historyOpen &&
      !historyError
    )
      input.current?.focus({ preventScroll: true });
  }, [isHome, pending, loading, waiting, ended, historyError]);
  useEffect(() => {
    if (!input.current) return;
    input.current.style.height = "auto";
    input.current.style.height = `${Math.min(input.current.scrollHeight, 150)}px`;
  }, [draft, verified, isHome]);
  function save(next: ChatSession | null) {
    currentSession.current = next;
    setSession(next);
    if (next)
      setEntries((prev) =>
        prev.some((entry) => entry.session.session_id === next.session_id)
          ? prev.map((entry) =>
              entry.session.session_id === next.session_id
                ? { ...entry, session: next }
                : entry,
            )
          : [conversation(next), ...prev],
      );
  }
  function titleFromMessage(id: string, text: string, touch = true) {
    setEntries((prev) =>
      prev.map((entry) =>
        entry.session.session_id === id
          ? {
              ...entry,
              title:
                !entry.renamed && entry.title === "Cuộc trò chuyện mới"
                  ? text.trim().slice(0, 80)
                  : entry.title,
              updatedAt: touch ? new Date().toISOString() : entry.updatedAt,
            }
          : entry,
      ),
    );
  }
  function rememberView() {
    if (session)
      cached.current.set(session.session_id, {
        turns,
        draft,
        loaded: !loading && !historyError,
        error,
        lastFailed,
      });
  }
  function openConversation(next: ChatSession) {
    if (pending || sending.current) return;
    if (next.session_id !== session?.session_id) {
      rememberView();
      const previous = cached.current.get(next.session_id);
      setTurns(previous?.turns ?? []);
      setDraft(previous?.draft ?? readDraft(next.session_id));
      setError(previous?.error ?? "");
      setLastFailed(previous?.lastFailed ?? null);
      setLoading(!previous?.loaded);
      setHistoryError("");
      setHint("");
      save(next);
    }
    focusConversation.current = true;
    setHistoryOpen(false);
    navigate("conversation");
  }
  useEffect(() => {
    try {
      const key = DRAFT_PREFIX + (session?.session_id ?? "anonymous");
      if (draft) sessionStorage.setItem(key, draft);
      else sessionStorage.removeItem(key);
      sessionStorage.removeItem("bankwise-chat-draft");
    } catch {
      setStorageError(
        "Trình duyệt chưa lưu được bản nháp. Hãy giữ tab này trong khi trao đổi.",
      );
    }
  }, [session?.session_id, draft]);
  useEffect(() => {
    if (
      !session ||
      freshSession.current === session.session_id ||
      cached.current.get(session.session_id)?.loaded
    ) {
      setLoading(false);
      return;
    }
    let live = true;
    setLoading(true);
    setHistoryError("");
    loadTurns(session.session_id, session.session_token)
      .then((data) => {
        if (live) {
          setTurns(data);
          const first = data.find((turn) => turn.role === "user");
          if (first) titleFromMessage(session.session_id, first.text, false);
        }
      })
      .catch((e) => {
        if (live) setHistoryError(errorMessage(e));
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [session?.session_id, historyRetry]);
  useEffect(() => {
    if (!session || pending) return;
    let live = true;
    request<ChatSession>(`/sessions/${session.session_id}`, {
      token: session.session_token,
    })
      .then((next) => {
        if (
          live &&
          ["active", "waiting_human", "completed", "ended"].includes(
            next.status ?? "",
          )
        )
          save({
            ...session,
            ...next,
            session_token: session.session_token,
            demo_profile: session.demo_profile,
          });
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [session?.session_id, pending]);
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
    if (!session?.handoff_id || ended) return;
    let live = true;
    const timer = setInterval(() => {
      request<ChatSession>(`/sessions/${session.session_id}`, {
        token: session.session_token,
      })
        .then((next) => {
          if (!live) return;
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
    return () => {
      live = false;
      clearInterval(timer);
    };
  }, [session?.session_id, session?.handoff_id, ended]);
  async function send(text = draft, id: string = crypto.randomUUID()) {
    if (
      !text.trim() ||
      pending ||
      loading ||
      historyError ||
      ended ||
      waiting ||
      sending.current
    )
      return;
    // Lock synchronously: repeated Enter events must share one in-flight request.
    sending.current = true;
    setPending(true);
    setError("");
    setLastFailed(null);
    navigate("conversation");
    const message = text.trim();
    const retrying = turns.some((turn) => turn.clientId === id);
    setTurns((prev) =>
      retrying
        ? prev.map((turn) =>
            turn.clientId === id ? { ...turn, delivery: "sending" } : turn,
          )
        : [
            ...prev,
            {
              id: `local-${id}`,
              clientId: id,
              role: "user",
              text: message,
              delivery: "sending",
            },
          ],
    );
    // Keep a different, unsent draft when retrying an older failed message.
    if (!retrying) setDraft("");
    try {
      let activeSession = currentSession.current;
      if (!activeSession) {
        activeSession = await createChatSession();
        freshSession.current = activeSession.session_id;
        save(activeSession);
      }
      const data = await request<Reply>(
        `/sessions/${activeSession.session_id}/messages`,
        {
          method: "POST",
          body: { text: message, client_message_id: id },
          token: activeSession.session_token,
        },
      );
      setTurns((prev) => [
        ...prev.map((turn): ChatTurn =>
          turn.clientId === id ? { ...turn, delivery: "sent" } : turn,
        ),
        { id: `reply-${id}`, role: "assistant", text: data.reply },
      ]);
      if (
        !data.guard_events?.some(
          (event) =>
            !!event &&
            typeof event === "object" &&
            "stage" in event &&
            event.stage === "input" &&
            "action" in event &&
            event.action === "block",
        )
      )
        titleFromMessage(activeSession.session_id, message);
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
      setTurns((prev) =>
        prev.map((turn) =>
          turn.clientId === id ? { ...turn, delivery: "failed" } : turn,
        ),
      );
      setError(errorMessage(e));
      setLastFailed({ text: message, id });
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
    if (pending || sending.current) return;
    setPending(true);
    setError("");
    try {
      let next: ChatSession;
      if (session) {
        try {
          next = await request<ChatSession>(
            `/sessions/${session.session_id}/new`,
            { method: "POST", body: {}, token: session.session_token },
          );
        } catch (e) {
          if (!(e instanceof ApiError) || ![401, 403, 404].includes(e.status))
            throw e;
          next = await createChatSession();
        }
        rememberView();
      } else next = await createChatSession();
      freshSession.current = next.session_id;
      save(next);
      setTurns([]);
      setDraft("");
      setLastFailed(null);
      setHint("");
      setLoading(false);
      setHistoryError("");
      setHistoryOpen(false);
      navigate("conversation");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setPending(false);
    }
  }
  function selectPrompt(message: string) {
    void send(message);
  }
  const historyProps = {
    entries,
    activeId: session?.session_id,
    busy: pending,
    onNew: () => void newChat(),
    onSelect: (entry: Conversation) => openConversation(entry.session),
    onRename: (id: string, title: string) =>
      setEntries((prev) =>
        prev.map((entry) =>
          entry.session.session_id === id
            ? { ...entry, title, renamed: true }
            : entry,
        ),
      ),
  };
  return (
    <div className={`chat-layout ${railOpen ? "rail-open" : ""}`}>
      {historyHost &&
        createPortal(<ChatHistory {...historyProps} />, historyHost)}
      <dialog
        className="history-dialog"
        ref={historyDialog}
        aria-label="Các cuộc trò chuyện"
        onCancel={() => setHistoryOpen(false)}
      >
        <header>
          <h2>Cuộc trò chuyện</h2>
          <button
            className="icon-button"
            aria-label="Đóng danh sách phiên"
            onClick={() => setHistoryOpen(false)}
          >
            <X size={20} />
          </button>
        </header>
        <ChatHistory {...historyProps} />
      </dialog>
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
              <h1 title={isHome ? "Trợ lý Bankwise" : currentEntry?.title}>
                {isHome
                  ? "Trợ lý Bankwise"
                  : (currentEntry?.title ?? "Cuộc trò chuyện mới")}
              </h1>
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
              ref={historyTrigger}
              className="icon-button mobile-history-toggle"
              aria-label="Mở danh sách phiên"
              onClick={() => setHistoryOpen(true)}
            >
              <History size={20} />
            </button>
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
              disabled={!verified || pending || waiting || ended}
              aria-label="Gặp chuyên viên"
              title="Gặp chuyên viên"
            >
              <Users size={20} />
            </button>
            <button
              className="icon-button"
              onClick={newChat}
              disabled={pending}
              aria-label="Bắt đầu phiên mới"
              title="Bắt đầu phiên mới"
            >
              <MessageSquarePlus size={20} />
            </button>
          </div>
        </header>
        {(error || historyError || storageError) && (
          <div className="chat-feedback">
            <ErrorBanner
              message={error || historyError || storageError}
              onRetry={
                historyError
                  ? () => {
                      if (session) cached.current.delete(session.session_id);
                      setHistoryError("");
                      setLoading(true);
                      setHistoryRetry((n) => n + 1);
                    }
                  : lastFailed
                    ? () => {
                        navigate("conversation");
                        void send(lastFailed.text, lastFailed.id);
                      }
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
                      disabled={
                        pending || loading || !!historyError || waiting || ended
                      }
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
          {!isHome && !turns.length && !loading && !historyError && (
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
                      <span
                        className={`delivered ${turn.delivery === "failed" ? "delivery-failed" : ""}`}
                      >
                        {turn.delivery === "sending" ? (
                          <>
                            <Clock3 size={13} /> Đang gửi…
                          </>
                        ) : turn.delivery === "failed" ? (
                          <>
                            Chưa gửi được
                            <button
                              type="button"
                              disabled={
                                pending ||
                                loading ||
                                !!historyError ||
                                ended ||
                                waiting
                              }
                              onClick={() =>
                                void send(turn.text, turn.clientId)
                              }
                            >
                              <RotateCcw size={12} /> Gửi lại
                            </button>
                          </>
                        ) : (
                          <>
                            <CheckCheck size={13} /> Đã gửi
                          </>
                        )}
                      </span>
                    )}
                  </div>
                </article>
              ))}
            </div>
          )}
          {!isHome && turns.some((turn) => turn.delivery === "sending") && (
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
          {!isHome && ended && (
            <div className="handoff-notice">
              <MessageCircle size={20} />
              <div>
                <strong>Cuộc trò chuyện đã kết thúc</strong>
                <p>
                  Bạn có thể xem lại nội dung hoặc bắt đầu một cuộc trò chuyện
                  mới.
                </p>
                <button
                  className="button secondary"
                  disabled={pending}
                  onClick={newChat}
                >
                  Tạo cuộc trò chuyện mới
                </button>
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
                disabled={
                  loading || !!historyError || ended || waiting || pending
                }
                aria-label="Tin nhắn cho trợ lý"
                placeholder={
                  ended
                    ? "Cuộc trò chuyện đã kết thúc. Tạo phiên mới để tiếp tục…"
                    : loading
                      ? "Đang khôi phục hội thoại…"
                      : waiting
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
                  disabled={
                    !draft.trim() ||
                    loading ||
                    !!historyError ||
                    ended ||
                    pending ||
                    waiting
                  }
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
              disabled={
                pending || loading || !!historyError || waiting || ended
              }
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
          disabled={!verified || pending || waiting || ended}
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
