import { useEffect, useState } from "react";

/** CMP as a Microsoft Teams tab (for schools without LTI in Teams).
 *
 * - #/teams/config: Teams shows it when a teacher adds the tab to a channel; it only saves the tab.
 * - #/teams: the tab. Teams single sign-on gives a token for CMP's app; the server checks it and,
 *   through Microsoft Graph, whether the user owns the team (teacher) or is in it (student).
 *   Then it continues exactly like an LTI launch (#/lti), so linking and the chat are the same.
 *
 * The Teams library is loaded only here, so the other pages stay small. */

type Teams = typeof import("@microsoft/teams-js");

async function teams(): Promise<Teams> {
  const sdk = await import("@microsoft/teams-js");
  await sdk.app.initialize();
  return sdk;
}

export function TeamsConfigPage() {
  const [error, setError] = useState("");
  useEffect(() => {
    teams()
      .then((sdk) => {
        const base = `${window.location.origin}${window.location.pathname}`;
        sdk.pages.config.registerOnSaveHandler((save) => {
          sdk.pages.config
            .setConfig({
              entityId: "cmp-assistant",
              contentUrl: `${base}#/teams`,
              websiteUrl: base,
              suggestedDisplayName: "Asistent",
            })
            .then(() => save.notifySuccess(), (e: Error) => save.notifyFailure(e.message));
        });
        sdk.pages.config.setValidityState(true);
      })
      .catch(() => setError("Ova stranica radi samo unutar Microsoft Teamsa."));
  }, []);
  return (
    <main className="page narrow">
      <h1>AI asistent kolegija</h1>
      <p>
        Dodajte asistenta na ovaj kanal. Kad ga prvi put otvorite kao vlasnik tima, povezat ćete tim s
        kolegijem u asistentu; članovi tima tada dobivaju chat tog kolegija.
      </p>
      <p className="small muted">
        Asistent odgovara samo iz materijala koje odobrite i uvijek navodi izvor; na što ne zna odgovor,
        prosljeđuje vama.
      </p>
      {error && <p className="error">{error}</p>}
    </main>
  );
}

export function TeamsTabPage() {
  const [error, setError] = useState("");
  useEffect(() => {
    (async () => {
      let sdk: Teams;
      try {
        sdk = await teams();
      } catch {
        setError("Ova stranica radi samo unutar Microsoft Teamsa.");
        return;
      }
      const context = await sdk.app.getContext();
      const teamId = context.team?.groupId;
      if (!teamId) {
        setError("Asistenta otvorite iz kanala tima.");
        return;
      }
      const ssoToken = await sdk.authentication.getAuthToken();
      const r = await fetch("/api/teams/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: ssoToken, team_id: teamId, team_name: context.team?.displayName }),
      });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) {
        setError(typeof body.detail === "string" ? body.detail : `Prijava nije uspjela (HTTP ${r.status}).`);
        return;
      }
      // the same landing as an LTI launch: it keeps the token for this tab and moves on
      const query = new URLSearchParams({ next: body.next, ...(body.token ? { token: body.token } : {}) });
      window.location.hash = `#/lti?${query}`;
    })().catch((e: Error) => setError(`Prijava preko Teamsa nije uspjela: ${e.message}`));
  }, []);
  return (
    <main className="page narrow">
      <p className={error ? "error" : "muted"}>{error || "Prijavljujem vas preko Teamsa…"}</p>
    </main>
  );
}
