import { useState } from "react";
import { Check, MessageCircle, MessageSquarePlus, Pencil, Search, X } from "lucide-react";
import type { ChatSession } from "./api";

export const CURRENT_SESSION = "bankwise-chat-session";
export const CHAT_HISTORY = "bankwise-chat-history-v1";
export type Conversation = {
  session: ChatSession;
  title: string;
  renamed: boolean;
  createdAt: string;
  updatedAt: string;
};
export function validSession(value: unknown): value is ChatSession {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const s = value as ChatSession;
  return typeof s.session_id === "string" && !!s.session_id &&
    typeof s.session_token === "string" && !!s.session_token &&
    ["unverified", "verified", "identified", "guest"].includes(s.identity_status) &&
    (s.demo_profile === undefined || typeof s.demo_profile === "string");
}
export function conversation(session: ChatSession): Conversation {
  const now = new Date().toISOString();
  return { session, title: "Cuộc trò chuyện mới", renamed: false, createdAt: now, updatedAt: now };
}
export function readChatHistory(storage: Pick<Storage, "getItem">): { current: ChatSession | null; entries: Conversation[] } {
  const parse = (key: string): unknown => {
    try { return JSON.parse(storage.getItem(key) ?? "null"); } catch { return null; }
  };
  const saved = parse(CURRENT_SESSION) ?? parse("mach-chat-session");
  const current = validSession(saved) ? saved : null;
  const raw = parse(CHAT_HISTORY);
  const seen = new Set<string>();
  const entries: Conversation[] = [];
  if (Array.isArray(raw)) for (const item of raw) {
    if (!item || !validSession(item.session) || seen.has(item.session.session_id)) continue;
    seen.add(item.session.session_id);
    const fallback = conversation(item.session);
    entries.push({ ...fallback,
      title: typeof item.title === "string" && item.title.trim() ? item.title.trim().slice(0, 80) : fallback.title,
      renamed: item.renamed === true,
      createdAt: typeof item.createdAt === "string" && Number.isFinite(Date.parse(item.createdAt)) ? item.createdAt : fallback.createdAt,
      updatedAt: typeof item.updatedAt === "string" && Number.isFinite(Date.parse(item.updatedAt)) ? item.updatedAt : fallback.updatedAt,
    });
  }
  if (current && !seen.has(current.session_id)) entries.unshift(conversation(current));
  return { current, entries };
}

export function ChatHistory({ entries, activeId, busy, onNew, onSelect, onRename }: {
  entries: Conversation[]; activeId?: string; busy: boolean;
  onNew: () => void; onSelect: (entry: Conversation) => void; onRename: (id: string, title: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const filtered = entries.filter((entry) => entry.title.toLocaleLowerCase("vi").includes(query.toLocaleLowerCase("vi")))
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  const today = new Date().toLocaleDateString("vi-VN");
  const groups = [
    { name: "Hôm nay", entries: filtered.filter((e) => new Date(e.updatedAt).toLocaleDateString("vi-VN") === today) },
    { name: "Trước đó", entries: filtered.filter((e) => new Date(e.updatedAt).toLocaleDateString("vi-VN") !== today) },
  ];
  return <div className="chat-history">
    <button className="new-conversation" onClick={onNew} disabled={busy}><MessageSquarePlus size={18} /> Cuộc trò chuyện mới</button>
    <label className="history-search"><Search size={16} /><input aria-label="Tìm cuộc trò chuyện" placeholder="Tìm cuộc trò chuyện…" value={query} onChange={(e) => setQuery(e.target.value)} />
      {query && <button aria-label="Xóa tìm kiếm" onClick={() => setQuery("")}><X size={14} /></button>}
    </label>
    <div className="history-items">
      {groups.map((group) => !!group.entries.length && <section key={group.name}>
        <h2>{group.name}</h2>
        {group.entries.map((entry) => <div key={entry.session.session_id} className={`history-row ${entry.session.session_id === activeId ? "is-active" : ""}`}>
          {editing === entry.session.session_id ? <form className="history-rename" onSubmit={(e) => { e.preventDefault(); if (title.trim()) { onRename(editing, title.trim()); setEditing(null); } }}>
            <input autoFocus aria-label="Tên cuộc trò chuyện" value={title} maxLength={80} onChange={(e) => setTitle(e.target.value)} onKeyDown={(e) => { if (e.key === "Escape") setEditing(null); }} />
            <button aria-label="Lưu tên" disabled={!title.trim()}><Check size={16} /></button>
            <button type="button" aria-label="Hủy đổi tên" onClick={() => setEditing(null)}><X size={16} /></button>
          </form> : <>
            <button className="history-select" aria-current={entry.session.session_id === activeId ? "page" : undefined} disabled={busy} onClick={() => onSelect(entry)}>
              <MessageCircle size={16} /><span><strong>{entry.title}</strong><small>{entry.session.handoff_id && entry.session.status !== "completed" ? "Chờ chuyên viên" : entry.session.status === "ended" || entry.session.status === "completed" ? "Đã kết thúc" : "Có thể tiếp tục"}</small></span>
            </button>
            <button className="history-edit" aria-label={`Đổi tên: ${entry.title}`} onClick={() => { setTitle(entry.title); setEditing(entry.session.session_id); }}><Pencil size={13} /></button>
          </>}
        </div>)}
      </section>)}
      {!filtered.length && <p className="history-empty">{query ? "Không tìm thấy cuộc trò chuyện." : "Các cuộc trò chuyện của bạn sẽ xuất hiện ở đây."}</p>}
    </div>
    <p className="history-scope">Lịch sử trong tab này · Mỗi cuộc trò chuyện có nội dung riêng.</p>
  </div>;
}
