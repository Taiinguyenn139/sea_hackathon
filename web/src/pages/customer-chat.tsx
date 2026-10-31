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
  Sparkles,
  PanelRight,
  MessageCircle,
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
import { BankingScene, ServiceArt } from "../banking-art";
import { PhoneLogin } from "../phone-login";
import { BriefView } from "../brief-view";
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
  const [phoneLogin, setPhoneLogin] = useState(false);
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
  const [railOpen, setRailOpen] = useState(false);
  const [promptNotice, setPromptNotice] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const verified = ["verified", "identified"].includes(session?.identity_status ?? "");
  const waiting =
    Boolean(session?.handoff_id) && session?.status !== "completed";
  useEffect(() => {
    if (!input.current) return;
    input.current.style.height = "auto";
    input.current.style.height = `${Math.min(input.current.scrollHeight, 150)}px`;
  }, [draft, verified]);
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
      setPromptNotice("");
      if (data.handoff_id || data.brief)
        save({
          ...session,
          brief: data.brief ?? session.brief,
          handoff_id: data.handoff_id ?? session.handoff_id,
          status: data.handoff_id ? "waiting_human" : session.status,
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
    setPromptNotice("");
    setError("");
    setHint("");
    setDob("");
    setPending(false);
  }
  function selectPrompt(message: string) {
    setDraft(message);
    setPromptNotice(verified ? "Gợi ý đã sẵn sàng. Bạn có thể chỉnh lại trước khi gửi." : "Đã chọn nội dung tư vấn. Hoàn tất bước bên dưới để gửi câu hỏi.");
    if (verified) input.current?.focus();
    else document.getElementById(session ? "dob" : "demo-profile")?.focus();
  }
  const chosenProfile = profiles.find((p) => p.id === profile);
  return (
    <div className={`chat-layout ${railOpen ? "rail-open" : ""}`}>
      <section className={`chat-main ${!turns.length ? "is-welcome" : ""}`} aria-label="Hỗ trợ khách hàng ngân hàng">
        <header className="chat-heading">
          <div className="assistant-identity">
            <BrandMark small />
            <div>
              <h1>Trợ lý ngân hàng</h1>
              <p>
                <span className="status-dot" /> {waiting ? "Đang chờ chuyên viên" : verified ? "Sẵn sàng tiếp nối cùng bạn" : "Cùng bạn trong từng quyết định"}
              </p>
            </div>
          </div>
          <div className="chat-header-actions">
            <button className="icon-button context-toggle" onClick={() => setRailOpen(!railOpen)}
              aria-label={railOpen ? "Ẩn thông tin hỗ trợ" : "Hiện thông tin hỗ trợ"}
              aria-expanded={railOpen} aria-controls="support-context" title="Thông tin hỗ trợ">
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
        <div className="conversation-scroll">
          {!turns.length && (
            <section className="welcome">
              <div className="welcome-hero">
                <div className="hero-copy">
                  <div className="eyebrow welcome-tag"><span /> TÀI CHÍNH CÁ NHÂN, THẬT GẦN GŨI</div>
                  <h2>Mỗi dự định.<br /><em>Một khởi đầu mới.</em></h2>
                  <p>Từ ngôi nhà đầu tiên đến những kế hoạch tiếp theo.<br className="desktop-break" /> Cùng trò chuyện để tìm hướng đi phù hợp với bạn.</p>
                  <div className="hero-signature"><span className="signature-mark"><Sparkles size={15} /></span> Lắng nghe hôm nay. Tiếp nối ngày mai.</div>
                </div>
                <BankingScene />
              </div>
              <div className="services-heading">
                <div><span className="section-index">01 / KHÁM PHÁ</span><h3>Chúng tôi có thể hỗ trợ gì cho bạn?</h3></div>
                <span className="services-hint">Chọn điều bạn quan tâm <ArrowRight size={15} /></span>
              </div>
              <div className="suggestions">
                {suggestions.map(({ icon: Icon, title, subtitle, message }, index) => (
                  <button
                    key={title}
                    className={`suggestion suggestion-${index} ${draft === message ? "is-selected" : ""}`}
                    onClick={() => selectPrompt(message)}
                    aria-pressed={draft === message}
                    disabled={pending || waiting}
                  >
                    <span className="service-category"><Icon size={15} /><span>0{index + 1}</span></span>
                    <ServiceArt kind={index} />
                    <span className="service-copy"><strong>{title}</strong><span>{subtitle}</span></span>
                    <span className="service-cta">{draft === message ? "Đã chọn" : "Khám phá"}<ArrowRight size={17} /></span>
                  </button>
                ))}
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
            <div className="typing" role="status">
              <BrandMark small />
              <span className="typing-dots" aria-hidden="true"><i /><i /><i /></span>
              <span>{!verified ? "Đang xử lý xác minh…" : "Trợ lý đang xử lý yêu cầu…"}</span>
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
          {!turns.length && <div className="conversation-intro"><span className="section-index">02 / KẾT NỐI</span><strong>Câu chuyện của bạn bắt đầu tại đây.</strong><span><ShieldCheck size={14} /> Xác minh trước khi tư vấn</span></div>}
          {verified && !waiting && !pending && turns.length > 0 && (
            <div className="quick-prompts" aria-label="Gợi ý tiếp tục hội thoại">
              <button onClick={() => selectPrompt("Tôi muốn hỏi thêm về lãi suất sau ưu đãi.")}>Lãi sau ưu đãi <ArrowRight size={13} /></button>
              <button onClick={() => selectPrompt("Tôi cần chuẩn bị những giấy tờ nào?")}>Hồ sơ cần chuẩn bị <ArrowRight size={13} /></button>
            </div>
          )}
          {promptNotice && <p className="prompt-notice" role="status"><Check size={14} />{promptNotice}</p>}
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
          {!session && phoneLogin && (
            <PhoneLogin onCancel={() => setPhoneLogin(false)} onSuccess={(next) => {
              save(next);
              setTurns([]);
              setPhoneLogin(false);
              setHint("Đã kết nối hồ sơ từ Telegram. Bạn có thể tiếp tục nhu cầu đã trao đổi.");
              setError("");
            }} />
          )}
          {!session && !phoneLogin && (
            <div className="phone-continuation">
              <button className="button secondary" onClick={() => setPhoneLogin(true)} disabled={pending}>
                Tiếp tục bằng số điện thoại <ArrowRight size={16} />
              </button>
            </div>
          )}
          {!session && !phoneLogin && (
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
                <strong>Xác minh trước khi tiếp tục</strong>
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
              disabled={!verified || waiting || pending}
              aria-label="Tin nhắn cho trợ lý"
              placeholder={
                !session
                  ? "Bắt đầu phiên hỗ trợ để gửi tin nhắn…"
                  : !verified
                    ? "Xác minh hồ sơ để tiếp tục…"
                    : waiting
                      ? "Chuyên viên sẽ tiếp nhận yêu cầu của bạn…"
                      : "Nhập câu hỏi cần hỗ trợ…"
              }
            />
            <div className="composer-footer">
              <span>
                <Sparkles size={14} /> {verified ? "Enter để gửi · Shift + Enter để xuống dòng" : "Câu chuyện của bạn bắt đầu từ đây"}
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
            Không chia sẻ mật khẩu, PIN hoặc OTP. Đây là ngân hàng giả định; mọi hồ sơ và sản phẩm đều hư cấu.
          </p>
        </div>
      </section>
      <aside className="context-rail" id="support-context" hidden={!railOpen} aria-label="Thông tin hỗ trợ">
        <div className="rail-feature">
          <span className="rail-feature-icon"><MessageCircle size={23} /></span>
          <span className="mini-label">MỘT CUỘC TRÒ CHUYỆN LIỀN MẠCH</span>
          <h2>Thêm thấu hiểu.<br />Bớt những lần hỏi lại.</h2>
          <p>Tiếp tục điều bạn quan tâm, với thông tin được kết nối qua từng cuộc trò chuyện.</p>
          <div className="rail-feature-art" aria-hidden="true"><span /><span /><span /></div>
        </div>
        <div className="journey-progress">
          <div className="mini-label">HÀNH TRÌNH CỦA BẠN</div>
          <ol>
            {[
              { label: "Chọn hồ sơ", detail: "Bắt đầu một phiên hỗ trợ", done: Boolean(session), current: !session },
              { label: "Xác minh thông tin", detail: "Kết nối đúng hồ sơ của bạn", done: verified, current: Boolean(session) && !verified },
              { label: "Trao đổi cùng trợ lý", detail: waiting ? "Chuyên viên sẽ tiếp nối" : "Tư vấn theo nhu cầu của bạn", done: false, current: verified },
            ].map((step, index) => (
              <li key={step.label} className={step.done ? "is-done" : step.current ? "is-current" : ""} aria-current={step.current ? "step" : undefined}>
                <span className="step-number">{step.done ? <Check size={14} /> : index + 1}</span>
                <div><strong>{step.label}</strong><small>{step.detail}</small></div>
              </li>
            ))}
          </ol>
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
            <button key={title} onClick={() => selectPrompt(message)} disabled={pending || waiting}>
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
                {session?.identity_status === "identified" ? String(session.brief?.name ?? "Khách hàng") : chosenProfile?.name ??
                  chosenProfile?.full_name ??
                  "Chưa chọn hồ sơ"}
              </dd>
            </div>
            <div>
              <dt>Xác minh</dt>
              <dd className={verified ? "session-verified" : ""}>
                {session?.identity_status === "identified" ? "Đã xác nhận SĐT" : verified ? "Đã xác minh" : "Chưa xác minh"}
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
        {session?.identity_status === "identified" && session.brief && (
          <details className="phone-context">
            <summary>Thông tin đã ghi nhận từ Telegram</summary>
            <BriefView brief={session.brief} />
          </details>
        )}
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
