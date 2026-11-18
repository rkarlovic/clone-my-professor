// Hash routes (#/teacher, #/teacher/<id>, #/chat/<id>): they work from a static file server
// and inside LMS iframes without server-side routing.

import { useEffect, useState } from "react";

export type Route =
  | { page: "home" }
  | { page: "teacher" }
  | { page: "course"; kbId: string }
  | { page: "chat"; kbId: string }
  | { page: "lti"; query: URLSearchParams }
  | { page: "admin" }
  | { page: "invite"; code: string };

export function parseRoute(hash: string): Route {
  const [path, query = ""] = hash.replace(/^#\/?/, "").split("?", 2);
  const parts = path.split("/").filter(Boolean);
  if (parts[0] === "lti") return { page: "lti", query: new URLSearchParams(query) };
  if (parts[0] === "admin") return { page: "admin" };
  if (parts[0] === "invite" && parts[1]) return { page: "invite", code: parts[1] };
  if (parts[0] === "teacher" && parts[1]) return { page: "course", kbId: parts[1] };
  if (parts[0] === "teacher") return { page: "teacher" };
  if (parts[0] === "chat" && parts[1]) return { page: "chat", kbId: parts[1] };
  return { page: "home" };
}

export const href = {
  home: "#/",
  teacher: "#/teacher",
  course: (kbId: string) => `#/teacher/${kbId}`,
  chat: (kbId: string) => `#/chat/${kbId}`,
  invite: (code: string) => `#/invite/${code}`,
};

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}
