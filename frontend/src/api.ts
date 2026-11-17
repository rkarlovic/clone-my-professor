// Thin client for the CMP API. Every request carries the dev sign-in header (see identity.ts);
// SSO / LTI replace that before the pilot.

import { devHeaders, type Role } from "./identity";
import { parseSse, type SseEvent } from "./sse";

export interface Course {
  id: string;
  subject_name: string;
  active_version: number | null;
}

export interface VersionSummary {
  documents: number;
  pages: number;
  chunks_embedded: number;
  carried_over: string[];
  sections: Record<string, number>;
  seconds: number;
}

export interface Version {
  version: number;
  status: "draft" | "approved" | "active";
  ingest_status: "queued" | "processing" | "ready" | "failed";
  ingest_error: string | null;
  created_at: string;
  pending_files: string[];
  approved_at: string | null;
  activated_at: string | null;
  summary: VersionSummary | null;
  documents: { file_name: string; file_hash: string }[] | null;
}

export interface SourceRef {
  file_name: string;
  page: number | null;
}

export interface QueueItem {
  id: string;
  question: string;
  reason: string;
  reason_text: string;
  status: "open" | "answered" | "dismissed";
  created_at: string;
  answer: string | null;
  answer_sources: SourceRef[];
  answered_at: string | null;
}

export interface Citation {
  n: number;
  file_name: string;
  pages: number[];
}

export interface Message {
  id: string;
  question: string;
  answer: string;
  citations: Citation[];
  forwarded: boolean;
  feedback: number | null;
  created_at: string;
  teacher_answer: string | null;
  teacher_answered_at: string | null;
}

export interface ChatCourse {
  kb_id: string;
  subject_name: string;
  live: boolean;
  ai_notice: string;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(role: Role, path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  for (const [k, v] of Object.entries(devHeaders(role))) headers.set(k, v);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const r = await fetch(path, { ...init, headers });
  if (!r.ok) throw new ApiError(r.status, await errorText(r));
  return (r.status === 204 ? undefined : await r.json()) as T;
}

async function errorText(r: Response): Promise<string> {
  try {
    const body = await r.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) return body.detail.map((d: { msg: string }) => d.msg).join("; ");
  } catch {
    /* not JSON */
  }
  return `HTTP ${r.status}`;
}

const json = (body: unknown) => JSON.stringify(body);

// --- teacher -----------------------------------------------------------------------------

export const teacherApi = {
  courses: () => request<Course[]>("teacher", "/api/courses"),
  createCourse: (subject_name: string) =>
    request<Course>("teacher", "/api/courses", { method: "POST", body: json({ subject_name }) }),
  versions: (kb: string) => request<Version[]>("teacher", `/api/courses/${kb}/versions`),
  version: (kb: string, n: number) => request<Version>("teacher", `/api/courses/${kb}/versions/${n}`),
  upload: (kb: string, files: File[]) => {
    const form = new FormData();
    for (const f of files) form.append("files", f, f.name);
    return request<Version>("teacher", `/api/courses/${kb}/versions`, { method: "POST", body: form });
  },
  step: (kb: string, n: number, step: "approve" | "activate" | "retry") =>
    request<Version>("teacher", `/api/courses/${kb}/versions/${n}/${step}`, { method: "POST" }),
  discard: (kb: string, n: number) =>
    request<void>("teacher", `/api/courses/${kb}/versions/${n}`, { method: "DELETE" }),
  questions: (kb: string, status: string) =>
    request<QueueItem[]>("teacher", `/api/courses/${kb}/questions?status=${status}`),
  answer: (kb: string, id: string, answer: string, sources: SourceRef[]) =>
    request<QueueItem>("teacher", `/api/courses/${kb}/questions/${id}/answer`, {
      method: "POST",
      body: json({ answer, sources }),
    }),
  dismiss: (kb: string, id: string) =>
    request<QueueItem>("teacher", `/api/courses/${kb}/questions/${id}/dismiss`, { method: "POST" }),
};

// --- student -----------------------------------------------------------------------------

export const studentApi = {
  course: (kb: string) => request<ChatCourse>("student", `/api/chat/${kb}`),
  history: (kb: string) => request<Message[]>("student", `/api/chat/${kb}/messages`),
  feedback: (id: string, helpful: boolean) =>
    request<Message>("student", `/api/chat/messages/${id}/feedback`, {
      method: "POST",
      body: json({ helpful }),
    }),
  /** Ask a question; calls onEvent for each server-sent event until the answer or an error. */
  ask: async (kb: string, question: string, onEvent: (e: SseEvent) => void): Promise<void> => {
    const headers = { ...devHeaders("student"), "Content-Type": "application/json" };
    const r = await fetch(`/api/chat/${kb}/messages`, {
      method: "POST",
      headers,
      body: json({ question }),
    });
    if (!r.ok || !r.body) throw new ApiError(r.status, await errorText(r));
    const reader = r.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;
      const { events, rest } = parseSse(buffer);
      buffer = rest;
      events.forEach(onEvent);
    }
  },
};
