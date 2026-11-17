import type { Notice } from "../api";
import { noticeText } from "./Notifications";

const n = (kind: string, data: Record<string, unknown>): Notice => ({
  id: "1", kb_id: "k", course: "C", kind, data, created_at: "2026-11-18T10:00:00Z", read: false,
});

test("notices are worded for the teacher", () => {
  expect(noticeText(n("draft_ready", { version: 3, origin: "lms" }))).toBe(
    "Verzija 3 je obrađena i čeka vaš pregled (iz sustava za učenje).",
  );
  expect(noticeText(n("lms_removed", { version: 4, files: ["a.pdf", "b.pptx"] }))).toContain("a.pdf, b.pptx");
  expect(noticeText(n("question", { question: "Tko je Vigotski?" }))).toBe("Novo pitanje studenta: „Tko je Vigotski?”");
  expect(noticeText(n("persona_rejected", { version: 2 }))).toContain("nisu prošle provjeru");
});
