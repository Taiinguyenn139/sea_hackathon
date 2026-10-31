import { useEffect, useState, type FormEvent } from "react";
import { ArrowRight, Check, Download, Target } from "lucide-react";
import { ApiError, errorMessage, request, type RunSummary } from "./api";
import { useStaff } from "./staff-auth";
import { Empty, ErrorBanner, Spinner } from "./ui";

type Action = {
  id: string;
  metric: string;
  label: string;
  title: string;
  priority: "high" | "medium" | "experiment";
  reason: string;
  current: number;
  target: number | null;
  steps: string[];
  suggested_script: string;
  script: string;
  notes: string;
  status: "draft" | "in_progress" | "ready_for_evaluation";
  affected_cases: number;
  evidence: {
    scenario_id: string;
    repetition: number;
    session_number: number | null;
    step: number | null;
    input: string;
    reply: string;
    issues: string[];
  }[];
};
type Plan = {
  run_id: string;
  eligible: boolean;
  mode: string;
  message: string;
  actions: Action[];
};
type Comparison = {
  comparable: boolean;
  reasons: string[];
  outcome:
    "not_comparable" | "improved" | "regressed" | "unchanged" | "insufficient";
  failed_gates?: string[];
  metrics: {
    key: string;
    label: string;
    before: number | null;
    after: number | null;
    delta: number | null;
    direction: string;
  }[];
};
const percent = (value: number | null) =>
  value === null
    ? "—"
    : `${(value * 100).toLocaleString("vi-VN", { maximumFractionDigits: 1 })}%`;
const outcomes = {
  not_comparable: "Chưa thể so sánh hai lượt đánh giá",
  improved: "Có KPI cải thiện, các chỉ số được đối chiếu không giảm",
  regressed: "Có chỉ số giảm chất lượng — cần rà soát thêm",
  unchanged: "Chưa ghi nhận thay đổi trên các KPI được đối chiếu",
  insufficient: "Chưa đủ cơ sở xác nhận cải thiện",
};

