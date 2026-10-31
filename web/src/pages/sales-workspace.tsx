import { useEffect, useState, type FormEvent } from "react";
import { request, errorMessage, ApiError, type RunSummary } from "../api";
import { useStaff } from "../staff-auth";
import { PageHeading, ErrorBanner, Spinner } from "../ui";

type Item = Record<string, any>;
type Overview = {
  active: string;
  playbooks: Item[];
  jobs: Item[];
  activations: Item[];
  profiles: Item[];
  exercises: Item[];
  attempts: Item[];
  rubric: string[];
  levels: string[];
};
const statusLabel: Record<string, string> = {
  draft: "Nháp",
  evaluating: "Đang đánh giá",
  failed: "Cần sửa / chạy lại",
  awaiting_review: "Chờ duyệt",
  approved: "Đã duyệt",
  rejected: "Từ chối",
  queued: "Đang xếp hàng",
  running: "Đang chạy",
  completed: "Hoàn tất",
  published: "Đã phát hành",
  submitted: "Chờ chấm",
  graded: "Đã chấm",
};
function Checklist({ checks }: { checks: Item }) {
  return (
    <div className="improvement-note">
      <p>
        Đối chiếu dữ kiện:{" "}
        {checks.oracle_available
          ? "Có dữ liệu theo tình huống"
          : "Cần người chấm đối chiếu nguồn"}
        .
      </p>
      <p>
        Phát biểu số liệu chưa khớp:{" "}
        {checks.unsupported_numeric_claims
          ?.map((v: string[]) => `${v[1]} ${v[0]}`)
          .join(", ") || "Chưa ghi nhận"}
        .
      </p>
      <p>
        Từ khóa cần bổ sung: {checks.missing_terms?.join(", ") || "Không có"}.
      </p>
      <p>
        Dấu hiệu hỏi lại cần rà soát:{" "}
        {checks.repeated_question_signals?.join(", ") || "Chưa ghi nhận"}.
      </p>
      <small>
        Checklist hỗ trợ đối chiếu; người chấm xác nhận tính phù hợp và cho điểm
        kỹ năng.
      </small>
    </div>
  );
}
export function SalesWorkspace() {
  const { csrf, expire } = useStaff();
  const [data, setData] = useState<Overview | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const params = new URLSearchParams(location.search);
  const [run, setRun] = useState(params.get("run") ?? "");
  const [actions, setActions] = useState<Item[]>([]);
  const [action, setAction] = useState(params.get("action") ?? "");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState("playbooks");
  const [profile, setProfile] = useState("");
  const [reviewer, setReviewer] = useState("");
  const [notes, setNotes] = useState("");
  const [scores, setScores] = useState([0, 0, 0, 0, 0]);
  const [comments, setComments] = useState(["", "", "", "", ""]);
  const [safe, setSafe] = useState(false);
  const [grading, setGrading] = useState("");
  function fail(e: unknown) {
    if (e instanceof ApiError && e.status === 401) expire();
    else setError(errorMessage(e));
  }
  async function load() {
    try {
      setData(await request<Overview>("/sales/overview"));
    } catch (e) {
      fail(e);
    }
  }
  useEffect(() => {
    void load();
    request<{ runs: RunSummary[] }>("/eval/runs")
      .then((d) => {
        const values = d.runs.filter(
          (r) => r.split === "dev" && r.status === "completed",
        );
        setRuns(values);
        setRun((prev) => prev || values[0]?.run_id || "");
      })
      .catch(fail);
    const timer = setInterval(() => void load(), 4000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setActions([]);
    if (run)
      request<{ actions: Item[] }>(
        `/eval/runs/${encodeURIComponent(run)}/improvements`,
        { signal: controller.signal },
      )
        .then((d) => {
          setActions(d.actions);
          setAction((prev) =>
            d.actions.some((a) => a.id === prev)
              ? prev
              : (d.actions[0]?.id ?? ""),
          );
        })
        .catch((e) => {
          if (!controller.signal.aborted) fail(e);
        });
    return () => controller.abort();
  }, [run]);
  async function write(path: string, body: unknown = {}) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await request(path, { method: "POST", csrf, body });
      await load();
      setNotice("Đã lưu thao tác.");
      return true;
    } catch (e) {
      fail(e);
      return false;
    } finally {
      setBusy(false);
    }
  }
  const selected = actions.find((a) => a.id === action);
  const receipt = { reviewer, notes };
  const canReview = !!reviewer.trim() && !!notes.trim();
  function fields(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    return Object.fromEntries(new FormData(event.currentTarget));
  }
  if (!data)
    return (
      <div className="workspace-page">
        {error ? (
          <ErrorBanner message={error} onRetry={load} />
        ) : (
          <Spinner label="Đang tải không gian cải thiện…" />
        )}
      </div>
    );
  return (
    <div className="workspace-page sales-workspace">
      <PageHeading
        eyebrow="PHÁT TRIỂN NĂNG LỰC BÁN HÀNG"
        title="Từ điều đã học, đến lần tư vấn tốt hơn."
        description="Cải thiện chatbot có kiểm chứng và luyện tập cho chuyên viên."
      />
      <p className="improvement-note">
        Kịch bản đang áp dụng: <strong>{data.active}</strong>. Hồ sơ luyện tập
        dùng chung trong console nội bộ, chưa phải tài khoản cá nhân.
      </p>
      <div className="tabs">
        {[
          ["playbooks", "Kịch bản chatbot"],
          ["practice", "Luyện tập"],
          ["review", "Chấm bài"],
        ].map(([key, label]) => (
          <button
            key={key}
            aria-pressed={tab === key}
            className={tab === key ? "active" : ""}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>
      {error && <ErrorBanner message={error} onRetry={load} />}
      {notice && (
        <p role="status" className="success-message">
          {notice}
        </p>
      )}
      <section className="panel improvement-card">
        <h2>Nguồn QA/QC</h2>
        <label>
          Lượt dev đã hoàn tất
          <select value={run} onChange={(e) => setRun(e.target.value)}>
            <option value="">Chọn lượt đánh giá</option>
            {runs.map((r) => (
              <option key={r.run_id}>{r.run_id}</option>
            ))}
          </select>
        </label>
        <label>
          Đề xuất
          <select value={action} onChange={(e) => setAction(e.target.value)}>
            <option value="">Chọn đề xuất</option>
            {actions.map((a) => (
              <option key={a.id} value={a.id}>
                {a.title}
              </option>
            ))}
          </select>
        </label>
        {selected && (
          <>
            <p>{selected.reason}</p>
            <ol>
              {selected.steps.map((s: string) => (
                <li key={s}>{s}</li>
              ))}
            </ol>
            <details>
              <summary>Dẫn chứng</summary>
              {selected.evidence.map((e: Item, i: number) => (
                <p key={i}>
                  {i + 1}. {e.scenario_id}: {e.input}
                  <br />
                  Trợ lý: {e.reply}
                </p>
              ))}
            </details>
          </>
        )}
      </section>
      {tab === "playbooks" && (
        <>
          <form
            className="panel improvement-card improvement-form"
            onSubmit={(e) => {
              const f = fields(e);
              void write("/sales/playbooks", {
                ...f,
                run_id: run,
                action_id: action,
              });
            }}
          >
            <h2>Tạo phiên bản kịch bản</h2>
            <p>
              Các lỗi tool, memory, số liệu và bàn giao cần task kỹ thuật riêng.
              Kịch bản mới chỉ hỗ trợ lời tư vấn, không chứa số liệu cố định.
            </p>
            <label>
              Phiên bản gốc
              <select name="base_version" defaultValue="v1">
                <option>v1</option>
                {data.playbooks
                  .filter((v) => v.status === "approved")
                  .map((v) => (
                    <option key={v.id}>{v.id}</option>
                  ))}
              </select>
            </label>
            <label>
              Áp dụng khi
              <select name="trigger">
                {[
                  "rate_high",
                  "need_spouse",
                  "discover",
                  "quote",
                  "objection",
                  "followup",
                  "apply",
                ].map((t) => (
                  <option key={t}>{t}</option>
                ))}
              </select>
            </label>
            <label>
              Lời tư vấn mới
              <textarea
                key={action}
                name="text"
                defaultValue={
                  selected?.script ?? selected?.suggested_script ?? ""
                }
                required
                maxLength={4000}
              />
            </label>
            <button
              className="button primary"
              disabled={
                busy || !["next_step", "relevant_offer"].includes(action)
              }
            >
              Tạo bản nháp bất biến
            </button>
          </form>
          <section className="panel improvement-card improvement-form">
            <h2>Thông tin duyệt / kích hoạt</h2>
            <label>
              Người phụ trách
              <input
                value={reviewer}
                onChange={(e) => setReviewer(e.target.value)}
                maxLength={100}
              />
            </label>
            <label>
              Nhận xét và lý do
              <textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                maxLength={2000}
              />
            </label>
          </section>
          {data.playbooks.map((v) => {
            const job = data.jobs.find((j) => j.id === v.job_id);
            return (
              <article className="panel improvement-card" key={v.id}>
                <h3>{v.id}</h3>
                <span className="badge">{statusLabel[v.status]}</span>
                <p>
                  Gốc: {v.base_version} · Nguồn: {v.run_id} · KPI: {v.metric}
                </p>
                <p>{v.text}</p>
                <details>
                  <summary>Đối chiếu nội dung trước / sau</summary>
                  <pre>{JSON.stringify(v.base_content, null, 2)}</pre>
                  <pre>{JSON.stringify(v.content, null, 2)}</pre>
                </details>
                {job && (
                  <>
                    <p>
                      Đánh giá: {statusLabel[job.status]} {job.error}
                    </p>
                    {job.comparison && (
                      <>
                        <p>
                          Kết quả:{" "}
                          {
                            (
                              {
                                improved: "KPI cải thiện",
                                unchanged: "KPI chưa thay đổi",
                                regressed: "Có KPI suy giảm",
                                insufficient: "Chưa đủ cơ sở",
                                not_comparable: "Điều kiện chưa khớp",
                              } as Record<string, string>
                            )[job.comparison.outcome]
                          }
                        </p>
                        {job.comparison.reasons.map((r: string) => (
                          <p key={r}>{r}</p>
                        ))}
                        <div className="table-scroll">
                          <table className="kpi-table">
                            <thead>
                              <tr>
                                <th>KPI</th>
                                <th>Trước</th>
                                <th>Sau</th>
                              </tr>
                            </thead>
                            <tbody>
                              {job.comparison.metrics.map((m: Item) => (
                                <tr key={m.key}>
                                  <th>{m.label}</th>
                                  <td>
                                    {m.before === null
                                      ? "—"
                                      : `${(m.before * 100).toFixed(1)}%`}
                                  </td>
                                  <td>
                                    {m.after === null
                                      ? "—"
                                      : `${(m.after * 100).toFixed(1)}%`}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </>
                    )}
                  </>
                )}
                <div className="improvement-save">
                  {["draft", "failed", "rejected"].includes(v.status) && (
                    <button
                      className="button secondary"
                      disabled={busy}
                      onClick={() =>
                        void write(`/sales/playbooks/${v.id}/evaluate`)
                      }
                    >
                      Chạy đánh giá cách ly
                    </button>
                  )}
                  <button
                    className="button secondary"
                    onClick={() => {
                      setAction(v.action_id);
                      setRun(v.run_id);
                    }}
                  >
                    Chọn nguồn để tạo bản sửa mới
                  </button>
                  {v.status === "awaiting_review" && (
                    <>
                      <button
                        className="button primary"
                        disabled={busy || !canReview}
                        onClick={() =>
                          void write(`/sales/playbooks/${v.id}/review`, {
                            ...receipt,
                            approve: true,
                          })
                        }
                      >
                        Duyệt bản sửa
                      </button>
                      <button
                        className="button secondary"
                        disabled={busy || !canReview}
                        onClick={() =>
                          void write(`/sales/playbooks/${v.id}/review`, {
                            ...receipt,
                            approve: false,
                          })
                        }
                      >
                        Từ chối
                      </button>
                    </>
                  )}
                  {v.status === "approved" && (
                    <button
                      className="button primary"
                      disabled={busy || !canReview || data.active === v.id}
                      onClick={() =>
                        void write(`/sales/playbooks/${v.id}/activate`, receipt)
                      }
                    >
                      Kích hoạt
                    </button>
                  )}
                  {data.activations.some((a) => a.version === v.id) && (
                    <button
                      className="button secondary"
                      disabled={busy || !canReview || data.active === v.id}
                      onClick={() =>
                        void write(`/sales/playbooks/${v.id}/rollback`, receipt)
                      }
                    >
                      Quay lại bản này
                    </button>
                  )}
                </div>
              </article>
            );
          })}
          <section className="panel improvement-card">
            <h2>Lịch sử áp dụng</h2>
            <button
              className="button secondary"
              disabled={busy || !canReview || data.active === "v1"}
              onClick={() =>
                void write("/sales/playbooks/v1/rollback", receipt)
              }
            >
              Quay lại v1
            </button>
            {data.activations.map((a) => (
              <p key={a.id}>
                {a.at} · {a.previous} → {a.version} · {a.reviewer}: {a.notes}
              </p>
            ))}
          </section>
        </>
      )}
      {tab === "practice" && (
        <>
          <form
            className="panel improvement-card improvement-form"
            onSubmit={(e) => {
              const f = fields(e);
              void write("/sales/practice/profiles", f);
            }}
          >
            <h2>Tạo hồ sơ luyện tập</h2>
            <label>
              Mã chuyên viên
              <input name="code" required pattern="[a-zA-Z0-9_-]{1,40}" />
            </label>
            <label>
              Họ tên
              <input name="name" required maxLength={100} />
            </label>
            <button disabled={busy} className="button secondary">
              Tạo hồ sơ
            </button>
          </form>
          <form
            className="panel improvement-card improvement-form"
            onSubmit={(e) => {
              const f = fields(e);
              void write("/sales/practice/exercises", {
                ...f,
                run_id: run,
                action_id: action,
                evidence_index: Number(f.evidence_index),
                required_terms: String(f.required_terms)
                  .split(",")
                  .map((s) => s.trim())
                  .filter(Boolean),
              });
            }}
          >
            <h2>Soạn bài luyện từ dẫn chứng</h2>
            <label>
              Tiêu đề
              <input name="title" required maxLength={200} />
            </label>
            <label>
              Dẫn chứng
              <select name="evidence_index">
                {selected?.evidence.map((e: Item, i: number) => (
                  <option value={i} key={i}>
                    {i + 1}. {e.scenario_id}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Câu hỏi
              <textarea name="question" required maxLength={2000} />
            </label>
            <label>
              Từ khóa cần có, cách nhau bằng dấu phẩy
              <input name="required_terms" />
            </label>
            <button
              className="button primary"
              disabled={busy || !selected?.evidence.length}
            >
              Tạo bài nháp
            </button>
          </form>
          <label>
            Hồ sơ đang luyện
            <select
              value={profile}
              onChange={(e) => setProfile(e.target.value)}
            >
              <option value="">Chọn chuyên viên</option>
              {data.profiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.code} · {p.name}
                </option>
              ))}
            </select>
          </label>
          {data.exercises.map((ex) => (
            <article className="panel improvement-card" key={ex.id}>
              <h3>{ex.title}</h3>
              <span className="badge">{statusLabel[ex.status]}</span>
              <p>Bối cảnh: {ex.context.input}</p>
              <p>{ex.question}</p>
              <p>Rubric: {data.rubric.join(" · ")}</p>
              <p>
                Mức điểm: {data.levels.map((l, i) => `${i}: ${l}`).join("; ")}
              </p>
              {ex.status === "draft" ? (
                <form
                  className="improvement-form"
                  onSubmit={(e) => {
                    const f = fields(e);
                    void write(`/sales/practice/exercises/${ex.id}/publish`, f);
                  }}
                >
                  <details>
                    <summary>Rà soát dẫn chứng và hướng dẫn</summary>
                    <p>{ex.context.reply}</p>
                    <p>{ex.steps.join(" ")}</p>
                  </details>
                  <label>
                    Người duyệt bài
                    <input name="reviewer" required maxLength={100} />
                  </label>
                  <label>
                    Nhận xét rà soát
                    <input name="notes" required maxLength={2000} />
                  </label>
                  <button className="button secondary" disabled={busy}>
                    Phát hành bài luyện
                  </button>
                </form>
              ) : (
                <form
                  className="improvement-form"
                  onSubmit={(e) => {
                    const f = fields(e);
                    void write(`/sales/practice/exercises/${ex.id}/attempts`, {
                      ...f,
                      profile_id: profile,
                    });
                  }}
                >
                  <label>
                    Câu trả lời
                    <textarea name="answer" required maxLength={8000} />
                  </label>
                  <button
                    className="button primary"
                    disabled={busy || !profile}
                  >
                    Nộp bài / luyện lại
                  </button>
                </form>
              )}
              {data.attempts
                .filter(
                  (a) => a.exercise_id === ex.id && a.profile_id === profile,
                )
                .slice()
                .reverse()
                .map((a, i) => (
                  <div className="evidence-example" key={a.id}>
                    <strong>
                      Lần {i + 1}: {statusLabel[a.status]}
                    </strong>
                    <p>{a.answer}</p>
                    <Checklist checks={a.checks} />
                    {a.grade && (
                      <>
                        <p>
                          Điểm người chấm: {a.grade.total}/100 ·{" "}
                          {a.grade.passed
                            ? "Đạt"
                            : "Cần luyện thêm / xử lý điều kiện an toàn"}
                        </p>
                        {a.grade.comments.map((c: string, j: number) => (
                          <p key={j}>
                            {data.rubric[j]}: {a.grade.scores[j]}/4 — {c}
                          </p>
                        ))}
                      </>
                    )}
                  </div>
                ))}
            </article>
          ))}
        </>
      )}
      {tab === "review" && (
        <section className="panel improvement-card improvement-form">
          <h2>Hàng đợi chấm bài</h2>
          <label>
            Lần nộp
            <select
              value={grading}
              onChange={(e) => {
                setGrading(e.target.value);
                setScores([0, 0, 0, 0, 0]);
                setComments(["", "", "", "", ""]);
                setSafe(false);
              }}
            >
              <option value="">Chọn lần nộp chưa chấm</option>
              {data.attempts
                .filter((a) => a.status === "submitted")
                .map((a) => (
                  <option key={a.id} value={a.id}>
                    {data.profiles.find((p) => p.id === a.profile_id)?.name} ·{" "}
                    {
                      data.exercises.find((ex) => ex.id === a.exercise_id)
                        ?.title
                    }{" "}
                    · {a.created_at}
                  </option>
                ))}
            </select>
          </label>
          {grading &&
            (() => {
              const a = data.attempts.find((a) => a.id === grading);
              if (!a) return null;
              return (
                <>
                  <p>
                    {
                      data.exercises.find((ex) => ex.id === a.exercise_id)
                        ?.question
                    }
                  </p>
                  <blockquote>{a.answer}</blockquote>
                  <Checklist checks={a.checks} />
                  <p>{data.levels.map((l, i) => `${i}: ${l}`).join("; ")}</p>
                </>
              );
            })()}
          {data.rubric.map((r, i) => (
            <div className="improvement-form" key={r}>
              <label>
                {r}
                <select
                  value={scores[i]}
                  onChange={(e) =>
                    setScores((prev) =>
                      prev.map((v, j) =>
                        i === j ? Number(e.target.value) : v,
                      ),
                    )
                  }
                >
                  {[0, 1, 2, 3, 4].map((n) => (
                    <option key={n}>{n}</option>
                  ))}
                </select>
              </label>
              <label>
                Nhận xét có dẫn chứng — {r}
                <textarea
                  value={comments[i]}
                  onChange={(e) =>
                    setComments((prev) =>
                      prev.map((v, j) => (i === j ? e.target.value : v)),
                    )
                  }
                  maxLength={2000}
                />
              </label>
            </div>
          ))}
          <label>
            Người chấm
            <input
              value={reviewer}
              onChange={(e) => setReviewer(e.target.value)}
              maxLength={100}
            />
          </label>
          <label>
            Nhận xét chung
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              maxLength={2000}
            />
          </label>
          <label>
            <input
              type="checkbox"
              checked={safe}
              onChange={(e) => setSafe(e.target.checked)}
            />
            Đã đối chiếu số liệu, điều kiện phù hợp và xử lý các điểm chưa an
            toàn
          </label>
          <button
            className="button primary"
            disabled={
              busy || !grading || !canReview || comments.some((c) => !c.trim())
            }
            onClick={async () => {
              if (
                await write(`/sales/practice/attempts/${grading}/grade`, {
                  ...receipt,
                  scores,
                  comments,
                  safety_resolved: safe,
                })
              )
                setGrading("");
            }}
          >
            Lưu điểm và phản hồi
          </button>
        </section>
      )}
    </div>
  );
}
