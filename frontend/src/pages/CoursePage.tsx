import { useCallback, useEffect, useState } from "react";
import { teacherApi, type Course, type Version } from "../api";
import { CheckQuestions } from "../components/CheckQuestions";
import { PersonaPanel } from "../components/PersonaSettings";
import { QuestionQueue } from "../components/QuestionQueue";
import { UploadBox } from "../components/UploadBox";
import { VersionRow } from "../components/VersionRow";
import { href } from "../router";

const POLL_MS = 3000;

export function CoursePage({ kbId }: { kbId: string }) {
  const [course, setCourse] = useState<Course | null>(null);
  const [versions, setVersions] = useState<Version[]>([]);
  const [files, setFiles] = useState<string[]>([]); // documents of the active version
  const [tab, setTab] = useState<"materials" | "questions" | "checks" | "persona">("materials");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const [all, vs] = await Promise.all([teacherApi.courses(), teacherApi.versions(kbId)]);
      const c = all.find((x) => x.id === kbId) ?? null;
      setCourse(c);
      setVersions(vs);
      if (c?.active_version) {
        const active = await teacherApi.version(kbId, c.active_version);
        setFiles((active.documents ?? []).map((d) => d.file_name));
      } else setFiles([]);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }, [kbId]);

  useEffect(() => {
    load();
  }, [load]);

  // While an upload is being processed, refresh until it is ready or failed.
  const busy = versions.some((v) => v.ingest_status === "queued" || v.ingest_status === "processing");
  const checking = versions.some((v) => v.check_status === "queued" || v.check_status === "running");
  useEffect(() => {
    if (!busy && !checking) return;
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [busy, checking, load]);
  const latestCheck = versions.find((v) => v.check_status === "done")?.check ?? null;

  const chatLink = `${window.location.origin}${window.location.pathname}${href.chat(kbId)}`;

  return (
    <main className="page">
      <nav className="crumbs">
        <a href={href.home}>Početna</a> › <a href={href.teacher}>Moji kolegiji</a> ›{" "}
        {course?.subject_name ?? "…"}
      </nav>
      <h1>{course?.subject_name ?? "Kolegij"}</h1>
      {error && <p className="error">{error}</p>}
      {course?.active_version ? (
        <p className="card ok">
          Studenti dobivaju odgovore iz verzije {course.active_version}. Poveznica za studente:{" "}
          <a href={href.chat(kbId)}>{chatLink}</a>
        </p>
      ) : (
        <p className="card">
          Kolegij još nije objavljen: učitajte materijale, pregledajte ih, odobrite i objavite.
        </p>
      )}

      <div className="tabs" role="tablist">
        <button
          role="tab"
          aria-selected={tab === "materials"}
          onClick={() => setTab("materials")}
        >
          Materijali
        </button>
        <button
          role="tab"
          aria-selected={tab === "questions"}
          onClick={() => setTab("questions")}
        >
          Pitanja studenata
        </button>
        <button role="tab" aria-selected={tab === "checks"} onClick={() => setTab("checks")}>
          Provjerna pitanja
        </button>
        <button role="tab" aria-selected={tab === "persona"} onClick={() => setTab("persona")}>
          Postavke asistenta
        </button>
      </div>

      {tab === "materials" ? (
        <>
          <UploadBox kbId={kbId} disabled={busy} onUploaded={load} />
          <h2>Verzije</h2>
          {versions.length === 0 ? (
            <p className="muted">Još nema učitanih materijala.</p>
          ) : (
            <ul className="versions">
              {versions.map((v) => (
                <VersionRow key={v.version} kbId={kbId} version={v} onChanged={load} />
              ))}
            </ul>
          )}
        </>
      ) : tab === "questions" ? (
        <QuestionQueue kbId={kbId} files={files} />
      ) : tab === "checks" ? (
        <CheckQuestions kbId={kbId} latest={latestCheck} />
      ) : (
        <PersonaPanel kbId={kbId} live={!!course?.active_version} />
      )}
    </main>
  );
}
