import { parseRoute } from "./router";

test("hash routes", () => {
  expect(parseRoute("")).toEqual({ page: "home" });
  expect(parseRoute("#/teacher")).toEqual({ page: "teacher" });
  expect(parseRoute("#/teacher/abc")).toEqual({ page: "course", kbId: "abc" });
  expect(parseRoute("#/chat/abc")).toEqual({ page: "chat", kbId: "abc" });
  expect(parseRoute("#/nonsense")).toEqual({ page: "home" });
});
