import { render, screen } from "@testing-library/react";
import { devHeaders, getToken, setToken } from "../identity";
import { LtiPage } from "./LtiPage";

afterEach(() => {
  setToken("student", "");
  setToken("teacher", "");
  window.location.hash = "";
});

test("a student launch keeps the token for the tab, hides it and opens the chat", () => {
  render(<LtiPage query={new URLSearchParams("next=chat/kb1&token=abc.def.ghi")} />);
  expect(getToken("student")).toBe("abc.def.ghi");
  expect(window.location.href).not.toContain("abc.def.ghi");
  expect(window.location.hash).toBe("#/chat/kb1");
  expect(devHeaders("student")).toEqual({ Authorization: "Bearer abc.def.ghi" });
});

test("an unlinked course says the assistant is not ready", () => {
  render(<LtiPage query={new URLSearchParams("next=not-linked")} />);
  expect(screen.getByText("Asistent još nije spreman")).toBeInTheDocument();
  expect(getToken("student")).toBe("");
});
