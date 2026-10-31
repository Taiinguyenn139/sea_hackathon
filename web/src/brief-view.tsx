import { AlertTriangle, Clock3, FileCheck2, UserRound } from "lucide-react";
import { StructuredData } from "./ui";
type Fact = Record<string, unknown>;
function records(value: unknown): Fact[] {
  return Array.isArray(value)
    ? value.filter((x): x is Fact => Boolean(x) && typeof x === "object")
    : [];
}
const labels: Record<string, string> = {
  amount: "Khoản vay mong muốn",
  term: "Kỳ hạn",
  income: "Thu nhập",
  debt: "Nợ hiện tại",
  property_value: "Giá trị tài sản",
  product: "Sản phẩm",
  objection: "Điều khách còn băn khoăn",
  standard_rate: "Lãi suất tiêu chuẩn",
  promo_rate: "Lãi suất ưu đãi",
  promo_months: "Thời gian ưu đãi",
  installment_promo: "Trả góp trong ưu đãi",
  installment_after: "Trả góp sau ưu đãi",
  floating_margin: "Biên độ thả nổi",
  total_payment: "Tổng thanh toán minh họa",
  card_limit_min: "Hạn mức thẻ tối thiểu",
  card_limit_max: "Hạn mức thẻ tối đa",
  annual_fee: "Phí thường niên",
  fee_waiver_spend: "Chi tiêu miễn phí",
  commitment: "Cam kết",
};
function label(key: unknown) {
  return labels[String(key)] ?? String(key ?? "Thông tin").replaceAll("_", " ");
}
function factValue(f: Fact) {
  const value = f.value;
  const num =
    typeof value === "number"
      ? value
      : typeof value === "string" && value.trim() !== ""
        ? Number(value)
        : NaN;
  const formatted = Number.isFinite(num)
    ? num.toLocaleString("vi-VN", { maximumFractionDigits: 3 })
    : String(value ?? "—");
  return `${formatted}${f.unit ? ` ${f.unit}` : ""}`;
}
function FactRows({ facts }: { facts: Fact[] }) {
  return (
    <dl className="brief-facts">
      {facts.map((fact, i) => (
        <div key={String(fact.id ?? fact.fact_id ?? i)}>
          <dt>{label(fact.key)}</dt>
          <dd>
            <strong>{factValue(fact)}</strong>
            <span>
              {fact.source_type === "customer"
                ? "Khách chia sẻ"
                : "Hệ thống ngân hàng"}
              {fact.channel ? ` · ${fact.channel}` : ""}
              {fact.status === "expired"
                ? " · Hết hiệu lực"
                : fact.status === "stale"
                  ? " · Cần cập nhật"
                  : fact.status === "conflicted"
                    ? " · Cần xác nhận"
                    : ""}
            </span>
          </dd>
        </div>
      ))}
    </dl>
  );
}
export function BriefView({ brief }: { brief?: Record<string, unknown> }) {
  if (!brief) return <p className="muted">Chưa có bản tóm tắt.</p>;
  const needs = records(brief.needs);
  const warnings = records(brief.warnings);
  const quotes = records(brief.quotes);
  const commitments = records(brief.commitments);
  return (
    <div className="brief-view">
      <div className="customer-summary">
        <span className="list-icon">
          <UserRound size={21} />
        </span>
        <div>
          <h3>{String(brief.name ?? brief.full_name ?? "Khách hàng")}</h3>
          <p>
            {brief.cif ? `CIF ${brief.cif}` : String(brief.customer_id ?? "")}
          </p>
        </div>
      </div>
      {warnings.length > 0 && (
        <section className="brief-section warning-section">
          <h3>
            <AlertTriangle size={17} /> Cần chú ý trước khi tư vấn
          </h3>
          <FactRows facts={warnings} />
        </section>
      )}
      <section className="brief-section">
        <h3>Nhu cầu đã ghi nhận</h3>
        {needs.length ? (
          <FactRows facts={needs} />
        ) : (
          <p className="muted">Chưa ghi nhận nhu cầu cụ thể.</p>
        )}
      </section>
      <section className="brief-section">
        <h3>
          <FileCheck2 size={16} /> Thông tin đã báo
        </h3>
        {quotes.length ? (
          <FactRows facts={quotes} />
        ) : (
          <p className="muted">Chưa có báo giá trong hồ sơ.</p>
        )}
      </section>
      {commitments.length > 0 && (
        <section className="brief-section">
          <h3>
            <Clock3 size={16} /> Cam kết cần thực hiện
          </h3>
          {commitments.map((c, i) => (
            <div className="commitment-row" key={String(c.id ?? i)}>
              {String(c.due_at ?? c.time ?? "Chưa xác định thời gian")}
              <span className="badge warning">Cần gọi lại</span>
            </div>
          ))}
        </section>
      )}
    </div>
  );
}
export function TraceView({ value }: { value: unknown }) {
  const data =
    value && typeof value === "object"
      ? (value as Record<string, unknown>)
      : {};
  const items = records(data.items);
  if (!items.length)
    return <p className="muted">Chưa có dấu vết xử lý cho phiên này.</p>;
  return (
    <div className="trace-list">
      {items.map((item, i) => (
        <article className="trace-turn" key={String(item.turn_id ?? i)}>
          <h3>Lượt {i + 1}</h3>
          <p className="trace-id">{String(item.turn_id ?? "")}</p>
          {records(item.guard_events).length > 0 && (
            <details>
              <summary>
                Kiểm tra an toàn · {records(item.guard_events).length} sự kiện
              </summary>
              <StructuredData value={item.guard_events} />
            </details>
          )}
          <details>
            <summary>Nguồn các số liệu</summary>
            <StructuredData value={item.source_spans ?? item.used_fact_ids} />
          </details>
          <details>
            <summary>Thông tin được đưa vào ngữ cảnh</summary>
            <StructuredData value={item.context_parts} />
          </details>
          <details>
            <summary>Các lần gọi hệ thống ngân hàng</summary>
            <StructuredData value={item.tool_calls} />
          </details>
        </article>
      ))}
    </div>
  );
}
