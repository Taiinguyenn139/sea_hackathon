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
const STORAGE = "bankwise-chat-session";
const LEGACY_STORAGE = "mach-chat-session";
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
    else {
      sessionStorage.removeItem(STORAGE);
      sessionStorage.removeItem(LEGACY_STORAGE);
    }
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
      <section className="chat-main" aria-label="Hỗ trợ khách hàng ngân hàng">
        <header className="chat-heading">
          <div className="assistant-identity">
            <BrandMark small />
            <div>
              <h1>Trợ lý ngân hàng</h1>
              <p>
                <span className="status-dot" /> Hỗ trợ trực tuyến
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
              <div className="welcome-symbol">
                <BrandMark />
              </div>
              <div className="eyebrow">NGÂN HÀNG BANKWISE</div>
              <h2>Chúng tôi có thể hỗ trợ gì cho bạn?</h2>
              <p>Vui lòng chọn dịch vụ hoặc nhập câu hỏi để được hỗ trợ.</p>
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
                <span>
                  Không cung cấp mật khẩu, mã PIN hoặc OTP trong hội thoại.
                </span>
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
                      {turn.role === "user" ? "Bạn" : "Trợ lý ngân hàng"}
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
              <Spinner label="Trợ lý đang trả lời…" />
            </div>
          )}
          {waiting && (
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
                <span className="mini-label">THÔNG TIN KHÁCH HÀNG</span>
                <label htmlFor="demo-profile">
                  Chọn hồ sơ để bắt đầu hỗ trợ
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
                <strong>Xác minh để xem lịch sử và đặt lịch</strong>
                <label htmlFor="dob">
                  Ngày sinh của khách hàng
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
              disabled={!session || waiting || pending}
              aria-label="Tin nhắn cho trợ lý"
              placeholder={
                !session
                  ? "Bắt đầu phiên hỗ trợ để gửi tin nhắn…"
                  : !verified
                    ? "Hỏi thông tin công khai; xác minh để dùng dịch vụ cá nhân…"
                    : waiting
                      ? "Chuyên viên sẽ tiếp nhận yêu cầu của bạn…"
                      : "Nhập câu hỏi cần hỗ trợ…"
              }
            />
            <div className="composer-footer">
              <span>
                <LockKeyhole size={13} /> Ngân hàng Bankwise
              </span>
              <button
                className="send-button"
                disabled={!session || !draft.trim() || pending || waiting}
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
          <span className="mini-label">TRUNG TÂM HỖ TRỢ</span>
          <div className="line-accent" />
        </div>
        <h2>Dịch vụ ngân hàng</h2>
        <div className="bank-services">
          {[
            {
              icon: Wallet,
              title: "Tài khoản & tiết kiệm",
              message: "Tôi muốn tìm hiểu tài khoản và tiết kiệm.",
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
            <button key={title} onClick={() => selectPrompt(message)}>
              <Icon size={19} />
              <span>{title}</span>
              <ArrowRight size={15} />
            </button>
          ))}
        </div>
        <div className="support-session">
          <span className="mini-label">PHIÊN HỖ TRỢ</span>
          <dl>
            <div>
              <dt>Khách hàng</dt>
              <dd>
                {chosenProfile?.name ??
                  chosenProfile?.full_name ??
                  "Chưa chọn hồ sơ"}
              </dd>
            </div>
            <div>
              <dt>Xác minh</dt>
              <dd className={verified ? "session-verified" : ""}>
                {verified ? "Đã xác minh" : "Chưa xác minh"}
              </dd>
            </div>
            <div>
              <dt>Trạng thái</dt>
              <dd>
                {waiting
                  ? "Chờ chuyên viên"
                  : session
                    ? "Đang hỗ trợ"
                    : "Chưa bắt đầu"}
              </dd>
            </div>
          </dl>
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
