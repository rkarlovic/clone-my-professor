import { useEffect, useState } from "react";
import { ltiApi, type LinkInfo } from "../api";
import { setToken } from "../identity";
import { href } from "../router";

/** Where an LTI launch or a Microsoft/Google sign-in lands: #/lti?next=…&token=…. The token is
 * kept for this tab and removed from the address bar at once; then the user goes on to the
 * chat, their courses, a course, an invite, or linking. */
export function LtiPage({ query }: { query: URLSearchParams }) {
  const next = query.get("next") ?? "";
  const token = query.get("token");
  const [stored, setStored] = useState(false);

  useEffect(() => {
    if (token) {
      setToken(next.startsWith("chat/") ? "student" : "teacher", token);
      window.history.replaceState(null, "", `${window.location.pathname}#/lti?next=${next}`);
    }
    setStored(true);
    if (next.startsWith("chat/")) window.location.hash = href.chat(next.slice(5));
    else if (next.startsWith("teacher/")) window.location.hash = href.course(next.slice(8));
    else if (next === "teacher") window.location.hash = href.teacher;
    else if (next.startsWith("invite/")) window.location.hash = href.invite(next.slice(7));
  }, [next, token]);

  if (!stored) return null;
  if (next === "link") return <LinkCourse />;
  if (next === "not-linked")
    return (
      <main className="page narrow">
        <h1>Asistent još nije spreman</h1>
        <p>Nastavnik ovaj kolegij još nije povezao s asistentom. Pokušajte kasnije.</p>
      </main>
    );
  return <p className="page muted">Otvaram…</p>;
}

/** A teacher's first launch from an LMS course: link it to a CMP course (existing or new). */
function LinkCourse() {
  const [info, setInfo] = useState<LinkInfo | null>(null);
  const [choice, setChoice] = useState("new");
  const [name, setName] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    ltiApi
      .linkInfo()
      .then((i) => {
        setInfo(i);
        setName(i.context_title ?? "");
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  async function link(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const out = await ltiApi.link(choice === "new" ? { new_course_name: name.trim() } : { kb_id: choice });
      setToken("teacher", out.token);
      window.location.hash = href.course(out.kb_id);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <main className="page narrow">
      <h1>Povežite kolegij</h1>
      {info === null ? (
        <p className="muted">{error || "Učitavam…"}</p>
      ) : (
        <form className="card" onSubmit={link}>
          <p>
            Kolegij u sustavu za učenje: <strong>{info.context_title ?? "(bez naziva)"}</strong>
          </p>
          <p className="muted small">
            Povežite ga s kolegijem u asistentu. Studenti ovog kolegija tada dobivaju njegov chat.
            Više kolegija iz sustava za učenje može koristiti isti kolegij asistenta.
          </p>
          {info.courses.map((c) => (
            <label key={c.id} className="inline">
              <input
                type="radio"
                name="kb"
                checked={choice === c.id}
                onChange={() => setChoice(c.id)}
              />
              {c.subject_name}
            </label>
          ))}
          <label className="inline">
            <input type="radio" name="kb" checked={choice === "new"} onChange={() => setChoice("new")} />
            Novi kolegij:
          </label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={choice !== "new"}
            maxLength={300}
            aria-label="Naziv novog kolegija"
          />
          <div className="row actions">
            <button type="submit" disabled={choice === "new" && !name.trim()}>
              Poveži
            </button>
          </div>
          {error && <p className="error">{error}</p>}
        </form>
      )}
    </main>
  );
}
