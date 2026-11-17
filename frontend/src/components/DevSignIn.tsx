import { useState } from "react";
import { getIdentity, setIdentity, type Role } from "../identity";

const LABEL: Record<Role, string> = { teacher: "Nastavnik", student: "Student" };

/** Development stand-in for sign-in: a name sent as a header. Never used in production. */
export function DevSignIn({ role, onChange }: { role: Role; onChange?: (name: string) => void }) {
  const [name, setName] = useState(getIdentity(role));
  const [saved, setSaved] = useState(getIdentity(role));
  return (
    <form
      className="dev-signin"
      onSubmit={(e) => {
        e.preventDefault();
        const value = name.trim();
        setIdentity(role, value);
        setSaved(value);
        onChange?.(value);
      }}
    >
      <label>
        {LABEL[role]} (razvojna prijava)
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={role === "teacher" ? "npr. ana" : "npr. student1"}
          maxLength={100}
        />
      </label>
      <button type="submit">{saved ? "Promijeni" : "Prijavi se"}</button>
      {saved && <span className="muted">prijavljen/a kao „{saved}”</span>}
    </form>
  );
}
