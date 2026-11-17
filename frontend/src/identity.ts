// Development sign-in only: the name typed on the start page is sent as X-CMP-Dev-Teacher /
// X-CMP-Dev-Student. The backend accepts it only with AUTH_MODE=dev; SSO (teachers) and LTI
// (students) replace it before the pilot. Kept in localStorage per browser, wrapped in
// try/catch because storage can be blocked (private windows, some LMS iframes).

export type Role = "teacher" | "student";

const KEY: Record<Role, string> = { teacher: "cmp.dev.teacher", student: "cmp.dev.student" };
const HEADER: Record<Role, string> = {
  teacher: "X-CMP-Dev-Teacher",
  student: "X-CMP-Dev-Student",
};
const memory: Partial<Record<Role, string>> = {};

export function getIdentity(role: Role): string {
  try {
    return localStorage.getItem(KEY[role]) ?? memory[role] ?? "";
  } catch {
    return memory[role] ?? "";
  }
}

export function setIdentity(role: Role, name: string): void {
  memory[role] = name;
  try {
    if (name) localStorage.setItem(KEY[role], name);
    else localStorage.removeItem(KEY[role]);
  } catch {
    /* storage blocked: memory only */
  }
}

export function devHeaders(role: Role): Record<string, string> {
  const name = getIdentity(role);
  return name ? { [HEADER[role]]: name } : {};
}
