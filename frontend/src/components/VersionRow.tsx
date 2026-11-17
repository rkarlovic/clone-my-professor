import { useState } from "react";
import { teacherApi, type Version } from "../api";

export const STATUS: Record<Version["status"], string> = {
  draft: "nacrt",
  approved: "odobreno",
  active: "objavljeno studentima",
};
export const INGEST: Record<Version["ingest_status"], string> = {
  queued: "čeka obradu",
  processing: "obrađuje se…",
  ready: "obrađeno",
  failed: "greška pri obradi",
};

function when(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString("hr-HR") : "";
}

export function VersionRow({
  kbId,
  version: v,
  onChanged,
}: {
  kbId: string;
  version: Version;
  onChanged: () => void;
}) {
  const [detail, setDetail] = useState<Version | null>(null);
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);

  async function run(action: () => Promise<unknown>, confirmText?: string) {
    if (confirmText && !window.confirm(confirmText)) return;
    setWorking(true);
    setError("");
    try {
      await action();
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setWorking(false);
    }
  }

  async function toggleDetail() {
    if (detail) return setDetail(null);
    try {
      setDetail(await teacherApi.version(kbId, v.version));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const ready = v.ingest_status === "ready";
  return (
    <li className={`version ${v.status}`}>
      <div className="row">
        <strong>Verzija {v.version}</strong>
        <span className={`badge ${v.status === "active" ? "ok" : ""}`}>{STATUS[v.status]}</span>
        <span className={`badge ${v.ingest_status === "failed" ? "bad" : ""}`}>
          {INGEST[v.ingest_status]}
        </span>
        <span className="muted small">{when(v.created_at)}</span>
      </div>
      {v.pending_files.length > 0 && (
        <p className="small">
          {v.origin === "lms" ? "Iz sustava za učenje, novo ili promijenjeno: " : "Novo u ovoj verziji: "}
          {v.pending_files.join(", ")}
        </p>
      )}
      {(v.removed_files ?? []).length > 0 && (
        <p className="small">
          {v.auto_activate
            ? "Uklonjeno jer više nije u sustavu za učenje (objavljeno automatski): "
            : "Uklonjeno u ovoj verziji: "}
          {v.removed_files!.join(", ")}
        </p>
      )}
      {v.ingest_error && <p className="error">{v.ingest_error}</p>}
      {v.summary && (
        <p className="small muted">
          {v.summary.documents} obrađeno, {v.summary.carried_over.length} preneseno iz prethodne
          verzije · {v.summary.pages} stranica/slajdova · {v.summary.chunks_embedded} odlomaka ·{" "}
          {Math.round(v.summary.seconds)} s
        </p>
      )}
      {ready && <CheckLine version={v} />}
      <div className="row actions">
        {ready && (
          <button type="button" className="link" onClick={toggleDetail}>
            {detail ? "Sakrij pregled" : "Pregledaj"}
          </button>
        )}
        {ready && v.status === "draft" && (
          <>
            <button
              type="button"
              disabled={working}
              onClick={() => run(() => teacherApi.step(kbId, v.version, "approve"))}
            >
              Odobri
            </button>
            <button
              type="button"
              className="secondary"
              disabled={working}
              onClick={() =>
                run(
                  () => teacherApi.discard(kbId, v.version),
                  `Odbaciti verziju ${v.version}? Ovo se ne može poništiti.`,
                )
              }
            >
              Odbaci
            </button>
          </>
        )}
        {v.status === "approved" && (
          <button
            type="button"
            disabled={working}
            onClick={() =>
              run(
                () => teacherApi.step(kbId, v.version, "activate"),
                `Objaviti verziju ${v.version}? Studenti će od sada dobivati odgovore iz nje.`,
              )
            }
          >
            Objavi studentima
          </button>
        )}
        {v.ingest_status === "failed" && (
          <button
            type="button"
            disabled={working}
            onClick={() => run(() => teacherApi.step(kbId, v.version, "retry"))}
          >
            Pokušaj ponovno
          </button>
        )}
      </div>
      {error && <p className="error">{error}</p>}
      {detail && (
        <div className="detail">
          <h3>Datoteke</h3>
          <ul className="small">
            {(detail.documents ?? []).map((d) => (
              <li key={d.file_name}>
                {d.file_name}
                {detail.summary?.carried_over.includes(d.file_name) && (
                  <span className="muted"> (preneseno)</span>
                )}
                {v.is_base && (detail.documents ?? []).length > 1 && (
                  <>
                    {" "}
                    <button
                      type="button"
                      className="link"
                      disabled={working}
                      onClick={() =>
                        run(
                          () => teacherApi.remove(kbId, [d.file_name]),
                          `Napraviti novu verziju bez datoteke „${d.file_name}”? Studenti je i dalje imaju dok novu verziju ne odobrite i objavite.`,
                        )
                      }
                    >
                      Ukloni
                    </button>
                  </>
                )}
              </li>
            ))}
          </ul>
          {detail.summary && Object.keys(detail.summary.sections).length > 0 && (
            <>
              <h3>Cjeline (novo obrađene)</h3>
              <ul className="small">
                {Object.entries(detail.summary.sections).map(([label, n]) => (
                  <li key={label}>
                    {label.split(" › ").slice(1).join(" › ") || label}: {n} odlomaka
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </li>
  );
}

/** The background check: how often search finds the slide a check question was written from. */
export function CheckLine({ version: v }: { version: Version }) {
  if (v.check_status === "queued" || v.check_status === "running")
    return <p className="small muted">Provjera asistenta u tijeku…</p>;
  if (v.check_status === "failed")
    return (
      <p className="small error">Provjera asistenta nije uspjela; materijali su obrađeni normalno.</p>
    );
  const c = v.check;
  if (v.check_status !== "done" || !c || c.questions === 0) return null;
  const pct = Math.round((100 * c.found_top5) / c.questions);
  return (
    <p className={`small check-line ${pct >= 80 ? "good" : "weak"}`}>
      Provjera: asistent pronalazi pravi slajd za {c.found_top5} od {c.questions} provjernih
      pitanja ({pct} %).
      {pct < 80 && " Pitanja koja promašuje označena su u kartici „Provjerna pitanja”."}
    </p>
  );
}
