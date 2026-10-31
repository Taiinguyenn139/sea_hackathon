import { useState, type FormEvent } from "react";
import { ArrowRight, Phone, ShieldCheck } from "lucide-react";
import { errorMessage, request, type ChatSession } from "./api";
import { ErrorBanner, Spinner } from "./ui";
import "./styles/phone-login.css";

export function PhoneLogin({
  onSuccess,
  onCancel,
}: {
  onSuccess: (session: ChatSession) => void;
  onCancel: () => void;
}) {
  const [phone, setPhone] = useState("");
  const [challenge, setChallenge] = useState("");
  const [code, setCode] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  async function sendCode() {
    setPending(true);
    setError("");
    try {
      const result = await request<{ challenge_id: string }>(
        "/auth/phone/start",
        {
          method: "POST",
          body: { phone },
        },
      );
      setChallenge(result.challenge_id);
      setCode("");
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setPending(false);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pending) return;
    if (!challenge) return sendCode();
    setPending(true);
    setError("");
    try {
      const session = await request<ChatSession>("/auth/phone/confirm", {
        method: "POST",
        body: { challenge_id: challenge, code },
      });
      onSuccess(session);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setPending(false);
    }
  }

  return (
    <form
      className="phone-login"
      onSubmit={submit}
      aria-label="Tiếp tục bằng số điện thoại"
    >
      <div className="phone-login-heading">
        {challenge ? <ShieldCheck size={22} /> : <Phone size={22} />}
        <div>
          <strong>Tiếp nối cuộc trò chuyện của bạn</strong>
          <p>
            {challenge
              ? "Nhập mã xác nhận gửi tới Telegram đã liên kết với số điện thoại này. Mã có hiệu lực trong 10 phút."
              : "Dùng số điện thoại đã chia sẻ với bot Telegram để giữ lại nhu cầu và thông tin bạn đã trao đổi."}
          </p>
        </div>
      </div>
      {error && <ErrorBanner message={error} />}
      {!challenge ? (
        <label>
          Số điện thoại
          <input
            type="tel"
            autoComplete="tel"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            placeholder="090 123 4567"
            maxLength={64}
            required
            disabled={pending}
          />
        </label>
      ) : (
        <>
          <label>
            Mã xác nhận
            <input
              inputMode="numeric"
              autoComplete="one-time-code"
              value={code}
              onChange={(e) =>
                setCode(e.target.value.replace(/\D/g, "").slice(0, 6))
              }
              pattern="[0-9]{6}"
              minLength={6}
              maxLength={6}
              required
              disabled={pending}
            />
          </label>
          <p className="phone-login-help">
            Nếu chưa nhận mã, mở bot Telegram và bấm “Chia sẻ số điện thoại”,
            rồi yêu cầu mã mới.
          </p>
        </>
      )}
      <div className="phone-login-actions">
        <button
          className="button primary"
          disabled={pending || (challenge ? code.length !== 6 : !phone.trim())}
        >
          {pending ? (
            <Spinner label="Đang xử lý…" />
          ) : (
            <>
              {challenge ? "Xác nhận và tiếp tục" : "Nhận mã qua Telegram"}
              <ArrowRight size={16} />
            </>
          )}
        </button>
        {challenge && (
          <button
            className="button secondary"
            type="button"
            onClick={sendCode}
            disabled={pending}
          >
            Yêu cầu mã mới
          </button>
        )}
        <button
          className="button secondary"
          type="button"
          onClick={onCancel}
          disabled={pending}
        >
          Quay lại
        </button>
      </div>
    </form>
  );
}
