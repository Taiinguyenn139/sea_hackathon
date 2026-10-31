import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  ArrowRight,
  ArrowUp,
  Check,
  CheckCheck,
  CircleHelp,
  CreditCard,
  Home,
  LockKeyhole,
  MessageSquarePlus,
  ShieldCheck,
  UserRound,
  Users,
  Wallet,
} from "lucide-react";
import {
  ApiError,
  loadTurns,
  errorMessage,
  request,
  type ChatSession,
  type Profile,
  type Reply,
  type Turn,
} from "../api";
import { BrandMark, ErrorBanner, Spinner } from "../ui";
const STORAGE = "mach-chat-session";
function savedSession(): ChatSession | null {
  try {
    return JSON.parse(sessionStorage.getItem(STORAGE) ?? "null");
  } catch {
    return null;
  }
}
const suggestions = [
  {
    icon: Home,
    title: "Một tổ ấm mới",
    subtitle: "Tìm hiểu khoản vay mua nhà",
    message: "Tôi muốn tìm hiểu vay mua nhà.",
  },
  {
    icon: Wallet,
    title: "Kế hoạch của riêng bạn",
    subtitle: "Khám phá vay tiêu dùng",
    message: "Tôi muốn tìm hiểu vay tiêu dùng.",
  },
  {
    icon: CreditCard,
    title: "Chi tiêu chủ động",
    subtitle: "Tìm chiếc thẻ tín dụng phù hợp",
    message: "Tôi muốn tìm hiểu thẻ tín dụng.",
  },
];
export function CustomerChat() {
  const [session, setSession] = useState<ChatSession | null>(savedSession);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [profile, setProfile] = useState(savedSession()?.demo_profile ?? "hoa");
  const [dob, setDob] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [lastFailed, setLastFailed] = useState<{
    text: string;
    id: string;
  } | null>(null);
  const [hint, setHint] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const verified = session?.identity_status === "verified";
  const waiting =
    Boolean(session?.handoff_id) && session?.status !== "completed";
  function save(next: ChatSession | null) {
    setSession(next);
    if (next) sessionStorage.setItem(STORAGE, JSON.stringify(next));
    else sessionStorage.removeItem(STORAGE);
  }
  const loadProfiles = () =>
    request<{ profiles: Profile[] }>("/demo/profiles")
      .then((data) => setProfiles(data.profiles))
      .catch((e) => setError(errorMessage(e)));
  useEffect(() => {
    void loadProfiles();
  }, []);
  useEffect(() => {
    if (!session) return;
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
    if (turns.length || pending)
      end.current?.scrollIntoView({
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "instant"
          : "smooth",
        block: "nearest",
      });
  }, [turns, pending]);
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
  async function start() {
    setError("");
    setPending(true);
    try {
      const next = await request<ChatSession>("/sessions", {
        method: "POST",
        body: { mode: "harness", demo_profile: profile },
      });
      save({ ...next, demo_profile: profile });
      setTurns([]);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setPending(false);
    }
  }
  async function verify(e: FormEvent) {
    e.preventDefault();
    if (!session) return;
    setPending(true);
    setError("");
    try {
      const result = await request<{ identity_status: string }>(
        `/sessions/${session.session_id}/verify`,
        { method: "POST", body: { dob }, token: session.session_token },
      );
      save({ ...session, identity_status: result.identity_status });
      if (result.identity_status !== "verified")
        setError("Ngày sinh chưa khớp. Vui lòng kiểm tra lại.");
      else input.current?.focus();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setPending(false);
    }
  }
  async function send(text = draft, id: string = crypto.randomUUID()) {
    if (!session || !text.trim() || pending || waiting) return;
    setPending(true);
    setError("");
    setLastFailed(null);
    try {
      const data = await request<Reply>(
        `/sessions/${session.session_id}/messages`,
        {
          method: "POST",
          body: { text: text.trim(), client_message_id: id },
          token: session.session_token,
        },
      );
      setTurns((prev) => [
        ...prev,
        { role: "user", text: text.trim() },
        { role: "assistant", text: data.reply },
      ]);
      setDraft("");
      if (data.handoff_id)
        save({
          ...session,
          handoff_id: data.handoff_id,
          status: "waiting_human",
        });
    } catch (e) {
      setError(errorMessage(e));
      setLastFailed({ text, id });
    } finally {
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
    setTurns([]);
    setDraft("");
    setError("");
    setHint("");
    setDob("");
    setPending(false);
  }
  function selectPrompt(message: string) {
    setDraft(message);
    input.current?.focus();
  }
  const chosenProfile = profiles.find((p) => p.id === profile);
  return (
    <div className="chat-layout">
      <section className="chat-main" aria-label="Hội thoại tài chính">
        <header className="chat-heading">
          <div className="assistant-identity">
            <BrandMark small />
            <div>
              <h1>Trợ lý tài chính</h1>
              <p>
                <span className="status-dot" /> Đồng hành cùng kế hoạch của bạn
              </p>
            </div>
          </div>
          <div className="chat-header-actions">
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
        <div className="conversation-scroll">
          {!turns.length && (
            <section className="welcome">
              <div className="eyebrow">CHÀO BẠN, MÌNH LÀ MẠCH</div>
              <h2>
                Mỗi kế hoạch lớn
                <br />
                bắt đầu từ <span>một cuộc trò chuyện.</span>
              </h2>
              <p>
                Cùng tìm giải pháp tài chính phù hợp với bạn.
                <br className="desktop-break" /> Mình sẽ ghi nhớ để lần sau bạn
                không phải bắt đầu lại.
              </p>
              <div className="suggestions">
                {suggestions.map(({ icon: Icon, title, subtitle, message }) => (
                  <button
                    key={title}
                    className="suggestion"
                    onClick={() => selectPrompt(message)}
                  >
                    <Icon size={23} />
                    <strong>{title}</strong>
                    <span>{subtitle}</span>
                    <ArrowRight size={17} className="suggestion-arrow" />
                  </button>
                ))}
              </div>
              <div className="privacy-note">
                <ShieldCheck size={16} />
                <span>Chỉ chia sẻ thông tin khi bạn đã sẵn sàng.</span>
              </div>
            </section>
          )}
          {loading && <Spinner label="Đang khôi phục hội thoại…" />}
          {!!turns.length && (
            <div className="messages" role="log" aria-label="Lịch sử hội thoại">
              {turns.map((turn, index) => (
                <article
                  className={`message ${turn.role === "user" ? "from-user" : "from-assistant"}`}
                  key={turn.turn_id ?? turn.id ?? index}
                >
                  {turn.role !== "user" && <BrandMark small />}
                  <div>
                    <div className="message-label">
                      {turn.role === "user" ? "Bạn" : "Mạch · Trợ lý tài chính"}
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
          {pending && session && (
            <div className="typing">
              <BrandMark small />
              <Spinner label="Mạch đang xử lý…" />
            </div>
          )}
          {waiting && (
            <div className="handoff-notice">
              <Users size={20} />
              <div>
                <strong>Đang chờ chuyên viên tiếp nhận</strong>
                <p>
                  Thông tin trao đổi đã được chuyển cùng yêu cầu. Bạn không cần
                  kể lại từ đầu.
                </p>
              </div>
            </div>
          )}
          {hint && (
            <p className="success-message" role="status">
              <Check size={16} />
              {hint}
            </p>
          )}
          <div ref={end} />
        </div>
        <div className="composer-area">
          {error && (
            <ErrorBanner
              message={error}
              onRetry={
                lastFailed
                  ? () => void send(lastFailed.text, lastFailed.id)
                  : !profiles.length
                    ? loadProfiles
                    : undefined
              }
            />
          )}
          {!session && (
            <div className="onboarding-bar">
              <div>
                <span className="mini-label">TRẢI NGHIỆM DEMO</span>
                <label htmlFor="demo-profile">
                  Chọn hồ sơ hư cấu để bắt đầu
                </label>
              </div>
              <select
                id="demo-profile"
                value={profile}
                onChange={(e) => setProfile(e.target.value)}
                disabled={!profiles.length}
              >
                {profiles.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name ?? p.full_name ?? p.id}
                  </option>
                ))}
              </select>
              <button
                className="button primary"
                onClick={start}
                disabled={pending || !profiles.length}
              >
                {pending ? (
                  <Spinner label="Đang mở…" />
                ) : (
                  <>
                    Bắt đầu <ArrowRight size={16} />
                  </>
                )}
              </button>
            </div>
          )}
          {session && !verified && (
            <form className="verify-bar" onSubmit={verify}>
              <LockKeyhole size={21} />
              <div>
                <strong>Xác minh trước khi tiếp tục</strong>
                <label htmlFor="dob">
                  Ngày sinh của hồ sơ demo
                  {chosenProfile?.dob ? ` · ${chosenProfile.dob}` : ""}
                </label>
              </div>
              <input
                id="dob"
                type="date"
                value={dob}
                onChange={(e) => setDob(e.target.value)}
                required
              />
              <button className="button primary" disabled={pending || !dob}>
                Xác minh
              </button>
            </form>
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
              disabled={!verified || waiting || pending}
              aria-label="Tin nhắn cho trợ lý"
              placeholder={
                !session
                  ? "Bắt đầu phiên để trò chuyện với Mạch…"
                  : !verified
                    ? "Xác minh hồ sơ để tiếp tục…"
                    : waiting
                      ? "Chuyên viên sẽ tiếp nhận yêu cầu của bạn…"
                      : "Bạn đang ấp ủ kế hoạch gì?"
              }
            />
            <div className="composer-footer">
              <span>
                <LockKeyhole size={13} /> Một cuộc trò chuyện riêng tư
              </span>
              <button
                className="send-button"
                disabled={!verified || !draft.trim() || pending || waiting}
                aria-label="Gửi tin nhắn"
              >
                <ArrowUp size={21} />
              </button>
            </div>
          </form>
          <p className="composer-disclaimer">
            Thông tin mang tính minh họa. Mọi khách hàng, sản phẩm và số liệu
            đều hư cấu.
          </p>
        </div>
      </section>
      <aside className="context-rail">
        <div className="rail-heading">
          <span className="mini-label">CÙNG BẠN, TỪNG BƯỚC</span>
          <div className="line-accent" />
        </div>
        <h2>
          Kế hoạch của bạn.
          <br />
          Một mạch liền nhau.
        </h2>
        <p className="rail-intro">
          Dù bắt đầu ở đâu, cuộc trò chuyện vẫn tiếp nối đúng điều bạn quan tâm.
        </p>
        <div className="journey-list">
          <div className={`journey-item ${session ? "done" : "current"}`}>
            <span>{session ? <Check size={15} /> : "01"}</span>
            <div>
              <strong>Bắt đầu trò chuyện</strong>
              <p>Chia sẻ mục tiêu của bạn</p>
            </div>
          </div>
          <div
            className={`journey-item ${verified ? "done" : session ? "current" : ""}`}
          >
            <span>{verified ? <Check size={15} /> : "02"}</span>
            <div>
              <strong>Xác minh hồ sơ</strong>
              <p>Bảo vệ thông tin riêng tư</p>
            </div>
          </div>
          <div className={`journey-item ${verified ? "current" : ""}`}>
            <span>03</span>
            <div>
              <strong>Tìm giải pháp phù hợp</strong>
              <p>Thông tin rõ ràng, có nguồn</p>
            </div>
          </div>
        </div>
        <div className="rail-callout">
          <ShieldCheck size={24} />
          <h3>Hiểu bạn, nhớ đúng.</h3>
          <p>
            Thông tin cần xác nhận sẽ được hỏi lại. Ưu đãi được kiểm tra trước
            khi tư vấn.
          </p>
          <div className="channel-list">
            <span>Web</span>
            <span>Telegram</span>
          </div>
        </div>
        <button
          className="human-help"
          onClick={handoff}
          disabled={!verified || pending || waiting}
        >
          <UserRound size={20} />
          <span>
            <strong>Cần một người hỗ trợ?</strong>
            <small>Kết nối với chuyên viên</small>
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
