import { useCallback, useEffect, useState } from "react";
import { notificationApi, type Notice } from "../api";
import { href } from "../router";

/** What a notice says, for a teacher. */
export function noticeText(n: Notice): string {
  const d = n.data as Record<string, unknown>;
  const v = d.version as number | undefined;
  switch (n.kind) {
    case "draft_ready":
      return `Verzija ${v} je obrađena i čeka vaš pregled${d.origin === "lms" ? " (iz sustava za učenje)" : ""}.`;
    case "draft_failed":
      return `Obrada verzije ${v} nije uspjela.`;
    case "lms_removed":
      return `Iz objavljene verzije uklonjeno jer više nije u sustavu za učenje: ${((d.files as string[]) ?? []).join(", ")}.`;
    case "question":
      return `Novo pitanje studenta: „${d.question as string}”`;
    case "persona_active":
      return `Postavke asistenta (verzija ${v}) prošle su provjeru i aktivne su.`;
    case "persona_rejected":
      return `Postavke asistenta (verzija ${v}) nisu prošle provjeru.`;
    default:
      return n.kind;
  }
}

/** The teacher's unread notices across their courses. */
export function TeacherNotifications({ onChange }: { onChange?: (unread: Notice[]) => void }) {
  const [items, setItems] = useState<Notice[] | null>(null);

  const load = useCallback(() => {
    notificationApi
      .list(true)
      .then((x) => {
        setItems(x);
        onChange?.(x);
      })
      .catch(() => setItems([]));
  }, [onChange]);
  useEffect(load, [load]);

  if (!items || items.length === 0) return null;

  async function readAll() {
    await notificationApi.readAll();
    load();
  }

  return (
    <section className="card notices" aria-label="Obavijesti">
      <div className="row">
        <h2 className="grow">Obavijesti ({items.length})</h2>
        <button type="button" className="link" onClick={readAll}>
          Označi sve pročitanim
        </button>
      </div>
      <ul>
        {items.map((n) => (
          <li key={n.id}>
            <a
              href={href.course(n.kb_id)}
              onClick={() => notificationApi.read([n.id])}
            >
              {n.course}
            </a>
            : {noticeText(n)}{" "}
            <span className="small muted">{new Date(n.created_at).toLocaleString("hr-HR")}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
