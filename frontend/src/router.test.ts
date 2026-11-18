import { parseRoute } from "./router";

test("hash routes", () => {
  expect(parseRoute("")).toEqual({ page: "home" });
  expect(parseRoute("#/teacher")).toEqual({ page: "teacher" });
  expect(parseRoute("#/teacher/abc")).toEqual({ page: "course", kbId: "abc" });
  expect(parseRoute("#/chat/abc")).toEqual({ page: "chat", kbId: "abc" });
  expect(parseRoute("#/nonsense")).toEqual({ page: "home" });
  expect(parseRoute("#/invite/Code_1-x")).toEqual({ page: "invite", code: "Code_1-x" });
  expect(parseRoute("#/invite")).toEqual({ page: "home" });
  expect(parseRoute("#/teams")).toEqual({ page: "teams", config: false });
  expect(parseRoute("#/teams/config")).toEqual({ page: "teams", config: true });
  const lti = parseRoute("#/lti?next=chat/abc&token=t.k.n");
  expect(lti.page).toBe("lti");
  if (lti.page === "lti") {
    expect(lti.query.get("next")).toBe("chat/abc");
    expect(lti.query.get("token")).toBe("t.k.n");
  }
});
