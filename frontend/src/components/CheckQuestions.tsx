import { useCallback, useEffect, useState } from "react";
import { teacherApi, type CheckQuestion, type CheckReport } from "../api";

const STYLE: Record<string, string> = {
  casual: "neformalno",
  symptom: "opis problema",
  english: "engleski",
  paraphrase: "svojim riječima",
  no_diacritics: "bez dijakritika",
};

/** The course's auto-generated check questions. Doing nothing here is fine; a teacher may
 * delete questions that make no sense (they are not written again for the same file). */
export function CheckQuestions({ kbId, latest }: { kbId: string; latest: CheckReport | null }) {
  const [items, setItems] = useState<CheckQuestion[] | null>(null);
  const [error, setError] = useState("");
  const missed = new Set((latest?.misses ?? []).map((m) => m.id));

  const load = useCallback(() => {
    teacherApi
      .checks(kbId)
      .then(setItems)
      .catch((e: Error) => setError(e.message));
  }, [kbId]);
  useEffect(load, [load]);

  async function remove(id: string) {
    try {
      await teacherApi.deleteCheck(kbId, id);
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <section>
      <h2>Provjerna pitanja</h2>
      <p className="muted small">
        Sustav ih piše sam iz vaših slajdova, onako kako bi ih student mogao pitati, i nakon
        svakog učitavanja provjerava pronalazi li asistent slajd iz kojeg je pitanje nastalo. Ne
        morate ništa raditi; pitanje koje nema smisla možete obrisati.
      </p>
      {error && <p className="error">{error}</p>}
      {items === null ? (
        <p className="muted">Učitavam…</p>
      ) : items.length === 0 ? (
        <p>Još nema provjernih pitanja: nastaju nakon obrade materijala.</p>
      ) : (
        <ul className="checks">
          {items.map((q) => (
            <li key={q.id} className={missed.has(q.id) ? "missed" : ""}>
              <span className="grow">
                „{q.question}”{" "}
                <span className="small muted">
                  · {q.file_name}, str. {q.page} · {STYLE[q.style] ?? q.style}
                  {missed.has(q.id) && " · asistent nije našao slajd"}
                </span>
              </span>
              <button type="button" className="link" onClick={() => remove(q.id)}>
                Obriši
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
