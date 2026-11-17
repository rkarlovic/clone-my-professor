import { render, screen } from "@testing-library/react";
import { citationLabel, withCitations } from "./citations";

const cites = [
  { n: 1, file_name: "7 Identifikacija.pptx", pages: [16] },
  { n: 2, file_name: "Okvir.pdf", pages: [67, 68] },
];

test("labels name the file and every page", () => {
  expect(citationLabel(cites[1])).toBe("Okvir.pdf, str. 67, 68");
  expect(citationLabel({ n: 3, file_name: "a.pdf", pages: [] })).toBe("a.pdf");
});

test("markers become source buttons; unknown numbers are dropped", () => {
  render(<p>{withCitations("Kontinuiran proces [1]. Roditelji [1, 2]; ostalo [9].", cites, "m")}</p>);
  expect(screen.getAllByRole("button", { name: "izvor 1" })).toHaveLength(2);
  expect(screen.getByRole("button", { name: "izvor 2" })).toBeInTheDocument();
  expect(screen.queryByText(/\[9\]/)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "izvor 9" })).not.toBeInTheDocument();
});
