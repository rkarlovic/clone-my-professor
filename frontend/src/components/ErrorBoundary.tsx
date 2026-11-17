import { Component, type ReactNode } from "react";

/** A page error shows a message and a reload button instead of a blank page. */
export class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <main className="page narrow">
        <p className="error" role="alert">
          Nešto je pošlo po zlu na ovoj stranici.
        </p>
        <button type="button" onClick={() => window.location.reload()}>
          Učitaj ponovno
        </button>
      </main>
    );
  }
}
