import { render, screen } from "@testing-library/react";
import type { Version } from "../api";
import { VersionRow } from "./VersionRow";

const base: Version = {
  version: 2,
  status: "draft",
  ingest_status: "ready",
  ingest_error: null,
  created_at: "2026-11-17T20:00:00Z",
  pending_files: ["9 Osnovne potrebe.pptx"],
  approved_at: null,
  activated_at: null,
  check_status: null,
  check: null,
  summary: {
    documents: 1,
    pages: 32,
    chunks_embedded: 24,
    carried_over: ["7.pptx"],
    sections: {},
    seconds: 6,
  },
  documents: null,
};

function buttons(v: Partial<Version>): string[] {
  render(
    <ul>
      <VersionRow kbId="kb" version={{ ...base, ...v }} onChanged={() => {}} />
    </ul>,
  );
  return screen.queryAllByRole("button").map((b) => b.textContent ?? "");
}

test("a processed draft can be reviewed, approved or discarded", () => {
  expect(buttons({})).toEqual(["Pregledaj", "Odobri", "Odbaci"]);
});

test("an approved version can be published", () => {
  expect(buttons({ status: "approved" })).toEqual(["Pregledaj", "Objavi studentima"]);
});

test("the active version offers no step", () => {
  expect(buttons({ status: "active" })).toEqual(["Pregledaj"]);
});

test("a version still processing cannot be approved", () => {
  expect(buttons({ ingest_status: "processing", summary: null })).toEqual([]);
});

test("a failed version shows the reason and a retry", () => {
  const error = "a.pdf: the file could not be read";
  expect(buttons({ ingest_status: "failed", ingest_error: error, summary: null })).toEqual([
    "Pokušaj ponovno",
  ]);
  expect(screen.getByText(/could not be read/)).toBeInTheDocument();
});

test("a finished check says how often search finds the right slide", () => {
  const check = { questions: 20, found_top5: 15, recall_at_5: 0.75, written: 20, misses: [], seconds: 9 };
  buttons({ check_status: "done", check });
  expect(screen.getByText(/za 15 od 20 provjernih pitanja \(75 %\)/)).toBeInTheDocument();
});

test("a running check is announced", () => {
  buttons({ check_status: "running" });
  expect(screen.getByText(/Provjera asistenta u tijeku/)).toBeInTheDocument();
});

test("the newest version offers removing a file; a removal says what it removed", async () => {
  const { default: userEvent } = await import("@testing-library/user-event");
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(
        JSON.stringify({ ...base, documents: [{ file_name: "a.pdf", file_hash: "1" }, { file_name: "b.pdf", file_hash: "2" }] }),
      ),
    ),
  );
  render(
    <ul>
      <VersionRow
        kbId="kb"
        version={{ ...base, is_base: true, removed_files: ["c.pdf"], auto_activate: true }}
        onChanged={() => {}}
      />
    </ul>,
  );
  expect(screen.getByText(/više nije u sustavu za učenje/)).toHaveTextContent("c.pdf");
  await userEvent.click(screen.getByRole("button", { name: "Pregledaj" }));
  expect(await screen.findAllByRole("button", { name: "Ukloni" })).toHaveLength(2);
  vi.unstubAllGlobals();
});
