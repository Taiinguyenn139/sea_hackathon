const BASE = import.meta.env.DEV ? "/api" : "";
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}
function responseError(payload: unknown, status: number): ApiError {
  const data = payload as { detail?: unknown; error?: unknown } | null;
  const detail =
    typeof data?.detail === "string"
      ? data.detail
      : typeof data?.error === "string"
        ? data.error
        : status === 401
          ? "Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại."
          : status === 429
            ? "Bạn đã thử quá nhiều lần. Vui lòng chờ trước khi thử lại."
            : "Yêu cầu chưa hoàn tất. Vui lòng thử lại.";
  const localized: Record<string, string> = {
    "Invalid credentials": "Mật khẩu chưa đúng. Vui lòng thử lại.",
    "Staff login required":
      "Vui lòng đăng nhập để truy cập không gian chuyên viên.",
    "Please try again later": "Bạn đã thử quá nhiều lần. Vui lòng thử lại sau.",
    "CSRF verification failed":
      "Phiên bảo mật đã thay đổi. Vui lòng đăng nhập lại.",
    "Session capability required":
      "Phiên trò chuyện không còn hợp lệ. Bạn có thể bắt đầu phiên mới.",
    "Complete handoff before ending session":
      "Vui lòng đợi chuyên viên hoàn tất trước khi mở phiên mới.",
  };
  return new ApiError(localized[detail] ?? detail, status);
}
export async function request<T>(
  path: string,
  options: {
    method?: string;
    body?: unknown;
    token?: string;
    csrf?: string;
    signal?: AbortSignal;
  } = {},
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (options.token) headers.Authorization = `Bearer ${options.token}`;
  if (options.csrf) headers["X-CSRF-Token"] = options.csrf;
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method: options.method ?? "GET",
      headers,
      credentials: "include",
      body:
        options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new ApiError("Chưa kết nối được máy chủ. Vui lòng thử lại sau.", 0);
  }
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw responseError(payload, response.status);
  if (payload === null)
    throw new ApiError("Máy chủ trả về dữ liệu không hợp lệ.", response.status);
  return payload as T;
}
export function errorMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "Đã xảy ra lỗi. Vui lòng thử lại.";
}
export type Profile = {
  id?: string;
  phone?: string;
  runtime_profile?: string | null;
  key?: string;
  demo_profile?: string;
  name?: string;
  full_name?: string;
  label?: string;
};
export type ChatSession = {
  telegram_url?: string;
  session_id: string;
  user_id?: string | null;
  session_token: string;
  identity_status: string;
  brief?: Record<string, unknown>;
  handoff_id?: string;
  status?: string;
  demo_profile?: string;
};
export function createChatSession(): Promise<ChatSession> {
  return request<ChatSession>("/sessions", {
    method: "POST",
    body: { mode: "harness" },
  });
}
export type Turn = {
  turn_id?: string;
  id?: string;
  role: string;
  text: string;
  created_at?: string;
};
export type Reply = {
  reply: string;
  identity_status?: string;
  user_id?: string | null;
  brief?: Record<string, unknown>;
  used_fact_ids: string[];
  guard_events: unknown[];
  session_id: string;
  handoff_id?: string;
};
export type ReplyStreamEvent =
  { type: "text"; text: string } | { type: "status"; stage: string };

