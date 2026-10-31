import { useEffect, useRef, type ReactNode } from "react";
import { AlertCircle, ArrowRight, LoaderCircle, X } from "lucide-react";
export function BrandMark({ small = false }: { small?: boolean }) {
  return (
    <span className={`brand-mark ${small ? "small" : ""}`} aria-hidden="true">
      <svg viewBox="0 0 32 32" fill="none">
        <path
          d="M7 24V13a6 6 0 0 1 12 0v8a4 4 0 0 0 8 0V8M7 19h18M13 7v18"
          stroke="currentColor"
          strokeWidth="2.4"
          strokeLinecap="round"
        />
      </svg>
    </span>
  );
}
export function Spinner({ label = "Đang tải" }: { label?: string }) {
  return (
    <span className="loading" role="status">
      <LoaderCircle className="spin" size={17} />
      {label}
    </span>
  );
}
export function ErrorBanner({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="error-banner" role="alert">
      <AlertCircle size={18} />
      <span>{message}</span>
      {onRetry && <button onClick={onRetry}>Thử lại</button>}
    </div>
  );
}
export function Empty({
  icon,
  title,
  children,
  action,
}: {
  icon?: ReactNode;
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      {icon && <div className="empty-icon">{icon}</div>}
      <h2>{title}</h2>
      <p>{children}</p>
      {action}
    </div>
  );
}
export function PageHeading({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow: string;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <header className="page-heading">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {action}
    </header>
  );
}
export function Dialog({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (open) ref.current?.showModal();
    else ref.current?.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      onCancel={onClose}
      className="detail-dialog"
      aria-labelledby="dialog-title"
    >
      <header>
        <h2 id="dialog-title">{title}</h2>
        <button className="icon-button" aria-label="Đóng" onClick={onClose}>
          <X size={20} />
        </button>
      </header>
      <div className="dialog-body">{children}</div>
    </dialog>
  );
}
export function ActionLink({
  children,
  href,
}: {
  children: ReactNode;
  href: string;
}) {
  return (
    <a className="text-link" href={href}>
      {children}
      <ArrowRight size={16} />
    </a>
  );
}
export function StructuredData({ value }: { value: unknown }) {
  if (value === null || value === undefined)
    return <span className="muted">Chưa có dữ liệu</span>;
  if (typeof value !== "object") return <span>{String(value)}</span>;
  if (Array.isArray(value))
    return value.length ? (
      <ul className="data-list">
        {value.map((item, i) => (
          <li key={i}>
            <StructuredData value={item} />
          </li>
        ))}
      </ul>
    ) : (
      <span className="muted">Không có</span>
    );
  return (
    <dl className="data-fields">
      {Object.entries(value).map(([key, val]) => (
        <div key={key}>
          <dt>{fieldLabel(key)}</dt>
          <dd>
            <StructuredData value={val} />
          </dd>
        </div>
      ))}
    </dl>
  );
}
function fieldLabel(key: string) {
  const labels: Record<string, string> = {
    need: "Nhu cầu",
    needs: "Nhu cầu",
    quote: "Báo giá",
    quotes: "Báo giá",
    concerns: "Mối quan tâm",
    commitments: "Cam kết",
    conflicts: "Cần xác nhận",
    facts: "Thông tin",
    full_name: "Họ tên",
    customer_id: "Mã khách hàng",
    cif: "CIF",
    status: "Trạng thái",
    source_type: "Loại nguồn",
    source_ref: "Tham chiếu",
    observed_at: "Ghi nhận lúc",
    valid_until: "Hiệu lực đến",
    value: "Giá trị",
    key: "Thông tin",
    unit: "Đơn vị",
    fact_id: "Mã fact",
    next_step: "Bước tiếp theo",
    reason: "Lý do",
    open_questions: "Câu hỏi còn mở",
  };
  return labels[key] ?? key.replaceAll("_", " ");
}
