// Who the pages act as.
// - After an LTI launch: a session token (from the URL fragment, see pages/LtiPage.tsx), kept
//   in sessionStorage for this tab only and sent as `Authorization: Bearer …`.
// - Development only: a name typed on the start page, sent as X-CMP-Dev-Teacher /
//   X-CMP-Dev-Student (the backend accepts it only with AUTH_MODE=dev).
// Storage can be blocked (private windows, some LMS iframes), so every access is guarded and
// falls back to memory.

export type Role = "teacher" | "student";

const KEY: Record<Role, string> = { teacher: "cmp.dev.teacher", student: "cmp.dev.student" };
const TOKEN_KEY: Record<Role, string> = { teacher: "cmp.token.teacher", student: "cmp.token.student" };
const HEADER: Record<Role, string> = {
  teacher: "X-CMP-Dev-Teacher",
  student: "X-CMP-Dev-Student",
};
const memory: Record<string, string> = {};

function read(storage: () => Storage, key: string): string {
  try {
    return storage().getItem(key) ?? memory[key] ?? "";
  } catch {
    return memory[key] ?? "";
  }
}

function write(storage: () => Storage, key: string, value: string): void {
  if (value) memory[key] = value;
  else delete memory[key];
  try {
    if (value) storage().setItem(key, value);
    else storage().removeItem(key);
  } catch {
    /* storage blocked: memory only */
  }
}

export const getIdentity = (role: Role) => read(() => localStorage, KEY[role]);
export const setIdentity = (role: Role, name: string) => write(() => localStorage, KEY[role], name);
export const getToken = (role: Role) => read(() => sessionStorage, TOKEN_KEY[role]);
export const setToken = (role: Role, token: string) =>
  write(() => sessionStorage, TOKEN_KEY[role], token);

/** Signed in at all (through the LMS or the dev stand-in). */
export const isSignedIn = (role: Role) => !!getToken(role) || !!getIdentity(role);

export function devHeaders(role: Role): Record<string, string> {
  const token = getToken(role);
  if (token) return { Authorization: `Bearer ${token}` };
  const name = getIdentity(role);
  return name ? { [HEADER[role]]: name } : {};
}
