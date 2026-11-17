// Render an answer's [n] markers as references to its sources (rule 1: every answer cites).

import { Fragment, type ReactNode } from "react";
import type { Citation } from "./api";

const MARKER = /\[(\d+(?:\s*[,;]\s*\d+)*)\]/g;

export function citationLabel(c: Citation): string {
  return c.pages.length === 0 ? c.file_name : `${c.file_name}, str. ${c.pages.join(", ")}`;
}

export const sourceId = (prefix: string, n: number) => `src-${prefix}-${n}`;

/** Answer text with [1] / [1, 2] markers turned into superscript buttons that scroll to the
 * source (buttons, not #anchors: the app uses hash routes). Markers that name no cited
 * source are left out, like the backend's citation check. */
export function withCitations(text: string, citations: Citation[], prefix: string): ReactNode[] {
  const known = new Set(citations.map((c) => c.n));
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(MARKER)) {
    const at = m.index ?? 0;
    out.push(text.slice(last, at));
    const ns = m[1]
      .split(/[,;]/)
      .map((s) => Number(s.trim()))
      .filter((n) => known.has(n));
    if (ns.length > 0) {
      out.push(
        <sup key={`m${at}`} className="cite">
          {ns.map((n, i) => (
            <Fragment key={n}>
              {i > 0 && ","}
              <button
                type="button"
                className="cite-link"
                aria-label={`izvor ${n}`}
                onClick={() => document.getElementById(sourceId(prefix, n))?.scrollIntoView()}
              >
                {n}
              </button>
            </Fragment>
          ))}
        </sup>,
      );
    }
    last = at + m[0].length;
  }
  out.push(text.slice(last));
  return out;
}
