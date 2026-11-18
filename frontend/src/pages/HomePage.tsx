import { DevSignIn } from "../components/DevSignIn";
import { SsoButtons } from "../components/SsoButtons";
import { href } from "../router";

export function HomePage() {
  return (
    <main className="page narrow">
      <h1>Clone My Professor</h1>
      <p>
        AI asistent koji studentima odgovara iz materijala koje je nastavnik odobrio, s izvorima.
      </p>
      <section className="card">
        <h2>Nastavnik</h2>
        <SsoButtons />
        <DevSignIn role="teacher" />
        <p>
          <a href={href.teacher}>Moji kolegiji →</a>
        </p>
      </section>
      <section className="card">
        <h2>Student</h2>
        <DevSignIn role="student" />
        <p className="muted">
          Chat kolegija otvara se poveznicom koju dobijete od nastavnika (u pilotu: iz Moodlea).
        </p>
      </section>
      <p className="muted small">
        Nastavnici se prijavljuju računom ustanove (Microsoft/Google) ili otvaraju asistenta iz
        sustava za učenje; studenti samo iz sustava za učenje. Razvojna prijava (samo ime) radi
        samo na probnoj instalaciji.
      </p>
      <p className="small">
        <a href="#/admin">Administracija</a> (za one koji vode instalaciju)
      </p>
    </main>
  );
}
