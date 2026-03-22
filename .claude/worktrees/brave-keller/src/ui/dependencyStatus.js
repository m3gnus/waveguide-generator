import {
  formatDependencyBlockMessage,
  getRuntimeDoctorComponents,
  getRuntimeDoctorIssues,
} from "../modules/runtime/health.js";

function createNode(tagName, className, textContent = "") {
  const node = document.createElement(tagName);
  if (className) {
    node.className = className;
  }
  if (textContent) {
    node.textContent = textContent;
  }
  return node;
}

function createDependencyItem(component) {
  const item = createNode(
    "div",
    `dep-item dep-status-${component.status === "installed" ? "ready" : component.category === "optional" ? "partial" : "missing"}`,
  );

  item.appendChild(
    createNode(
      "span",
      "dep-icon",
      component.status === "installed" ? "✓" : component.category === "optional" ? "!" : "✗",
    ),
  );

  const name = component.version
    ? `${component.name} (${component.version})`
    : component.name;
  item.appendChild(createNode("span", "dep-name", name));

  const feature = component.requiredFor || component.featureImpact || "";
  item.appendChild(createNode("span", "dep-feature", feature));

  const detail = createNode(
    "span",
    "dep-guidance",
    component.status === "installed"
      ? component.featureImpact || component.detail || ""
      : component.guidance[0] || component.featureImpact || component.detail || "",
  );
  if (detail.textContent) {
    item.appendChild(detail);
  }

  return item;
}

function renderDependencyStatus(container, health) {
  const content = container?.querySelector(".dependency-status-content");
  if (!content) {
    return;
  }

  const components = getRuntimeDoctorComponents(health);
  content.replaceChildren();

  if (components.length === 0) {
    const empty = createNode(
      "div",
      "dependency-status-empty",
      "Dependency status is unavailable until the backend health check succeeds.",
    );
    content.appendChild(empty);
    container.classList.remove("has-warnings");
    return;
  }

  const list = createNode("div", "dependency-status");
  for (const component of components) {
    list.appendChild(createDependencyItem(component));
  }
  content.appendChild(list);

  container.classList.toggle(
    "has-warnings",
    getRuntimeDoctorIssues(health).length > 0,
  );
}

export function createDependencyStatusPanel(health) {
  const container = createNode("div", "dependency-status-panel");
  container.setAttribute("role", "region");
  container.setAttribute("aria-label", "Dependency status");

  const header = createNode("div", "dependency-status-header");
  header.appendChild(createNode("span", "dependency-status-title", "Runtime Dependencies"));

  const toggle = createNode("button", "dependency-status-toggle", "▶");
  toggle.type = "button";
  toggle.setAttribute("aria-expanded", "false");
  header.appendChild(toggle);
  container.appendChild(header);

  const content = createNode("div", "dependency-status-content is-hidden");
  container.appendChild(content);

  toggle.addEventListener("click", () => {
    const expanded = toggle.getAttribute("aria-expanded") === "true";
    toggle.setAttribute("aria-expanded", String(!expanded));
    toggle.textContent = expanded ? "▶" : "▼";
    content.classList.toggle("is-hidden", expanded);
  });

  renderDependencyStatus(container, health);
  if (getRuntimeDoctorIssues(health).length > 0) {
    toggle.click();
  }
  return container;
}

export function updateDependencyStatusPanel(container, health) {
  if (!container) {
    return;
  }
  renderDependencyStatus(container, health);
}

export function getFeatureBlockedReason(health, feature) {
  const featureAliases = {
    "occ-mesh": ["meshBuild"],
    "mesh-build": ["meshBuild"],
    "export-msh": ["meshBuild"],
    "bem-solve": ["solve", "meshBuild"],
    simulation: ["solve", "meshBuild"],
    "chart-render": ["charts"],
    matplotlib: ["charts"],
  };

  const features = featureAliases[String(feature || "").trim()] || [];
  const issues = getRuntimeDoctorIssues(health, {
    features,
    includeOptional: true,
  });
  if (issues.length === 0) {
    return null;
  }

  return formatDependencyBlockMessage(health, {
    features,
    fallback: "Backend dependency requirements are not satisfied.",
    includeOptional: true,
  });
}
