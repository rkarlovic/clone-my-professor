import { useCallback, useEffect, useState } from "react";
import { lmsApi, type LmsLink } from "../api";

const STATUS: Record<string, string> = {
  removed: "datoteke kojih više nema u sustavu za učenje uklonjene su iz asistenta",
  new_version: "pronađene su nove ili promijenjene datoteke: nova verzija čeka vaš pregled",
  unchanged: "nema novih ni promijenjenih datoteka",
  busy: "prethodna verzija se još obrađuje; provjera se ponavlja sljedeći put",
  not_configured: "sinkronizacija za ovu platformu nije postavljena",
  failed: "provjera nije uspjela",
};

/** The LMS courses linked to this CMP course and their material sync. Off until the teacher
 * turns it on; found changes always become a draft to review (never live by themselves). */
export function LmsLinks({ kbId, onSynced }: { kbId: string; onSynced: () => void }) {
  const [links, setLinks] = useState<LmsLink[]>([]);
  const [error, setError] = useState("");
  const [waiting, setWaiting] = useState<string | null>(null);

  const load = useCallback(() => {
    lmsApi
      .links(kbId)
      .then(setLinks)
      .catch((e: Error) => setError(e.message));
  }, [kbId]);
  useEffect(load, [load]);

  // After "check now", refresh until the link's last sync time changes.
  useEffect(() => {
    if (!waiting) return;
    const before = links.find((l) => l.id === waiting)?.last_sync_at;
    const t = setInterval(async () => {
      const fresh = await lmsApi.links(kbId);
      setLinks(fresh);
      if (fresh.find((l) => l.id === waiting)?.last_sync_at !== before) {
        setWaiting(null);
        onSynced();
      }
    }, 3000);
    return () => clearInterval(t);
  }, [waiting, kbId, links, onSynced]);

  if (links.length === 0) return null;

  async function toggle(link: LmsLink) {
    try {
      await lmsApi.settings(kbId, link.id, !link.sync_enabled);
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function syncNow(link: LmsLink) {
    setError("");
    try {
      await lmsApi.syncNow(kbId, link.id);
      setWaiting(link.id);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <section className="card">
      <h2>Sustav za učenje</h2>
      {links.map((l) => (
        <div key={l.id} className="lms-link">
          <p>
            <strong>{l.context_title ?? "Kolegij"}</strong> <span className="muted small">({l.platform})</span>
          </p>
          {l.sync_available ? (
            <>
              <label className="inline">
                <input type="checkbox" checked={l.sync_enabled} onChange={() => toggle(l)} />
                Svakih 20 minuta provjeri ima li novih ili promijenjenih datoteka (nova verzija uvijek
                čeka vaš pregled)
              </label>
              <div className="row actions">
                <button type="button" className="secondary" disabled={waiting === l.id} onClick={() => syncNow(l)}>
                  {waiting === l.id ? "Provjeravam…" : "Provjeri sada"}
                </button>
                {l.last_sync_at && (
                  <span className="small muted">
                    Zadnja provjera {new Date(l.last_sync_at).toLocaleString("hr-HR")}:{" "}
                    {STATUS[l.last_sync_status ?? ""] ?? l.last_sync_status}
                  </span>
                )}
              </div>
              {l.last_sync_report?.removed_files && l.last_sync_report.removed_files.length > 0 && (
                <p className="small muted">Uklonjeno: {l.last_sync_report.removed_files.join(", ")}</p>
              )}
              {l.last_sync_report?.removal_skipped && (
                <p className="small error">
                  Sustav za učenje nije vratio nijednu datoteku, pa ništa nije uklonjeno (provjerite pristup).
                </p>
              )}
            </>
          ) : (
            <p className="small muted">Automatsko preuzimanje materijala za ovu platformu nije postavljeno.</p>
          )}
        </div>
      ))}
      {error && <p className="error">{error}</p>}
    </section>
  );
}
