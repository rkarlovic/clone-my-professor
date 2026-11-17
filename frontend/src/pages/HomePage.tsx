import { DevSignIn } from "../components/DevSignIn";
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
        Razvojna verzija: prijava je samo ime, bez lozinke. Prava prijava (Microsoft/Google, LTI)
        dolazi prije pilota.
      </p>
      <p className="small">
        <a href="#/admin">Administracija</a> (za one koji vode instalaciju)
      </p>
    </main>
  );
}
