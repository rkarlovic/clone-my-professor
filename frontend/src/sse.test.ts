import { parseSse } from "./sse";

test("splits complete events and keeps an incomplete tail", () => {
  const { events, rest } = parseSse(
    'event: notice\ndata: {"ai_notice": "AI"}\n\n: keep-alive\n\nevent: stage\ndata: {"stage": "sea',
  );
  expect(events).toEqual([{ event: "notice", data: { ai_notice: "AI" } }]);
  expect(rest).toBe('event: stage\ndata: {"stage": "sea');
  const next = parseSse(rest + 'rch"}\n\n');
  expect(next.events).toEqual([{ event: "stage", data: { stage: "search" } }]);
  expect(next.rest).toBe("");
});

test("handles CRLF line endings and plain-text data", () => {
  const { events } = parseSse("event: x\r\ndata: hello\r\n\r\n");
  expect(events).toEqual([{ event: "x", data: "hello" }]);
});
