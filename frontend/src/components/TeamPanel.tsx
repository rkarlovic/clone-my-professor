import { useCallback, useEffect, useState } from "react";
import { teamApi, type Member } from "../api";
import { href } from "../router";

const VIA: Record<Member["added_via"], string> = {
  owner: "osnovao/la kolegij",
  lti: "iz sustava za učenje",
  invite: "na poziv",
  teams: "iz Microsoft Teamsa",
};

/** The course's teachers: invite (owners), remove, make owner, leave. */
export function TeamPanel({ kbId }: { kbId: string }) {
  const [members, setMembers] = useState<Member[] | null>(null);
  const [invite, setInvite] = useState<{ url: string; expires_at: string } | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    teamApi.members(kbId).then(setMembers, (e: Error) => setError(e.message));
  }, [kbId]);
  useEffect(load, [load]);

  const iAmOwner = members?.some((m) => m.me && m.role === "owner") ?? false;
  const owners = members?.filter((m) => m.role === "owner").length ?? 0;
  // the last owner cannot leave (the course always keeps one); others may
  const mayLeave = (m: Member) => m.me && !(m.role === "owner" && owners <= 1);

  async function run(action: () => Promise<unknown>, after?: () => void) {
    setError("");
    try {
      await action();
      after ? after() : load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  function remove(m: Member) {
    const text = m.me
      ? "Napustiti kolegij? Više ga nećete vidjeti dok vas netko ponovno ne pozove."
      : `Ukloniti ${m.name} iz kolegija?` +
        (m.added_via === "lti"
          ? " Ako je i dalje nastavnik kolegija u sustavu za učenje, vratit će se kad otvori asistenta."
          : "");
    if (!window.confirm(text)) return;
    run(
      () => teamApi.remove(kbId, m.teacher_id),
      m.me ? () => (window.location.hash = href.teacher) : undefined,
    );
  }

  if (members === null) return <p className="muted">{error || "Učitavam…"}</p>;
  return (
    <section>
      <h2>Nastavnici</h2>
      <ul className="list team">
        {members.map((m) => (
          <li key={m.teacher_id} className="row">
            <span>
              <strong>{m.name}</strong>
              {m.me && " (vi)"} · {m.role === "owner" ? "vlasnik" : "nastavnik"} ·{" "}
              <span className="muted small">{VIA[m.added_via]}</span>
            </span>
            <span className="row actions">
              {iAmOwner && m.role !== "owner" && (
                <button className="secondary" onClick={() => run(() => teamApi.makeOwner(kbId, m.teacher_id))}>
                  Učini vlasnikom
                </button>
              )}
              {(mayLeave(m) || (!m.me && iAmOwner && m.role !== "owner")) && (
                <button className="secondary" onClick={() => remove(m)}>
                  {m.me ? "Napusti kolegij" : "Ukloni"}
                </button>
              )}
            </span>
          </li>
        ))}
      </ul>
      {iAmOwner && (
        <div className="card">
          <p className="small muted">
            Pozovite kolegu: poveznica vrijedi 7 dana i može se iskoristiti jednom. Kolega se
            prijavljuje svojim računom ustanove.
          </p>
          <button onClick={() => run(() => teamApi.invite(kbId).then(setInvite), () => {})}>
            Nova poveznica za poziv
          </button>
          {invite && (
            <p>
              <input readOnly value={invite.url} aria-label="Poveznica za poziv" onFocus={(e) => e.target.select()} />
              <span className="small muted">
                {" "}
                Vrijedi do {new Date(invite.expires_at).toLocaleDateString("hr-HR")}. Pošaljite je
                samo osobi koju pozivate.
              </span>
            </p>
          )}
        </div>
      )}
      {error && <p className="error">{error}</p>}
    </section>
  );
}
