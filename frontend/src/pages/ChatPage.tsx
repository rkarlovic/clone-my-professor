import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, studentApi, type ChatCourse, type Message } from "../api";
import { citationLabel, sourceId, withCitations } from "../citations";
import { DevSignIn } from "../components/DevSignIn";
import { isSignedIn } from "../identity";

// What the student sees while the clone works (the answer is shown only after its check).
export const STAGES: Record<string, string> = {
  followup: "Povezujem s prethodnim pitanjem…",
  scope: "Provjeravam pitanje…",
  search: "Tražim u materijalima kolegija…",
  answer: "Pišem odgovor…",
  check: "Provjeravam odgovor prema materijalima…",
};

export function ChatPage({ kbId }: { kbId: string }) {
  const [signedIn, setSignedIn] = useState(isSignedIn("student"));
  const [course, setCourse] = useState<ChatCourse | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState<{ question: string; stage: string } | null>(null);
  const [error, setError] = useState("");
  const [answered, setAnswered] = useState(0); // teacher answers since the last visit
  const end = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    if (!isSignedIn("student")) return;
    try {
      const c = await studentApi.course(kbId);
      setCourse(c);
      if (c.live) {
        setMessages(await studentApi.history(kbId));
        const notes = await studentApi.notifications(kbId).catch(() => []); // never blocks the chat
        const n = notes.filter((x) => x.kind === "answered").length;
        setAnswered(n);
        if (notes.length > 0) studentApi.readNotifications(kbId).catch(() => {});
      }
      setError("");
    } catch (e) {
      setError(e instanceof ApiError && e.status === 404 ? "Kolegij ne postoji." : (e as Error).message);
    }
  }, [kbId]);
  useEffect(() => {
    load();
  }, [load, signedIn]);
  useEffect(() => {
    // Braces matter: newer browsers return a Promise from scrollIntoView, and an effect must
    // not return anything but a cleanup function.
    end.current?.scrollIntoView?.({ block: "end" });
  }, [messages, pending]);

  async function ask(e: React.FormEvent) {
    e.preventDefault();
    const q = question.trim();
    if (!q || pending) return;
    setQuestion("");
    setError("");
    setPending({ question: q, stage: "" });
    try {
      await studentApi.ask(kbId, q, (ev) => {
        if (ev.event === "stage") {
          setPending({ question: q, stage: (ev.data as { stage: string }).stage });
        } else if (ev.event === "answer") {
          setMessages((m) => [...m, ev.data as Message]);
        } else if (ev.event === "error") {
          setError((ev.data as { detail: string }).detail);
          setQuestion(q);
        }
      });
    } catch (err) {
      setError((err as Error).message);
      setQuestion(q);
    } finally {
      setPending(null);
    }
  }

  async function rate(m: Message, helpful: boolean) {
    try {
      const updated = await studentApi.feedback(m.id, helpful);
      setMessages((all) => all.map((x) => (x.id === m.id ? { ...x, feedback: updated.feedback } : x)));
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <main className="chat">
      <header className="chat-head">
        <h1>{course?.subject_name ?? "Asistent kolegija"}</h1>
        {/* Rule 6 (EU AI Act): always visible, never collapsible. */}
        <p className="ai-notice" role="note">
          <span aria-hidden="true">🤖</span>{" "}
          {course?.ai_notice ??
            "Razgovarate s AI asistentom, ne s nastavnikom. Odgovara samo iz odobrenih materijala."}
        </p>
      </header>

      {!signedIn ? (
        <section className="page narrow">
          <DevSignIn role="student" onChange={(n) => setSignedIn(!!n)} />
        </section>
      ) : course && !course.live ? (
        <p className="page card">Nastavnik još nije objavio ovaj kolegij. Pokušajte kasnije.</p>
      ) : (
        <>
          {answered > 0 && (
            <p className="page card ok" role="status">
              Nastavnik je odgovorio na {answered === 1 ? "vaše pitanje" : `${answered} vaša pitanja`}:
              odgovor je ispod pitanja, označen „Odgovor nastavnika”.
            </p>
          )}
          <section className="messages" aria-live="polite">
            {messages.length === 0 && !pending && (
              <p className="muted center">
                Postavite pitanje o gradivu kolegija. Odgovor dolazi iz nastavnikovih materijala, s
                izvorima.
              </p>
            )}
            {messages.map((m) => (
              <Exchange key={m.id} message={m} onRate={(h) => rate(m, h)} />
            ))}
            {pending && (
              <div className="exchange">
                <p className="bubble student">{pending.question}</p>
                <p className="bubble ai working" role="status">
                  {STAGES[pending.stage] ?? "Šaljem pitanje…"}
                </p>
              </div>
            )}
            <div ref={end} />
          </section>
          {error && <p className="error page">{error}</p>}
          <form className="ask" onSubmit={ask}>
            <label className="sr-only" htmlFor="question">
              Vaše pitanje
            </label>
            <textarea
              id="question"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  e.currentTarget.form?.requestSubmit();
                }
              }}
              placeholder="Vaše pitanje…"
              rows={2}
              maxLength={2000}
              disabled={!!pending}
            />
            <button type="submit" disabled={!question.trim() || !!pending}>
              Pošalji
            </button>
          </form>
        </>
      )}
    </main>
  );
}

function Exchange({ message: m, onRate }: { message: Message; onRate: (helpful: boolean) => void }) {
  return (
    <div className="exchange">
      <p className="bubble student">{m.question}</p>
      <div className={`bubble ai${m.forwarded ? " forwarded" : ""}`}>
        <p className="who">AI asistent</p>
        <p className="answer">{withCitations(m.answer, m.citations, m.id)}</p>
        {m.citations.length > 0 && (
          <ol className="sources small">
            {m.citations.map((c) => (
              <li key={c.n} id={sourceId(m.id, c.n)} value={c.n}>
                {citationLabel(c)}
              </li>
            ))}
          </ol>
        )}
        {!m.forwarded && (
          <div className="rate" role="group" aria-label="Je li odgovor bio koristan?">
            <button
              type="button"
              aria-pressed={m.feedback === 1}
              aria-label="Koristan odgovor"
              onClick={() => onRate(true)}
            >
              👍
            </button>
            <button
              type="button"
              aria-pressed={m.feedback === -1}
              aria-label="Nije koristan odgovor"
              onClick={() => onRate(false)}
            >
              👎
            </button>
          </div>
        )}
      </div>
      {m.teacher_answer && (
        <div className="bubble teacher">
          <p className="who">Odgovor nastavnika</p>
          <p>{m.teacher_answer}</p>
        </div>
      )}
      {!m.forwarded && m.feedback === -1 && !m.teacher_answer && (
        <p className="small muted">Hvala. Pitanje i odgovor poslani su nastavniku da ih pogleda.</p>
      )}
      {m.forwarded && !m.teacher_answer && (
        <p className="small muted">Nastavnik još nije odgovorio.</p>
      )}
    </div>
  );
}
