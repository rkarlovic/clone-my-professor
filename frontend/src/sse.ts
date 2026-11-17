// Server-sent events from a fetch() body. The browser's EventSource cannot POST, and the chat
// sends the question in a POST, so the stream is read and split here.

export interface SseEvent {
  event: string;
  data: unknown;
}

/** Split complete events off `buffer`; `rest` is an incomplete tail to keep for the next
 * chunk. Comment lines (": keep-alive") are skipped. */
export function parseSse(buffer: string): { events: SseEvent[]; rest: string } {
  const normalized = buffer.replace(/\r\n/g, "\n");
  const blocks = normalized.split("\n\n");
  const rest = blocks.pop() ?? "";
  const events: SseEvent[] = [];
  for (const block of blocks) {
    let event = "message";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith(":")) continue;
      const colon = line.indexOf(":");
      const field = colon === -1 ? line : line.slice(0, colon);
      const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
      if (field === "event") event = value;
      else if (field === "data") data.push(value);
    }
    if (data.length === 0) continue;
    const text = data.join("\n");
    let parsed: unknown = text;
    try {
      parsed = JSON.parse(text);
    } catch {
      /* plain text data */
    }
    events.push({ event, data: parsed });
  }
  return { events, rest };
}
