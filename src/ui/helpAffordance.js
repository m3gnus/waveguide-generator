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
  if (!section || !doc || typeof doc.createElement !== "function" || !text) {
    return null;
  }

  const note = doc.createElement("div");
  note.className = "section-note";
  note.textContent = text;
  section.appendChild(note);
  return note;
}
