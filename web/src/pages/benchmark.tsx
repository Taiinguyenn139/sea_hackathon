import { useEffect, useState } from "react";
import {
  ArrowDownRight,
  ArrowUpRight,
  BarChart3,
  CheckCircle2,
  CircleDashed,
  Database,
  Download,
  FlaskConical,
  RefreshCw,
} from "lucide-react";
import { ApiError, errorMessage, request, type RunSummary } from "../api";
import { useStaff } from "../staff-auth";
import { Empty, ErrorBanner, PageHeading, Spinner } from "../ui";
import { SalesImprovements } from "../sales-improvements";
type Metrics = Record<string, number | null>;
type Run = RunSummary & {
  schema_version: number;
  summary: Record<string, Metrics>;
  metadata?: Record<string, unknown>;
  judge?: { status: string };
  results?: unknown[];
};
const modes = [
  {
    key: "no_memory",
    label: "Không nhớ",
    description: "Mỗi phiên bắt đầu lại từ đầu.",
    icon: CircleDashed,
  },
  {
    key: "full_history",
    label: "Toàn bộ lịch sử",
    description: "Sử dụng hội thoại từ các phiên cũ.",
    icon: Database,
  },
  {
    key: "harness",
    label: "Memory Harness",
    description: "Ghi nhớ có cấu trúc và kiểm tra nguồn.",
    icon: CheckCircle2,
  },
];
const kpis = [
  {
    key: "conversion_rate",
    label: "Tỷ lệ chuyển đổi",
    description: "Kết quả mô phỏng, chưa phải doanh số thực tế",
    unit: "%",
    better: "higher",
  },
  {
    key: "reask_rate",
    label: "Tỷ lệ hỏi lại",
    description: "Hỏi lại thông tin đã được biết",
    unit: "%",
    better: "lower",
  },
  {
    key: "price_error_rate",
    label: "Lỗi giá & ưu đãi",
    description: "Sai giá trị, nguồn hoặc hiệu lực",
    unit: "%",
    better: "lower",
  },
  {
    key: "handoff_completeness",
    label: "Đầy đủ khi chuyển tiếp",
    description: "Đủ thông tin theo checklist kịch bản",
    unit: "%",
    better: "higher",
  },
  {
    key: "calls_to_conversion",
    label: "Số phiên tới chuyển đổi",
    description: "Chỉ số tham khảo từ simulator",
    unit: "phiên",
    better: "lower",
  },
  {
    key: "missing_offer_rate",
    label: "Thiếu đề xuất phù hợp",
    description: "Không bỏ qua cơ hội để tránh lỗi số liệu",
    unit: "%",
    better: "lower",
  },
];
export function formatMetric(value: unknown, unit: string) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return unit === "%"
    ? `${(value * 100).toLocaleString("vi-VN", { maximumFractionDigits: 1 })}%`
    : value.toLocaleString("vi-VN", { maximumFractionDigits: 2 });
}
export function Benchmark() {
  const { expire } = useStaff();
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selected, setSelected] = useState("");
  const [run, setRun] = useState<Run | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");
  function fail(e: unknown) {
    if (e instanceof ApiError && e.status === 401) expire();
    else setError(errorMessage(e));
  }
  async function load() {
    setLoading(true);
    setError("");
    try {
      const data = await request<{
        schema_version: number;
        runs: RunSummary[];
      }>("/eval/runs");
      if (data.schema_version !== 1)
        throw new Error(
          "Phiên bản dữ liệu chưa được hỗ trợ. Vui lòng cập nhật ứng dụng.",
        );
      setRuns(data.runs);
      setSelected((prev) =>
        data.runs.some((r) => r.run_id === prev)
          ? prev
          : (data.runs[0]?.run_id ?? ""),
      );
    } catch (e) {
      fail(e);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    void load();
  }, []);
  useEffect(() => {
    if (!selected) {
      setRun(null);
      return;
    }
    let live = true;
    setDetailLoading(true);
    setRun(null);
    request<Run>(`/eval/runs/${encodeURIComponent(selected)}`)
      .then((data) => {
        if (data.schema_version !== 1)
          throw new Error("Phiên bản kết quả chưa được hỗ trợ.");
        if (live) setRun(data);
      })
      .catch((e) => {
        if (live) fail(e);
      })
      .finally(() => {
        if (live) setDetailLoading(false);
      });
    return () => {
      live = false;
    };
  }, [selected]);
  function download() {
    if (!run) return;
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(run, null, 2)], { type: "application/json" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = `${run.run_id}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }
  return (
    <div className="workspace-page">
      <PageHeading
        eyebrow="ĐÁNH GIÁ ĐỘC LẬP"
        title="Trí nhớ tốt cần được chứng minh."
        description="Cùng kịch bản, cùng điều kiện. So sánh ba cách tiếp nối một cuộc hội thoại."
        action={
          <button
            className="button secondary"
            disabled={!run}
            onClick={download}
          >
            <Download size={16} /> Xuất kết quả
          </button>
        }
      />
      <p>
        <a className="button secondary" href="/sales">
          Quản lý kịch bản & huấn luyện chuyên viên
        </a>
      </p>
      <div className="mode-cards">
        {modes.map(({ key, label, description, icon: Icon }) => (
          <article
            className={`mode-card ${key === "harness" ? "featured" : ""}`}
            key={key}
          >
            <div className="mode-icon">
              <Icon size={22} />
            </div>
            <span className="mini-label">
              {key === "harness" ? "HỆ THỐNG ĐỀ XUẤT" : "ĐỐI CHỨNG"}
            </span>
            <h2>{label}</h2>
            <p>{description}</p>
          </article>
        ))}
      </div>
      <section className="panel benchmark-panel">
        <div className="panel-heading">
          <div>
            <h2>Kết quả thực nghiệm</h2>
            <p className="muted">
              Chỉ hiển thị dữ liệu từ các lượt đánh giá đã chạy.
            </p>
          </div>
          <div className="run-actions">
            {!!runs.length && (
              <>
                <label className="sr-only" htmlFor="run-select">
                  Chọn lượt đánh giá
                </label>
                <select
                  id="run-select"
                  value={selected}
                  onChange={(e) => setSelected(e.target.value)}
                >
                  {runs.map((item) => (
                    <option value={item.run_id} key={item.run_id}>
                      {item.run_id} · {item.split ?? "dev"}
                    </option>
                  ))}
                </select>
              </>
            )}
            <button
              className="icon-button"
              aria-label="Làm mới kết quả"
              onClick={load}
              disabled={loading}
            >
              <RefreshCw size={18} />
            </button>
          </div>
        </div>
        {error && <ErrorBanner message={error} onRetry={load} />}
        {loading || detailLoading ? (
          <div className="page-loading">
            <Spinner label="Đang đọc kết quả đánh giá…" />
          </div>
        ) : !run ? (
          <Empty
            icon={<BarChart3 size={32} />}
            title="Chưa có kết quả đánh giá"
          >
            Khi một lượt benchmark hoàn tất, kết quả thực sẽ xuất hiện ở đây.
            Không có số liệu được tạo sẵn.
          </Empty>
        ) : (
          <>
            <div className="run-meta">
              <span
                className={`badge ${run.status === "completed" ? "success" : "warning"}`}
              >
                {run.status === "completed" ? "Đã hoàn tất" : "Chưa hoàn tất"}
              </span>
              <span>
                Bộ dữ liệu: <strong>{run.split ?? "—"}</strong>
              </span>
              <span>
                Backend: <strong>{run.backend ?? "—"}</strong>
              </span>
              <span>
                Seed: <strong>{run.seed ?? "—"}</strong>
              </span>
              <span>
                Số lần lặp: <strong>{run.repetitions ?? "—"}</strong>
              </span>
            </div>
            {run.status !== "completed" && (
              <div className="warning-banner">
                Lượt đánh giá còn thiếu kết quả. Chưa sử dụng số liệu này để kết
                luận hoặc trình bày.
              </div>
            )}
            <div className="table-scroll">
              <table className="kpi-table">
                <thead>
                  <tr>
                    <th>Chỉ số đánh giá</th>
                    {modes.map((mode) => (
                      <th
                        key={mode.key}
                        className={mode.key === "harness" ? "highlight" : ""}
                      >
                        {mode.label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {kpis.map((kpi) => (
                    <tr key={kpi.key}>
                      <th>
                        <strong>
                          {kpi.label}{" "}
                          {kpi.better === "lower" ? (
                            <ArrowDownRight size={14} />
                          ) : (
                            <ArrowUpRight size={14} />
                          )}
                        </strong>
                        <small>{kpi.description}</small>
                      </th>
                      {modes.map((mode) => (
                        <td
                          key={mode.key}
                          className={mode.key === "harness" ? "highlight" : ""}
                        >
                          {formatMetric(
                            run.summary?.[mode.key]?.[kpi.key],
                            kpi.unit,
                          )}
                          {kpi.unit === "phiên" &&
                            typeof run.summary?.[mode.key]?.[kpi.key] ===
                              "number" && <small> phiên</small>}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="result-footnote">
              <span>— = chưa có mẫu hợp lệ để tính chỉ số.</span>
              <span>
                LLM judge:{" "}
                {run.judge?.status === "completed" ? "Đã chạy" : "Chưa chạy"}
              </span>
            </div>
          </>
        )}
      </section>
      {!loading && !detailLoading && run && (
        <SalesImprovements key={run.run_id} runId={run.run_id} runs={runs} />
      )}
      <div className="method-note">
        <FlaskConical size={23} />
        <div>
          <h3>Đo đúng, rồi mới cải thiện.</h3>
          <p>
            Lỗi giá và độ đầy đủ bàn giao được chấm bằng tiêu chí tất định. Bộ
            held-out được giữ riêng và chỉ chạy sau khi khóa phiên bản.
          </p>
        </div>
      </div>
    </div>
  );
}
