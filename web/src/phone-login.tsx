import { useState, type FormEvent } from "react";
import { ArrowRight, Phone } from "lucide-react";
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
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pending) return;
    setPending(true);
    setError("");
    try {
      onSuccess(
        await request<ChatSession>("/auth/phone/login", {
          method: "POST",
          body: { phone },
        }),
      );
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
        <Phone size={22} />
        <div>
          <strong>Một hồ sơ cho website và Telegram</strong>
          <p>
            Nhập số điện thoại để bắt đầu chat ngay. Nếu đã chat trên Telegram,
            dùng cùng số đã chia sẻ để tiếp tục ngữ cảnh.
          </p>
          <p>Bản demo: chỉ cần số điện thoại, không yêu cầu mã xác nhận.</p>
        </div>
      </div>
      {error && <ErrorBanner message={error} />}
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
      <div className="phone-login-actions">
        <button className="button primary" disabled={pending || !phone.trim()}>
          {pending ? (
            <Spinner label="Đang xử lý…" />
          ) : (
            <>
              Bắt đầu trò chuyện <ArrowRight size={16} />
            </>
          )}
        </button>
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
