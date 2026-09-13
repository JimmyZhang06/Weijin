import { setupSelects } from "/select.js";
const paths = {
  spark: "m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z",
  page: "M6 3h8l4 4v14H6zM14 3v5h4M9 12h6M9 16h6",
  people:
    "M15 20H3v-2a6 6 0 0 1 12 0zm-3-12a3 3 0 1 1-6 0 3 3 0 0 1 6 0M17 5a3 3 0 0 1 0 6m1 3c3 1 3 3 3 6",
  book: "M4 4h6c2 0 2 1 2 2v15c0-2-2-3-4-3H4zM12 6c0-1 1-2 3-2h5v14h-4c-2 0-4 1-4 3",
};
export function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.classList.add("ui-icon");
  const path = document.createElementNS(svg.namespaceURI, "path");
  path.setAttribute("d", paths[name]);
  svg.append(path);
  return svg;
}
export function setupComponents() {
  setupSelects();
  for (const [selector, name] of [
    [".ai-symbol", "spark"],
    [".auto-mark", "spark"],
    [".crumb-icon", "page"],
  ]) {
    document.querySelector(selector)?.replaceChildren(icon(name));
  }
  for (const [view, name] of [
    ["manuscript", "page"],
    ["settings", "people"],
    ["extras", "book"],
  ]) {
    document.querySelector(`[data-view="${view}"]`)?.prepend(icon(name));
  }
  const menu = document.querySelector(".appearance-menu");
  document.addEventListener("pointerdown", (event) => {
    if (menu.open && !menu.contains(event.target)) menu.open = false;
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && menu.open) {
      menu.open = false;
      menu.querySelector("summary").focus();
    }
  });
  menu.querySelector(".appearance-items").addEventListener("click", (event) => {
    if (event.target.closest("button,a")) menu.open = false;
  });
}
