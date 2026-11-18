import { useCallback, useEffect, useState } from "react";
import { teacherApi, type QueueItem } from "../api";

type Filter = "open" | "answered" | "dismissed" | "all";
const FILTERS: Record<Filter, string> = {
  open: "Otvorena",
  answered: "Odgovorena",
  dismissed: "Odbačena",
  all: "Sva",
};

/** Forwarded questions (rule 2). The API sends no student identity, so none is shown. */
export function QuestionQueue({ kbId, files }: { kbId: string; files: string[] }) {
  const [filter, setFilter] = useState<Filter>("open");
  const [items, setItems] = useState<QueueItem[] | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    teacherApi
      .questions(kbId, filter)
      .then((x) => {
        setItems(x);
        setError("");
      })
      .catch((e: Error) => setError(e.message));
  }, [kbId, filter]);
  useEffect(load, [load]);

  return (
    <section>
      <div className="row">
        <h2 className="grow">Pitanja koja je asistent proslijedio</h2>
        <label>
          Prikaži{" "}
          <select value={filter} onChange={(e) => setFilter(e.target.value as Filter)}>
            {Object.entries(FILTERS).map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className="muted small">
        Vaš odgovor student vidi ispod svog pitanja. Ako navedete izvor (datoteku i slajd), pitanje
        kasnije služi i za provjeru asistenta.
      </p>
      {error && <p className="error">{error}</p>}
      {items === null ? (
        <p className="muted">Učitavam…</p>
      ) : items.length === 0 ? (
        <p>Nema pitanja.</p>
      ) : (
        <ul className="queue">
          {items.map((q) => (
            <QueueRow key={q.id} kbId={kbId} item={q} files={files} onChanged={load} />
          ))}
        </ul>
      )}
    </section>
  );
}

function QueueRow({
  kbId,
  item,
  files,
  onChanged,
}: {
  kbId: string;
  item: QueueItem;
  files: string[];
  onChanged: () => void;
}) {
  const [answer, setAnswer] = useState(item.answer ?? "");
  const [file, setFile] = useState(item.answer_sources[0]?.file_name ?? "");
  const [page, setPage] = useState(item.answer_sources[0]?.page?.toString() ?? "");
  const [editing, setEditing] = useState(item.status === "open");
  const [error, setError] = useState("");

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const sources = file ? [{ file_name: file, page: page ? Number(page) : null }] : [];
      await teacherApi.answer(kbId, item.id, answer, sources);
      setEditing(false);
      onChanged();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function dismiss() {
    try {
      await teacherApi.dismiss(kbId, item.id);
      onChanged();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <li className={`card queue-item ${item.status}`}>
      <p className="question">„{item.question}”</p>
      <p className="small muted">
        {new Date(item.created_at).toLocaleString("hr-HR")} · razlog: {item.reason_text}
      </p>
      {item.ai_answer && (
        <blockquote className="small muted">Asistent je odgovorio: {item.ai_answer}</blockquote>
      )}
      {!editing && item.answer && (
        <div className="teacher-answer">
          <p>{item.answer}</p>
          {item.answer_sources.length > 0 && (
            <p className="small muted">
              Izvor:{" "}
              {item.answer_sources
                .map((s) => (s.page ? `${s.file_name}, str. ${s.page}` : s.file_name))
                .join("; ")}
            </p>
          )}
          <button type="button" className="link" onClick={() => setEditing(true)}>
            Uredi odgovor
          </button>
        </div>
      )}
      {editing && (
        <form onSubmit={save}>
          <label>
            Odgovor
            <textarea
              value={answer}
              onChange={(e) => setAnswer(e.target.value)}
              rows={3}
              required
            />
          </label>
          <div className="row">
            <label className="grow">
              Izvor (neobavezno)
              <select value={file} onChange={(e) => setFile(e.target.value)}>
                <option value="">— bez izvora —</option>
                {files.map((f) => (
                  <option key={f} value={f}>
                    {f}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Str./slajd
              <input
                type="number"
                min={1}
                value={page}
                onChange={(e) => setPage(e.target.value)}
                disabled={!file}
                className="page-input"
              />
            </label>
          </div>
          <div className="row actions">
            <button type="submit" disabled={!answer.trim()}>
              Spremi odgovor
            </button>
            {item.status === "open" && (
              <button type="button" className="secondary" onClick={dismiss}>
                Odbaci pitanje
              </button>
            )}
          </div>
        </form>
      )}
      {error && <p className="error">{error}</p>}
    </li>
  );
}
