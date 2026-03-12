function setAttribute(element, name, value) {
    if (!element || value === undefined || value === null) return;
    if (typeof element.setAttribute === 'function') {
        element.setAttribute(name, value);
        return;
    }
    element[name] = String(value);
}

export function createHelpTrigger(doc, { labelText = '', helpText = '' } = {}) {
    if (!doc || typeof doc.createElement !== 'function' || !helpText) {
        return null;
    }

    const trigger = doc.createElement('button');
    trigger.type = 'button';
    trigger.className = 'control-help-trigger';
    trigger.textContent = '?';
    trigger.title = helpText;
    setAttribute(trigger, 'aria-label', `${labelText || 'Control'} help: ${helpText}`);
    setAttribute(trigger, 'data-help-text', helpText);
    return trigger;
}

export function createLabelRow(doc, { labelText = '', htmlFor = '', helpText = '' } = {}) {
    const row = doc.createElement('div');
    row.className = 'input-label-row';

    const label = doc.createElement('label');
    label.textContent = labelText;
    if (htmlFor) {
        label.htmlFor = htmlFor;
    }
    row.appendChild(label);

    const helpTrigger = createHelpTrigger(doc, { labelText, helpText });
    if (helpTrigger) {
        row.appendChild(helpTrigger);
    }

    return { row, label, helpTrigger };
}

export function appendSectionNote(section, doc, text) {
    if (!section || !doc || typeof doc.createElement !== 'function' || !text) {
        return null;
    }

    const note = doc.createElement('div');
    note.className = 'section-note';
    note.textContent = text;
    section.appendChild(note);
    return note;
}
