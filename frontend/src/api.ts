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
  check_status: "queued" | "running" | "done" | "failed" | null;
  origin?: "upload" | "lms";
  removed_files?: string[];
  auto_activate?: boolean;
  is_base?: boolean;
  check: CheckReport | null;
  summary: VersionSummary | null;
  documents: { file_name: string; file_hash: string }[] | null;
}

export interface CheckReport {
  questions: number;
  found_top5: number;
  recall_at_5: number | null;
  written: number;
  misses: { id: string; question: string; file_name: string; page: number }[];
  seconds: number;
}

export interface CheckQuestion {
  id: string;
  question: string;
  style: string;
  file_name: string;
  page: number;
  created_at: string;
}

export interface PersonaSettings {
  address: "vi" | "ti";
  answer_length: "short" | "medium" | "long";
  exercise_help: "hints" | "guided" | "concepts";
  examples: boolean;
  terminology: string;
  forward_topics: string[];
}

export interface PersonaCheckItem {
  kind: string;
  question: string;
  forwarded: string | null;
  answer: string;
  expected: string | null;
  cites_expected: boolean;
}

export interface PersonaReport {
  passed: boolean;
  reasons: string[];
  check_questions?: number;
  answered?: number;
  generic?: number;
  forwarded?: number;
  items?: PersonaCheckItem[];
  seconds?: number;
}

export interface Persona {
  version: number;
  status: "draft" | "checking" | "active" | "rejected" | "retired";
  settings: PersonaSettings;
  free_text: string;
  prompt_text: string;
  created_at: string;
  activated_at: string | null;
  check_report: PersonaReport | null;
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
  remove: (kb: string, file_names: string[]) =>
    request<Version>("teacher", `/api/courses/${kb}/remove`, {
      method: "POST",
      body: json({ file_names }),
    }),
  discard: (kb: string, n: number) =>
    request<void>("teacher", `/api/courses/${kb}/versions/${n}`, { method: "DELETE" }),
  questions: (kb: string, status: string) =>
    request<QueueItem[]>("teacher", `/api/courses/${kb}/questions?status=${status}`),
  answer: (kb: string, id: string, answer: string, sources: SourceRef[]) =>
    request<QueueItem>("teacher", `/api/courses/${kb}/questions/${id}/answer`, {
      method: "POST",
      body: json({ answer, sources }),
    }),
  checks: (kb: string) => request<CheckQuestion[]>("teacher", `/api/courses/${kb}/checks`),
  deleteCheck: (kb: string, id: string) =>
    request<void>("teacher", `/api/courses/${kb}/checks/${id}`, { method: "DELETE" }),
  dismiss: (kb: string, id: string) =>
    request<QueueItem>("teacher", `/api/courses/${kb}/questions/${id}/dismiss`, { method: "POST" }),
};

/** POST that answers with server-sent events; calls onEvent for each until the stream ends. */
async function streamPost(
  role: Role,
  path: string,
  body: unknown,
  onEvent: (e: SseEvent) => void,
): Promise<void> {
  const headers = { ...devHeaders(role), "Content-Type": "application/json" };
  const r = await fetch(path, { method: "POST", headers, body: json(body) });
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
}

export const personaApi = {
  list: (kb: string) => request<Persona[]>("teacher", `/api/courses/${kb}/persona`),
  save: (kb: string, settings: PersonaSettings, free_text: string) =>
    request<Persona>("teacher", `/api/courses/${kb}/persona`, {
      method: "PUT",
      body: json({ settings, free_text }),
    }),
  activate: (kb: string, n: number) =>
    request<Persona>("teacher", `/api/courses/${kb}/persona/${n}/activate`, { method: "POST" }),
  preview: (kb: string, n: number, question: string, onEvent: (e: SseEvent) => void) =>
    streamPost("teacher", `/api/courses/${kb}/persona/${n}/preview`, { question }, onEvent),
};

export interface LmsLink {
  id: string;
  platform: string;
  context_title: string | null;
  sync_available: boolean;
  sync_enabled: boolean;
  last_sync_at: string | null;
  last_sync_status: string | null;
  last_sync_report: { removed_files?: string[]; new_files?: string[]; removal_skipped?: string } | null;
}

export const lmsApi = {
  links: (kb: string) => request<LmsLink[]>("teacher", `/api/courses/${kb}/lms`),
  settings: (kb: string, id: string, sync_enabled: boolean) =>
    request<LmsLink>("teacher", `/api/courses/${kb}/lms/${id}`, {
      method: "PUT",
      body: json({ sync_enabled }),
    }),
  syncNow: (kb: string, id: string) =>
    request<{ queued: boolean }>("teacher", `/api/courses/${kb}/lms/${id}/sync`, { method: "POST" }),
};

export interface LinkInfo {
  context_title: string | null;
  courses: { id: string; subject_name: string }[];
}

export const ltiApi = {
  linkInfo: () => request<LinkInfo>("teacher", "/api/lti/link"),
  link: (body: { kb_id?: string; new_course_name?: string }) =>
    request<{ kb_id: string; token: string }>("teacher", "/api/lti/link", {
      method: "POST",
      body: json(body),
    }),
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
  ask: (kb: string, question: string, onEvent: (e: SseEvent) => void) =>
    streamPost("student", `/api/chat/${kb}/messages`, { question }, onEvent),
};
