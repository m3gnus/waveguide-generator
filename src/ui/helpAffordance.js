function setAttribute(element, name, value) {
  if (!element || value === undefined || value === null) return;
  if (typeof element.setAttribute === "function") {
    element.setAttribute(name, value);
    return;
  }
  element[name] = String(value);
}

export function createLabelRow(
  doc,
  { labelText = "", htmlFor = "", helpText = "" } = {},
) {
  const row = doc.createElement("div");
  row.className = "input-label-row";

  const label = doc.createElement("label");
  label.textContent = labelText;
  if (htmlFor) {
    label.htmlFor = htmlFor;
  }
  if (helpText) {
    setAttribute(label, "data-help-text", helpText);
    setAttribute(label, "title", helpText);
  }
  row.appendChild(label);

  return { row, label, helpTrigger: null };
}

export function appendSectionNote(section, doc, text) {
  if (!section || !text) {
    return null;
  }

  // Set the description as a tooltip on the section's summary element
  const summary = typeof section.querySelector === "function"
    ? section.querySelector("summary")
    : null;
  if (summary) {
    setAttribute(summary, "title", text);
    return summary;
  }

  // Fallback for non-details sections or fake DOMs: set title on the section itself
  setAttribute(section, "title", text);
  return section;
}