export async function streamReply(
  session: ChatSession,
  text: string,
  messageId: string,
  onEvent: (event: ReplyStreamEvent) => void,
  signal?: AbortSignal,
): Promise<Reply> {
  let response: Response;
  try {
    response = await fetch(
      `${BASE}/sessions/${encodeURIComponent(session.session_id)}/messages/stream`,
      {
        method: "POST",
        credentials: "include",
        headers: {
          Accept: "text/event-stream",
          "Content-Type": "application/json",
          Authorization: `Bearer ${session.session_token}`,
        },
        body: JSON.stringify({ text, client_message_id: messageId }),
        signal,
      },
    );
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new ApiError("Chưa kết nối được máy chủ. Vui lòng thử lại sau.", 0);
  }
  if (!response.ok)
    throw responseError(
      await response.json().catch(() => null),
      response.status,
    );
  if (
    !response.body ||
    !response.headers.get("content-type")?.includes("text/event-stream")
  )
    throw new ApiError("Máy chủ trả về luồng phản hồi không hợp lệ.", 502);
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer = (buffer + decoder.decode(value, { stream: !done })).replace(
        /\r\n/g,
        "\n",
      );
      let boundary: number;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        let kind = "message";
        const lines: string[] = [];
        for (const line of frame.split("\n")) {
          if (line.startsWith("event:")) kind = line.slice(6).trim();
          if (line.startsWith("data:")) lines.push(line.slice(5).trimStart());
        }
        if (!lines.length) continue;
        const data: Record<string, unknown> = JSON.parse(lines.join("\n"));
        if (kind === "result") {
          if (
            typeof data.reply !== "string" ||
            data.session_id !== session.session_id
          )
            throw new ApiError("Máy chủ trả về dữ liệu không hợp lệ.", 502);
          return data as Reply;
        }
        if (kind === "error")
          throw responseError(
            { detail: data.message },
            typeof data.status === "number" ? data.status : 500,
          );
        if (kind === "text" && typeof data.text === "string")
          onEvent({ type: "text", text: data.text });
        if (kind === "status" && typeof data.stage === "string")
          onEvent({ type: "status", stage: data.stage });
      }
      if (buffer.length > 128000)
        throw new ApiError("Luồng phản hồi vượt giới hạn cho phép.", 502);
      if (done)
        throw new ApiError("Phản hồi bị gián đoạn. Vui lòng thử lại.", 502);
    }
  } catch (error) {
    if (
      error instanceof ApiError ||
      (error instanceof DOMException && error.name === "AbortError")
    )
      throw error;
    throw new ApiError("Phản hồi bị gián đoạn. Vui lòng thử lại.", 502);
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
export type Handoff = {
  handoff_id: string;
  from_session?: string;
  session_id?: string;
  customer_id?: string;
  reason: string;
  status: string;
  brief?: Record<string, unknown>;
  open_questions?: string[];
  next_step?: string;
  bot_said?: unknown[];
};
export type Callback = {
  task_id?: string;
  callback_id?: string;
  id?: string;
  customer_id?: string;
  time?: string;
  due_at?: string;
  status: string;
};
export type RunSummary = {
  run_id: string;
  status: string;
  split?: string;
  created_at?: string;
  backend?: string;
  seed?: number;
  repetitions?: number;
  schema_version?: number | string;
};
export function asItems<T>(
  value: T[] | { items?: T[]; profiles?: T[]; runs?: T[]; turns?: T[] },
): T[] {
  return Array.isArray(value)
    ? value
    : (value.items ?? value.profiles ?? value.runs ?? value.turns ?? []);
}

export type TurnPair = {
  id?: string;
  text: string;
  reply?: string;
  created_at?: string;
};
export function normalizeTurns(
  value:
    | Array<Turn | TurnPair>
    | { items?: Array<Turn | TurnPair>; turns?: Array<Turn | TurnPair> },
): Turn[] {
  return asItems(value).flatMap((item): Turn[] => {
    if ("role" in item) return [item];
    const messages: Turn[] = [
      {
        id: `${item.id ?? ""}-user`,
        role: "user",
        text: item.text,
        created_at: item.created_at,
      },
    ];
    if (item.reply)
      messages.push({
        id: `${item.id ?? ""}-assistant`,
        role: "assistant",
        text: item.reply,
        created_at: item.created_at,
      });
    return messages;
  });
}

export async function loadTurns(
  sessionId: string,
  token?: string,
): Promise<Turn[]> {
  const turns: Turn[] = [];
  for (let offset = 0; ; offset += 100) {
    const result = await request<{ items: Array<Turn | TurnPair> }>(
      `/sessions/${sessionId}/turns${offset ? `?offset=${offset}&limit=100` : ""}`,
      { token },
    );
    turns.push(...normalizeTurns(result));
    if (result.items.length < 100) return turns;
  }
}
