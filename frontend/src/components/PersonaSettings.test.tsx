import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { setIdentity } from "../identity";
import { PersonaPanel } from "./PersonaSettings";

const active = {
  version: 1,
  status: "active",
  settings: {
    address: "vi",
    answer_length: "medium",
    exercise_help: "hints",
    examples: true,
    terminology: "",
    forward_topics: ["projekt"],
  },
  free_text: "",
  prompt_text: "- Studentu se obraćaj s „Vi”.",
  created_at: "2026-11-17T20:00:00Z",
  activated_at: "2026-11-17T20:10:00Z",
  check_report: {
    passed: true,
    reasons: [],
    check_questions: 10,
    answered: 9,
    generic: 12,
    forwarded: 12,
    items: [],
  },
};

function fakeServer() {
  const sent: { method: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      if (init.method === "PUT") {
        sent.push({ method: "PUT", body: JSON.parse(String(init.body)) });
        return new Response(JSON.stringify({ ...active, version: 2, status: "draft" }));
      }
      if (url.endsWith("/persona")) return new Response(JSON.stringify([active]));
      return new Response("{}", { status: 404 });
    }),
  );
  return sent;
}

beforeEach(() => setIdentity("teacher", "t1"));
afterEach(() => vi.unstubAllGlobals());

test("the form starts from the active persona and saves topics one per line", async () => {
  const sent = fakeServer();
  render(<PersonaPanel kbId="kb1" live={true} />);
  expect(await screen.findByText("Aktivne su postavke verzije 1.")).toBeInTheDocument();
  expect(screen.getByText(/proslijeđeno 12 od 12/)).toBeInTheDocument();
  const topics = screen.getByLabelText(/prosljeđuje vama/);
  expect(topics).toHaveValue("projekt");
  await userEvent.click(screen.getByLabelText("„ti”"));
  await userEvent.type(topics, "\n  ocjenjivanje seminara  \n");
  await userEvent.click(screen.getByRole("button", { name: "Spremi nacrt" }));
  expect(await screen.findByText(/Spremljeno kao nacrt/)).toBeInTheDocument();
  expect(sent[0].body).toMatchObject({
    settings: { address: "ti", forward_topics: ["projekt", "ocjenjivanje seminara"] },
  });
});

test("activation needs a draft and published materials", async () => {
  fakeServer();
  render(<PersonaPanel kbId="kb1" live={false} />);
  await screen.findByText("Aktivne su postavke verzije 1.");
  expect(screen.getByRole("button", { name: "Aktiviraj" })).toBeDisabled();
  expect(screen.getByText(/kad objavite materijale/)).toBeInTheDocument();
});
