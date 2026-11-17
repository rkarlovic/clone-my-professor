import { useCallback, useEffect, useState } from "react";
import { adminApi, type Platform, type PlatformIn } from "../api";
import { href } from "../router";

const EMPTY: PlatformIn = {
  name: "",
  issuer: "",
  client_id: "",
  auth_login_url: "",
  jwks_url: "",
  deployment_ids: [],
  ws_base_url: null,
  ws_token_env: null,
};

/** For whoever runs the installation: register learning platforms (LTI) and their Web
 * Services. The admin token (ADMIN_TOKEN in .env) is kept for this tab only. */
export function AdminPage() {
  const [token, setTokenState] = useState(adminApi.token());
  const [entered, setEntered] = useState("");
  const [tool, setTool] = useState<Record<string, string> | null>(null);
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [editing, setEditing] = useState<{ id: string | null; body: PlatformIn } | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!adminApi.token()) return;
    try {
      setTool(await adminApi.tool());
      setPlatforms(await adminApi.platforms());
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);
  useEffect(() => {
    load();
  }, [load, token]);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!editing) return;
    try {
      if (editing.id) await adminApi.change(editing.id, editing.body);
      else await adminApi.register(editing.body);
      setEditing(null);
      load();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  const field = (key: keyof PlatformIn, label: string, hint = "") => (
    <label>
      {label}
      <input
        value={key === "deployment_ids" ? editing!.body.deployment_ids.join(", ") : ((editing!.body[key] as string) ?? "")}
        onChange={(e) =>
          setEditing((ed) =>
            ed && {
              ...ed,
              body: {
                ...ed.body,
                [key]:
                  key === "deployment_ids"
                    ? e.target.value.split(",").map((x) => x.trim()).filter(Boolean)
                    : e.target.value || (key.startsWith("ws_") ? null : ""),
              },
            },
          )
        }
        placeholder={hint}
      />
    </label>
  );

  return (
    <main className="page">
      <nav className="crumbs">
        <a href={href.home}>Početna</a> › Administracija
      </nav>
      <h1>Administracija</h1>
      {!token ? (
        <form
          className="card dev-signin"
          onSubmit={(e) => {
            e.preventDefault();
            adminApi.setToken(entered.trim());
            setTokenState(entered.trim());
          }}
        >
          <label className="grow">
            Administratorski ključ (ADMIN_TOKEN iz .env)
            <input type="password" value={entered} onChange={(e) => setEntered(e.target.value)} />
          </label>
          <button type="submit">Otvori</button>
        </form>
      ) : (
        <>
          {error && <p className="error">{error}</p>}
          {tool && (
            <section className="card">
              <h2>Podaci za sustav za učenje</h2>
              <p className="small muted">Unesite ih kad CMP dodajete kao vanjski alat (LTI 1.3).</p>
              <ul className="small">
                {Object.entries(tool).map(([k, v]) => (
                  <li key={k}>
                    {k}: <code>{v}</code>
                  </li>
                ))}
              </ul>
            </section>
          )}
          <section className="card">
            <div className="row">
              <h2 className="grow">Sustavi za učenje</h2>
              <button type="button" onClick={() => setEditing({ id: null, body: EMPTY })}>
                Dodaj
              </button>
            </div>
            <ul className="list">
              {platforms.map((p) => (
                <li key={p.id}>
                  <strong>{p.name}</strong> <span className="small muted">{p.issuer}</span>{" "}
                  {p.ws_base_url ? (
                    <span className={`badge ${p.ws_token_set ? "ok" : "bad"}`}>
                      preuzimanje materijala {p.ws_token_set ? "postavljeno" : `nedostaje ${p.ws_token_env}`}
                    </span>
                  ) : (
                    <span className="badge">bez preuzimanja materijala</span>
                  )}{" "}
                  <button type="button" className="link" onClick={() => setEditing({ id: p.id, body: { ...p } })}>
                    Uredi
                  </button>
                </li>
              ))}
            </ul>
          </section>
          {editing && (
            <form className="card persona-form" onSubmit={save}>
              <h2>{editing.id ? "Uredi sustav" : "Novi sustav"}</h2>
              {field("name", "Naziv", "npr. Merlin")}
              {field("issuer", "Issuer (platform ID)", "https://moodle.example.hr")}
              {field("client_id", "Client ID")}
              {field("auth_login_url", "Authentication request URL", ".../mod/lti/auth.php")}
              {field("jwks_url", "Public keyset URL", ".../mod/lti/certs.php")}
              {field("deployment_ids", "Deployment ID-jevi (zarezom)", "1")}
              {field("ws_base_url", "Web Services adresa (neobavezno)", "https://moodle.example.hr")}
              {field("ws_token_env", "Ime varijable s tokenom (ne token!)", "MOODLE_WS_TOKEN")}
              <div className="row actions">
                <button type="submit">Spremi</button>
                <button type="button" className="secondary" onClick={() => setEditing(null)}>
                  Odustani
                </button>
              </div>
            </form>
          )}
        </>
      )}
    </main>
  );
}
