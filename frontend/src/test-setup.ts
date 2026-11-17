import "@testing-library/jest-dom/vitest";

// jsdom has no scrollIntoView; current browsers have one that returns a Promise. Mimic the
// browser, so a component that accidentally returns its result from an effect fails here too.
Element.prototype.scrollIntoView = function () {
  return Promise.resolve();
} as unknown as Element["scrollIntoView"];