function ActionEditor({
  action,
  runId,
  onSaved,
}: {
  action: Action;
  runId: string;
  onSaved: (plan: Plan) => void;
}) {
  const { csrf, expire } = useStaff();
  const [script, setScript] = useState(action.script);
  const [notes, setNotes] = useState(action.notes);
  const [status, setStatus] = useState(action.status);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const dirty =
    script !== action.script ||
    notes !== action.notes ||
    status !== action.status;
  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setSaved(false);
    setError("");
    try {
      const plan = await request<Plan>(
        `/eval/runs/${encodeURIComponent(runId)}/improvements/${action.id}`,
        {
          method: "PATCH",
          csrf,
          body: { script, notes, status },
        },
      );
      const updated = plan.actions.find((item) => item.id === action.id);
      if (updated) {
        setScript(updated.script);
        setNotes(updated.notes);
        setStatus(updated.status);
      }
      onSaved(plan);
      setSaved(true);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) expire();
      else setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <article className="improvement-card">
      <div className="improvement-title">
        <span
          className={`badge ${action.priority === "high" ? "warning" : ""}`}
        >
          {action.priority === "high"
            ? "Ưu tiên cao"
            : action.priority === "experiment"
              ? "Giả thuyết thử nghiệm"
              : "Cần cải thiện"}
        </span>
        <span className="muted">
          {action.label}: <strong>{percent(action.current)}</strong>
        </span>
      </div>
      <h3>{action.title}</h3>
      <p>{action.reason}</p>
      <ol>
        {action.steps.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>
      <div className="improvement-target">
        <Target size={16} />
        {action.target === null
          ? "Mục tiêu thử nghiệm: tăng chuyển đổi, giữ chất lượng tư vấn."
          : `Mục tiêu kiểm tra lại: ${action.label.toLocaleLowerCase("vi")} ${percent(action.target)}.`}
      </div>
      <details className="improvement-evidence">
        <summary>
          Dẫn chứng hội thoại · {action.affected_cases} ca đánh giá
        </summary>
        {!action.evidence.length ? (
          <p>
            KPI tổng hợp có tín hiệu cần rà soát; chưa có dẫn chứng chi tiết
            trong lượt này.
          </p>
        ) : (
          <>
            <p className="muted">
              Hiển thị tối đa 5 ca của Memory Harness; mỗi lần lặp được tính là
              một ca.
            </p>
            {action.evidence.map((item, index) => (
              <div
                className="evidence-example"
                key={`${item.scenario_id}-${item.repetition}-${index}`}
              >
                <strong>
                  {item.scenario_id} · lần lặp {item.repetition + 1}
                  {item.session_number !== null &&
                    ` · phiên ${item.session_number}`}
                  {item.step !== null && ` · lượt ${item.step + 1}`}
                </strong>
                {item.input && (
                  <p>
                    <b>Khách:</b> {item.input}
                  </p>
                )}
                {item.reply && (
                  <p>
                    <b>Trợ lý:</b> {item.reply}
                  </p>
                )}
                {!!item.issues.length && (
                  <small>Dấu hiệu QA/QC: {item.issues.join(", ")}</small>
                )}
              </div>
            ))}
          </>
        )}
      </details>
      <p>
        <a
          href={`/sales?run=${encodeURIComponent(runId)}&action=${encodeURIComponent(action.id)}`}
        >
          Tạo kịch bản hoặc bài luyện từ kế hoạch đã lưu →
        </a>
      </p>
      <form className="improvement-form" onSubmit={save}>
        <label htmlFor={`script-${action.id}`}>Lời tư vấn đề xuất</label>
        <textarea
          id={`script-${action.id}`}
          value={script}
          onChange={(e) => {
            setScript(e.target.value);
            setSaved(false);
          }}
          maxLength={4000}
          rows={3}
          required
          disabled={busy}
        />
        <label htmlFor={`notes-${action.id}`}>
          Ghi chú thay đổi đã thực hiện
        </label>
        <textarea
          id={`notes-${action.id}`}
          value={notes}
          onChange={(e) => {
            setNotes(e.target.value);
            setSaved(false);
          }}
          maxLength={4000}
          rows={2}
          placeholder="Ví dụ: đã cập nhật kịch bản xử lý băn khoăn; cần chạy lại bộ dev."
          disabled={busy}
        />
        <div className="improvement-save">
          <label htmlFor={`status-${action.id}`}>
            Tiến độ
            <select
              id={`status-${action.id}`}
              value={status}
              onChange={(e) => {
                setStatus(e.target.value as Action["status"]);
                setSaved(false);
              }}
              disabled={busy}
            >
              <option value="draft">Đề xuất</option>
              <option value="in_progress">Đang thực hiện</option>
              <option value="ready_for_evaluation">Chờ đánh giá lại</option>
            </select>
          </label>
          <button
            className="button primary"
            disabled={busy || !script.trim() || !dirty}
          >
            {busy ? (
              <Spinner label="Đang lưu…" />
            ) : (
              <>
                <Check size={16} /> Lưu kế hoạch
              </>
            )}
          </button>
          {saved && (
            <span className="muted" role="status">
              Đã lưu kế hoạch.
            </span>
          )}
          {dirty && !busy && (
            <span className="muted">Có thay đổi chưa lưu</span>
          )}
        </div>
        {error && <ErrorBanner message={error} />}
      </form>
    </article>
  );
}

