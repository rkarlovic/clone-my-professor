import { useRef, useState } from "react";
import { teacherApi } from "../api";

const ACCEPT = ".pdf,.pptx,.docx";

export function UploadBox({
  kbId,
  disabled,
  onUploaded,
}: {
  kbId: string;
  disabled: boolean;
  onUploaded: () => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [sending, setSending] = useState(false);
  const [message, setMessage] = useState("");
  const [over, setOver] = useState(false);

  async function send() {
    setSending(true);
    setMessage("");
    try {
      await teacherApi.upload(kbId, files);
      setFiles([]);
      if (input.current) input.current.value = "";
      setMessage("Učitano. Obrada je u tijeku; nova verzija pojavit će se ispod.");
      onUploaded();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setSending(false);
    }
  }

  return (
    <section
      className={`card upload${over ? " over" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        setFiles(Array.from(e.dataTransfer.files));
      }}
    >
      <h2>Učitaj materijale</h2>
      <p className="muted">
        PDF, PowerPoint ili Word. Datoteke koje se nisu promijenile ne obrađuju se ponovno. Nova
        verzija je nacrt: studenti je ne vide dok je ne odobrite i objavite.
      </p>
      <label className="file-pick">
        Odaberi datoteke ili ih povuci ovamo
        <input
          ref={input}
          type="file"
          multiple
          accept={ACCEPT}
          onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
        />
      </label>
      {files.length > 0 && (
        <ul className="small">
          {files.map((f) => (
            <li key={f.name}>{f.name}</li>
          ))}
        </ul>
      )}
      <button type="button" onClick={send} disabled={files.length === 0 || sending || disabled}>
        {sending ? "Učitavam…" : "Učitaj"}
      </button>
      {disabled && <span className="muted"> Pričekajte da se prethodno učitavanje obradi.</span>}
      {message && <p role="status">{message}</p>}
    </section>
  );
}
