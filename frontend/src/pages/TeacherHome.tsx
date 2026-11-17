import { useCallback, useEffect, useState } from "react";
import { ApiError, teacherApi, type Course, type Notice } from "../api";
import { DevSignIn } from "../components/DevSignIn";
import { TeacherNotifications } from "../components/Notifications";
import { isSignedIn } from "../identity";
import { href } from "../router";

export function TeacherHome() {
  const [signedIn, setSignedIn] = useState(isSignedIn("teacher"));
  const [courses, setCourses] = useState<Course[] | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [unread, setUnread] = useState<Notice[]>([]);

  const load = useCallback(() => {
    if (!isSignedIn("teacher")) return;
    teacherApi
      .courses()
      .then(setCourses)
      .catch((e: ApiError) => setError(e.message));
  }, []);
  useEffect(load, [load, signedIn]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const course = await teacherApi.createCourse(name.trim());
      window.location.hash = href.course(course.id);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <main className="page">
      <nav className="crumbs">
        <a href={href.home}>Početna</a> › Moji kolegiji
      </nav>
      <h1>Moji kolegiji</h1>
      <DevSignIn role="teacher" onChange={(n) => setSignedIn(!!n)} />
      {!signedIn ? (
        <p>Prijavite se kako biste vidjeli svoje kolegije.</p>
      ) : (
        <>
          {error && <p className="error">{error}</p>}
          <TeacherNotifications onChange={setUnread} />
          {courses === null ? (
            <p className="muted">Učitavam…</p>
          ) : courses.length === 0 ? (
            <p>Još nemate kolegija. Napravite prvi:</p>
          ) : (
            <ul className="list">
              {courses.map((c) => (
                <li key={c.id}>
                  <a href={href.course(c.id)}>{c.subject_name}</a>{" "}
                  {c.active_version ? (
                    <span className="badge ok">objavljena verzija {c.active_version}</span>
                  ) : (
                    <span className="badge">nije objavljen</span>
                  )}
                  {unread.some((n) => n.kb_id === c.id) && (
                    <span className="badge bad">
                      {unread.filter((n) => n.kb_id === c.id).length} nove obavijesti
                    </span>
                  )}
                  {(c.teachers ?? 1) > 1 && (
                    <span className="badge">{c.teachers} nastavnika</span>
                  )}
                </li>
              ))}
            </ul>
          )}
          <form className="row" onSubmit={create}>
            <label className="grow">
              Novi kolegij
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="naziv kolegija"
                maxLength={300}
                required
              />
            </label>
            <button type="submit" disabled={!name.trim()}>
              Napravi
            </button>
          </form>
        </>
      )}
    </main>
  );
}
