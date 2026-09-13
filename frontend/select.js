/** Progressive native select component: preserves forms, labels and keyboard behavior. */
export function setupSelects(root = document) {
  for (const select of root.querySelectorAll("select")) {
    select.classList.add("studio-select");
    if (select.closest(".chat-tools, .route-control"))
      select.classList.add("studio-select-compact");
  }
}
