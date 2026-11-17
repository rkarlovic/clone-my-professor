import { useCallback, useEffect, useState } from "react";
import { personaApi, type Citation, type Persona, type PersonaSettings } from "../api";
import { citationLabel, withCitations } from "../citations";
import { STAGES } from "../pages/ChatPage";

const DEFAULTS: PersonaSettings = {
  address: "vi",
  answer_length: "medium",
  exercise_help: "hints",
  examples: true,
  terminology: "",
  forward_topics: [],
};

const STATUS: Record<Persona["status"], string> = {
  draft: "nacrt",
  checking: "provjerava se…",
  active: "aktivno",
  rejected: "odbijeno na provjeri",
  retired: "zamijenjeno",
};

const POLL_MS = 5000;

/** The teacher's settings for how the assistant talks. Platform rules always win: these change
 * tone, length and the kind of help, never what the assistant answers from or that it cites. */
export function PersonaPanel({ kbId, live }: { kbId: string; live: boolean }) {
  const [versions, setVersions] = useState<Persona[] | null>(null);
  const [settings, setSettings] = useState<PersonaSettings>(DEFAULTS);
  const [freeText, setFreeText] = useState("");
  const [topics, setTopics] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const vs = await personaApi.list(kbId);
      setVersions(vs);
      return vs;
    } catch (e) {
      setError((e as Error).message);
      return [];
    }
  }, [kbId]);

  // Start the form from the open draft, else the active persona, else the defaults.
  useEffect(() => {
    load().then((vs) => {
      const base = vs.find((v) => v.status === "draft") ?? vs.find((v) => v.status === "active");
      if (base) {
        setSettings(base.settings);
        setFreeText(base.free_text);
        setTopics(base.settings.forward_topics.join("\n"));
      }
    });
  }, [load]);

  const checking = versions?.some((v) => v.status === "checking") ?? false;
  useEffect(() => {
    if (!checking) return;
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [checking, load]);

  const draft = versions?.find((v) => v.status === "draft") ?? null;
  const active = versions?.find((v) => v.status === "active") ?? null;

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setMessage("");
    try {
      const forward_topics = topics
        .split("\n")
        .map((t) => t.trim())
        .filter(Boolean);
      await personaApi.save(kbId, { ...settings, forward_topics }, freeText);
      setMessage("Spremljeno kao nacrt. Studenti ga ne dobivaju dok ga ne aktivirate.");
      load();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function activate() {
    if (!draft) return;
    if (
      !window.confirm(
        "Aktivirati ove postavke? Sustav ih prvo provjerava na uzorku pitanja; ako asistent i dalje " +
          "prosljeđuje što mora i odgovara na gradivo, postavke postaju aktivne.",
      )
    )
      return;
    setError("");
    try {
      await personaApi.activate(kbId, draft.version);
      setMessage("Provjera je pokrenuta. Rezultat se pojavljuje ispod.");
      load();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  const set = <K extends keyof PersonaSettings>(key: K, value: PersonaSettings[K]) =>
    setSettings((s) => ({ ...s, [key]: value }));

  return (
    <section>
      <h2>Postavke asistenta</h2>
      <p className="muted small">
        Određuju kako asistent razgovara sa studentima. Osnovna pravila vrijede uvijek: odgovara
        samo iz odobrenih materijala, navodi izvore, ne daje gotova rješenja i pitanja o ocjenama i
        rokovima prosljeđuje vama.
      </p>
      {active ? (
        <p className="small">Aktivne su postavke verzije {active.version}.</p>
      ) : (
        <p className="small">Još nema aktivnih postavki: asistent radi sa zadanim ponašanjem.</p>
      )}

      <form className="card persona-form" onSubmit={save}>
        <fieldset>
          <legend>Obraćanje studentu</legend>
          <label className="inline">
            <input
              type="radio"
              name="address"
              checked={settings.address === "vi"}
              onChange={() => set("address", "vi")}
            />
            „Vi”
          </label>
          <label className="inline">
            <input
              type="radio"
              name="address"
              checked={settings.address === "ti"}
              onChange={() => set("address", "ti")}
            />
            „ti”
          </label>
        </fieldset>
        <label>
          Duljina odgovora
          <select
            value={settings.answer_length}
            onChange={(e) => set("answer_length", e.target.value as PersonaSettings["answer_length"])}
          >
            <option value="short">kratko (2–4 rečenice)</option>
            <option value="medium">srednje (1–2 odlomka)</option>
            <option value="long">opširnije kad tema traži</option>
          </select>
        </label>
        <label>
          Pomoć kod zadataka i vježbi (gotova rješenja nikad)
          <select
            value={settings.exercise_help}
            onChange={(e) => set("exercise_help", e.target.value as PersonaSettings["exercise_help"])}
          >
            <option value="hints">samo smjernice i potpitanja</option>
            <option value="guided">koraci pristupa, bez konačnog rješenja</option>
            <option value="concepts">samo objašnjenje pojmova</option>
          </select>
        </label>
        <label className="inline">
          <input
            type="checkbox"
            checked={settings.examples}
            onChange={(e) => set("examples", e.target.checked)}
          />
          Primjeri i usporedbe iz materijala kad pomažu
        </label>
        <label>
          Nazivlje (neobavezno)
          <input
            value={settings.terminology}
            onChange={(e) => set("terminology", e.target.value)}
            maxLength={300}
            placeholder="npr. hrvatski nazivi, engleski u zagradi"
          />
        </label>
        <label>
          Teme na koje asistent ne odgovara nego ih prosljeđuje vama (jedna po retku)
          <textarea
            value={topics}
            onChange={(e) => setTopics(e.target.value)}
            rows={3}
            placeholder="npr. projektni zadatak"
          />
        </label>
        <label>
          Dodatne upute (neobavezno)
          <textarea
            value={freeText}
            onChange={(e) => setFreeText(e.target.value)}
            rows={3}
            maxLength={2000}
          />
        </label>
        <div className="row actions">
          <button type="submit">Spremi nacrt</button>
          <button
            type="button"
            className="secondary"
            disabled={!draft || !live || checking}
            onClick={activate}
          >
            Aktiviraj
          </button>
          {!live && <span className="small muted">Aktivacija je moguća kad objavite materijale.</span>}
        </div>
        {message && <p role="status">{message}</p>}
        {error && <p className="error">{error}</p>}
      </form>

      {draft && live && <Preview kbId={kbId} version={draft.version} />}

      <h3>Verzije postavki</h3>
      {versions === null ? (
        <p className="muted">Učitavam…</p>
      ) : versions.length === 0 ? (
        <p className="muted">Još nema spremljenih postavki.</p>
      ) : (
        <ul className="versions">
          {versions.map((v) => (
            <li key={v.version} className={`version ${v.status === "active" ? "active" : ""}`}>
              <div className="row">
                <strong>Verzija {v.version}</strong>
                <span className={`badge ${v.status === "active" ? "ok" : v.status === "rejected" ? "bad" : ""}`}>
                  {STATUS[v.status]}
                </span>
              </div>
              <details>
                <summary className="small">Što asistent dobiva kao upute</summary>
                <pre className="prompt-text">{v.prompt_text}</pre>
              </details>
              {v.check_report && <ReportLine report={v.check_report} />}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ReportLine({ report: r }: { report: NonNullable<Persona["check_report"]> }) {
  return (
    <div className="small">
      <p className={r.passed ? "check-line good" : "check-line weak"}>
        Provjera: {r.passed ? "prošla" : "nije prošla"}
        {r.generic !== undefined &&
          ` · proslijeđeno ${r.forwarded} od ${r.generic} pitanja koja ne smije odgovoriti`}
        {r.check_questions ? ` · odgovoreno ${r.answered} od ${r.check_questions} pitanja iz materijala` : ""}
      </p>
      {r.reasons.length > 0 && <p className="error">{r.reasons.join("; ")}</p>}
      {r.items && r.items.length > 0 && (
        <details>
          <summary>Odgovori s ovim postavkama</summary>
          <ul className="report-items">
            {r.items.map((it, i) => (
              <li key={i}>
                <strong>„{it.question}”</strong>{" "}
                <span className="muted">{it.forwarded ? `→ proslijeđeno (${it.forwarded})` : "→ odgovoreno"}</span>
                {!it.forwarded && <p>{it.answer}</p>}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function Preview({ kbId, version }: { kbId: string; version: number }) {
  const [question, setQuestion] = useState("");
  const [stage, setStage] = useState<string | null>(null);
  const [result, setResult] = useState<{ answer: string; citations: Citation[]; forwarded: boolean } | null>(null);
  const [error, setError] = useState("");

  async function ask(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setResult(null);
    setStage("");
    try {
      await personaApi.preview(kbId, version, question.trim(), (ev) => {
        if (ev.event === "stage") setStage((ev.data as { stage: string }).stage);
        else if (ev.event === "answer") setResult(ev.data as { answer: string; citations: Citation[]; forwarded: boolean });
        else if (ev.event === "error") setError((ev.data as { detail: string }).detail);
      });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setStage(null);
    }
  }

  return (
    <section className="card">
      <h3>Isprobaj nacrt</h3>
      <p className="muted small">
        Postavite pitanje kao student: odgovor dolazi iz objavljenih materijala s postavkama iz
        nacrta. Ništa se ne sprema i ništa se ne prosljeđuje.
      </p>
      <form className="row" onSubmit={ask}>
        <label className="grow">
          Probno pitanje
          <input value={question} onChange={(e) => setQuestion(e.target.value)} maxLength={2000} />
        </label>
        <button type="submit" disabled={!question.trim() || stage !== null}>
          Pitaj
        </button>
      </form>
      {stage !== null && <p className="muted small">{STAGES[stage] ?? "Šaljem pitanje…"}</p>}
      {error && <p className="error">{error}</p>}
      {result && (
        <div className="bubble ai preview">
          <p className="who">AI asistent (proba)</p>
          <p className="answer">{withCitations(result.answer, result.citations, "preview")}</p>
          {result.citations.length > 0 && (
            <ol className="sources small">
              {result.citations.map((c) => (
                <li key={c.n} id={`src-preview-${c.n}`} value={c.n}>
                  {citationLabel(c)}
                </li>
              ))}
            </ol>
          )}
          {result.forwarded && <p className="small muted">(Ovo bi pitanje otišlo vama.)</p>}
        </div>
      )}
    </section>
  );
}