export function SalesImprovements({
  runId,
  runs,
}: {
  runId: string;
  runs: RunSummary[];
}) {
  const { expire } = useStaff();
  const [plan, setPlan] = useState<Plan | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [candidate, setCandidate] = useState("");
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [comparing, setComparing] = useState(false);
  const [comparisonError, setComparisonError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    request<Plan>(`/eval/runs/${encodeURIComponent(runId)}/improvements`, {
      signal: controller.signal,
    })
      .then(setPlan)
      .catch((e) => {
        if (controller.signal.aborted) return;
        if (e instanceof ApiError && e.status === 401) expire();
        else setError(errorMessage(e));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [runId, reload]);
  useEffect(() => {
    setComparison(null);
    setComparisonError("");
    setComparing(false);
    if (!candidate) return;
    const controller = new AbortController();
    setComparing(true);
    request<Comparison>(
      `/eval/runs/${encodeURIComponent(runId)}/comparison?candidate_id=${encodeURIComponent(candidate)}`,
      { signal: controller.signal },
    )
      .then(setComparison)
      .catch((e) => {
        if (controller.signal.aborted) return;
        if (e instanceof ApiError && e.status === 401) expire();
        else setComparisonError(errorMessage(e));
      })
      .finally(() => {
        if (!controller.signal.aborted) setComparing(false);
      });
    return () => controller.abort();
  }, [runId, candidate, reload]);
  function download() {
    if (!plan) return;
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(plan, null, 2)], { type: "application/json" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `${runId}-sales-improvements.json`;
    link.click();
    URL.revokeObjectURL(url);
  }
  const candidates = runs.filter(
    (run) =>
      run.run_id !== runId && run.status === "completed" && run.split === "dev",
  );
  return (
    <section
      className="panel sales-improvements"
      aria-labelledby="improvement-heading"
    >
      <div className="panel-heading">
        <div>
          <span className="mini-label">TỪ PHÂN TÍCH ĐẾN HÀNH ĐỘNG</span>
          <h2 id="improvement-heading">Cải thiện khả năng bán hàng</h2>
          <p className="muted">
            Kết quả QA/QC <ArrowRight size={12} /> Sửa kịch bản tư vấn{" "}
            <ArrowRight size={12} /> Đánh giá lại
          </p>
        </div>
        <button
          className="button secondary"
          onClick={download}
          disabled={!plan || loading}
        >
          <Download size={16} /> Xuất kế hoạch đã lưu
        </button>
      </div>
      {loading ? (
        <div className="page-loading">
          <Spinner label="Đang lập đề xuất cải thiện…" />
        </div>
      ) : error ? (
        <ErrorBanner
          message={error}
          onRetry={() => setReload((value) => value + 1)}
        />
      ) : (
        plan && (
          <div className="improvement-content">
            <p className="muted">{plan.message}</p>
            <p className="improvement-note">
              Lưu kế hoạch để đội ngũ chỉnh sửa chatbot hoặc playbook. Bản nháp
              chưa tự áp dụng vào hội thoại. Chuyển đổi là kết quả mô phỏng,
              chưa chứng minh doanh số thực tế.
            </p>
            {plan.actions.length ? (
              <div className="improvement-list">
                {plan.actions.map((action) => (
                  <ActionEditor
                    key={`${runId}-${action.id}`}
                    action={action}
                    runId={runId}
                    onSaved={setPlan}
                  />
                ))}
              </div>
            ) : (
              <Empty
                icon={<Target size={28} />}
                title="Chưa có đề xuất cần thực hiện"
              >
                {plan.message}
              </Empty>
            )}
            {plan.eligible && (
              <div className="improvement-comparison">
                <h3>Kiểm chứng sau cải thiện</h3>
                <p className="muted">
                  Sau khi cập nhật kịch bản và chạy lại benchmark, chọn lượt mới
                  để đối chiếu cùng bộ dữ liệu, seed và số lần lặp.
                </p>
                <label htmlFor="candidate-run">
                  Lượt đánh giá sau thay đổi
                </label>
                <select
                  id="candidate-run"
                  value={candidate}
                  onChange={(e) => setCandidate(e.target.value)}
                >
                  <option value="">
                    {candidates.length
                      ? "Chọn lượt dev đã hoàn tất…"
                      : "Chưa có lượt dev khác để so sánh"}
                  </option>
                  {candidates.map((run) => (
                    <option key={run.run_id} value={run.run_id}>
                      {run.run_id}
                    </option>
                  ))}
                </select>
                {comparing && <Spinner label="Đang đối chiếu KPI…" />}
                {comparisonError && (
                  <ErrorBanner
                    message={comparisonError}
                    onRetry={() => setReload((value) => value + 1)}
                  />
                )}
                {comparison && (
                  <div aria-live="polite">
                    <p
                      className={
                        comparison.outcome === "regressed"
                          ? "warning-banner"
                          : "improvement-note"
                      }
                    >
                      {outcomes[comparison.outcome]}
                    </p>
                    {!!comparison.reasons.length && (
                      <ul>
                        {comparison.reasons.map((reason) => (
                          <li key={reason}>{reason}</li>
                        ))}
                      </ul>
                    )}
                    {comparison.comparable && (
                      <div className="table-scroll">
                        <table className="kpi-table comparison-table">
                          <thead>
                            <tr>
                              <th>Chỉ số</th>
                              <th>Trước</th>
                              <th>Sau</th>
                              <th>Thay đổi</th>
                            </tr>
                          </thead>
                          <tbody>
                            {comparison.metrics.map((metric) => (
                              <tr key={metric.key}>
                                <th>{metric.label}</th>
                                <td>{percent(metric.before)}</td>
                                <td>{percent(metric.after)}</td>
                                <td>
                                  {metric.delta === null
                                    ? "—"
                                    : `${metric.delta > 0 ? "+" : ""}${(metric.delta * 100).toLocaleString("vi-VN", { maximumFractionDigits: 1 })} điểm %`}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                    {!!comparison.failed_gates?.length && (
                      <p className="muted">
                        Kiểm tra chất lượng chưa đạt:{" "}
                        {comparison.failed_gates.join(", ")}
                      </p>
                    )}
                    <p className="muted">
                      Đối chiếu mô tả thay đổi KPI; chưa xác định quan hệ nhân
                      quả của từng chỉnh sửa.
                    </p>
                  </div>
                )}
              </div>
            )}
          </div>
        )
      )}
    </section>
  );
}
