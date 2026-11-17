import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { setIdentity } from "../identity";
import { ChatPage } from "./ChatPage";

const NOTICE = "Razgovarate s AI asistentom, ne s nastavnikom.";
const answer = {
  id: "m1",
  question: "Zašto kontinuirano?",
  answer: "Jer se darovitost iskazuje u različito vrijeme [1].",
  citations: [{ n: 1, file_name: "7 Identifikacija.pptx", pages: [16] }],
  forwarded: false,
  feedback: null,
  created_at: "2026-11-17T20:00:00Z",
  teacher_answer: null,
  teacher_answered_at: null,
};
const forwarded = {
  ...answer,
  id: "m0",
  question: "Tko je Vigotski?",
  answer: "U materijalima kolegija ne nalazim odgovor. Pitanje je proslijeđeno nastavniku.",
  citations: [],
  forwarded: true,
  teacher_answer: "Ruski psiholog; vidi priručnik.",
};

function sseBody(chunks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  return new ReadableStream({
    start(c) {
      chunks.forEach((x) => c.enqueue(enc.encode(x)));
      c.close();
    },
  });
}

function fakeServer(live = true) {
  const calls: { url: string; method: string; headers: Headers }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      calls.push({ url, method: init.method ?? "GET", headers: new Headers(init.headers) });
      const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
      if (url === "/api/chat/kb1")
        return json({ kb_id: "kb1", subject_name: "Rad s darovitom djecom", live, ai_notice: NOTICE });
      if (url === "/api/chat/kb1/messages" && init.method === "POST")
        return new Response(
          sseBody([
            `event: notice\ndata: {"ai_notice": "${NOTICE}"}\n\n`,
            'event: stage\ndata: {"stage": "search"}\n\n: keep-alive\n\n',
            `event: answer\ndata: ${JSON.stringify(answer)}\n\n`,
          ]),
          { status: 200, headers: { "Content-Type": "text/event-stream" } },
        );
      if (url === "/api/chat/kb1/messages") return json([forwarded]);
      return new Response("{}", { status: 404 });
    }),
  );
  return calls;
}

beforeEach(() => setIdentity("student", "s1"));
afterEach(() => vi.unstubAllGlobals());

test("the AI notice is always shown and a teacher answer is labelled as the teacher's", async () => {
  fakeServer();
  render(<ChatPage kbId="kb1" />);
  expect(await screen.findByRole("note")).toHaveTextContent(NOTICE);
  expect(await screen.findByText("Odgovor nastavnika")).toBeInTheDocument();
  expect(screen.getByText("Ruski psiholog; vidi priručnik.")).toBeInTheDocument();
});

test("asking shows the answer with its source, sent with the dev student header", async () => {
  const calls = fakeServer();
  render(<ChatPage kbId="kb1" />);
  await screen.findByText("Tko je Vigotski?");
  await userEvent.type(screen.getByLabelText("Vaše pitanje"), "Zašto kontinuirano?");
  await userEvent.click(screen.getByRole("button", { name: "Pošalji" }));
  const sourceItem = await screen.findByText("7 Identifikacija.pptx, str. 16");
  const exchange = sourceItem.closest(".exchange") as HTMLElement;
  expect(within(exchange).getByRole("button", { name: "izvor 1" })).toBeInTheDocument();
  expect(within(exchange).getByRole("button", { name: "Koristan odgovor" })).toBeInTheDocument();
  const post = calls.find((c) => c.url === "/api/chat/kb1/messages" && c.method === "POST");
  expect(post?.headers.get("X-CMP-Dev-Student")).toBe("s1");
});

test("an unpublished course says so and offers no question box", async () => {
  fakeServer(false);
  render(<ChatPage kbId="kb1" />);
  expect(await screen.findByText(/još nije objavio/)).toBeInTheDocument();
  expect(screen.queryByLabelText("Vaše pitanje")).not.toBeInTheDocument();
  expect(screen.getByRole("note")).toHaveTextContent(NOTICE);
});
