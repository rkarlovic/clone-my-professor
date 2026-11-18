import { useEffect, useState } from "react";
import { teamApi } from "../api";
import { DevSignIn } from "../components/DevSignIn";
import { SsoButtons } from "../components/SsoButtons";
import { isSignedIn } from "../identity";
import { href } from "../router";

/** #/invite/<code>: a teacher joins a course they were invited to (signs in first if needed). */
export function InvitePage({ code }: { code: string }) {
  const [signedIn, setSignedIn] = useState(isSignedIn("teacher"));
  const [course, setCourse] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!signedIn) return;
    teamApi.inviteInfo(code).then(
      (i) => setCourse(i.subject_name),
      (e: Error) => setError(e.message),
    );
  }, [code, signedIn]);

  async function accept() {
    setError("");
    try {
      const out = await teamApi.accept(code);
      window.location.hash = href.course(out.kb_id);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <main className="page narrow">
      <h1>Poziv u kolegij</h1>
      {!signedIn ? (
        <section className="card">
          <p>Prijavite se kao nastavnik da biste prihvatili poziv.</p>
          <SsoButtons next={`invite/${code}`} />
          <DevSignIn role="teacher" onChange={(name) => setSignedIn(!!name)} />
        </section>
      ) : error ? (
        <p className="card error">{error}</p>
      ) : course === null ? (
        <p className="muted">Učitavam…</p>
      ) : (
        <section className="card">
          <p>
            Pozvani ste da zajedno s drugim nastavnicima vodite kolegij <strong>{course}</strong>:
            materijale, pitanja studenata i postavke asistenta.
          </p>
          <div className="row actions">
            <button onClick={accept}>Pridruži se</button>
          </div>
        </section>
      )}
    </main>
  );
}
