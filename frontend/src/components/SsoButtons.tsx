import { useEffect, useState } from "react";
import { ssoLoginUrl, teamApi, type SsoProvider } from "../api";
import { getToken } from "../identity";

/** "Prijava s Microsoftom / Googleom" for teachers; shows nothing when the server offers none. */
export function SsoButtons({ next = "teacher" }: { next?: string }) {
  const [providers, setProviders] = useState<SsoProvider[]>([]);
  useEffect(() => {
    teamApi.providers().then(setProviders, () => setProviders([]));
  }, []);
  if (providers.length === 0) return null;
  if (getToken("teacher"))
    return <p className="small muted">Prijavljeni ste. Za drugi račun zatvorite karticu.</p>;
  return (
    <div className="row actions">
      {providers.map((p) => (
        <a key={p.id} className="button" href={ssoLoginUrl(p.id, next)}>
          Prijava s računom {p.label}
        </a>
      ))}
    </div>
  );
}
