// Minimal DOM state model shared by the executable browser harnesses. It
// exists so the durable editor is exercised through real element state rather
// than substring matching, which is how the R4b.3 breakages escaped review.
import assert from "node:assert/strict";

export class FakeEventTarget {
  constructor() {
    this.listeners = new Map();
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  dispatchEvent(event) {
    event.target ||= this;
    event.currentTarget = this;
    event.preventDefault ||= () => { event.defaultPrevented = true; };
    event.stopPropagation ||= () => { event.propagationStopped = true; };
    for (const listener of this.listeners.get(event.type) || []) listener(event);
    if (!event.propagationStopped && this.parentNode) this.parentNode.dispatchEvent(event);
    return !event.defaultPrevented;
  }
}

export class FakeElement extends FakeEventTarget {
  constructor(tagName = "div", id = "") {
    super();
    this.tagName = tagName.toUpperCase();
    this.id = id;
    this.children = [];
    this.parentNode = null;
    this.dataset = {};
    this.attributes = new Map();
    this.className = "";
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
    this.type = "";
    this.name = "";
    this._value = "";
    this._textContent = "";
  }

  get value() {
    return this._value;
  }

  set value(value) {
    this._value = String(value);
  }

  get textContent() {
    if (this.children.length) return this.children.map((child) => child.textContent).join("");
    return this._textContent;
  }

  set textContent(value) {
    this._textContent = String(value);
    this.children = [];
  }

  get options() {
    return this.children.filter((child) => child.tagName === "OPTION");
  }

  append(...nodes) {
    for (const node of nodes.flat()) {
      const child = typeof node === "string" ? new FakeText(node) : node;
      child.parentNode = this;
      this.children.push(child);
      if (this.tagName === "SELECT" && this.options.length === 1) {
        this._value = child.value;
      }
    }
  }

  replaceChildren(...nodes) {
    this.children = [];
    if (this.tagName === "SELECT") this._value = "";
    this.append(...nodes);
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  removeAttribute(name) {
    this.attributes.delete(name);
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  scrollIntoView() {}

  closest(selector) {
    if (selector === "button[data-action]"
      && this.tagName === "BUTTON"
      && this.dataset.action) return this;
    return this.parentNode?.closest?.(selector) || null;
  }

  click() {
    this.dispatchEvent({ type: "click" });
  }
}

export class FakeText extends FakeElement {
  constructor(text) {
    super("#text");
    this._textContent = text;
  }
}

export class FakeDocument {
  constructor() {
    this.ids = new Map();
    this.all = [];
  }

  add(tag, id = "", properties = {}) {
    const node = new FakeElement(tag, id);
    Object.assign(node, properties);
    if (id) this.ids.set(id, node);
    this.all.push(node);
    return node;
  }

  createElement(tag) {
    const node = new FakeElement(tag);
    this.all.push(node);
    return node;
  }

  createTextNode(text) {
    return new FakeText(text);
  }

  querySelector(selector) {
    if (selector.startsWith("#")) return this.ids.get(selector.slice(1)) || null;
    return this.querySelectorAll(selector)[0] || null;
  }

  querySelectorAll(selector) {
    const selectors = selector.split(",").map((item) => item.trim());
    const found = [];
    for (const item of selectors) {
      if (item.startsWith("#engineering-content form ")) {
        const tag = item.split(" ").at(-1).toUpperCase();
        found.push(...this.all.filter((node) => node.tagName === tag && node.formEditor));
        continue;
      }
      const dataMatch = item.match(/^\[data-(axis|axis-unit)(?:="([^"]+)")?\]$/);
      if (dataMatch) {
        const key = dataMatch[1] === "axis" ? "axis" : "axisUnit";
        found.push(...this.all.filter((node) => (
          node.dataset[key] !== undefined
          && (dataMatch[2] === undefined || node.dataset[key] === dataMatch[2])
        )));
        continue;
      }
      const inputMatch = item.match(/^input\[name="([^"]+)"\](?:\[value="([^"]+)"\])?(?::checked)?$/);
      if (inputMatch) {
        const checked = item.endsWith(":checked");
        found.push(...this.all.filter((node) => (
          node.tagName === "INPUT"
          && node.name === inputMatch[1]
          && (inputMatch[2] === undefined || node.value === inputMatch[2])
          && (!checked || node.checked)
        )));
      }
    }
    return [...new Set(found)];
  }
}

export function buildDocument() {
  const document = new FakeDocument();
  const ids = [
    ["form", "project-create-form"], ["select", "project-select"], ["select", "setup-select"],
    ["p", "workspace-status"], ["div", "revision-conflict"], ["div", "retry-mutation"],
    ["div", "stale-source-banner"], ["button", "workspace-refresh"], ["button", "create-blank-setup"],
    ["button", "reload-current-revision"], ["button", "dismiss-revision-conflict"],
    ["button", "retry-mutation-button"], ["input", "project-name"], ["button", "upload-version-button"],
    ["span", "current-revision"], ["span", "source-version"], ["div", "engineering-empty"],
    ["div", "engineering-content"], ["span", "engineering-readiness-badge"],
    ["form", "configuration-form"], ["select", "analysis-type"], ["select", "analysis-dimensionality"],
    ["select", "analysis-coordinate-system"], ["select", "analysis-solver-target"],
    ["input", "mesh-size"], ["select", "mesh-size-unit"], ["form", "material-form"],
    ["p", "material-authority"], ["div", "material-decision-actions"], ["input", "material-name"],
    ["input", "material-e"], ["select", "material-e-unit"], ["input", "material-nu"],
    ["input", "material-density"], ["select", "material-density-unit"],
    ["button", "accept-material-proposal"], ["button", "reject-material-proposal"],
    ["form", "bc-form"], ["input", "bc-edit-index"], ["select", "bc-type"], ["select", "bc-target"],
    ["div", "bc-fixed-fields"], ["div", "bc-prescribed-fields"], ["span", "bc-viewer-selection"],
    ["p", "bc-selection-evidence"],
    ["p", "bc-normalized-preview"], ["button", "bc-cancel-edit"], ["div", "bc-list"],
    ["form", "load-form"], ["input", "load-edit-index"], ["select", "load-type"],
    ["select", "load-target"], ["label", "load-target-label"], ["select", "load-unit"],
    ["input", "load-magnitude"], ["input", "load-direction-x"], ["input", "load-direction-y"],
    ["input", "load-direction-z"], ["div", "load-direction-fields"], ["p", "pressure-convention"],
    ["p", "gravity-dependency"], ["span", "load-viewer-selection"],
    ["p", "load-selection-evidence"], ["p", "load-normalized-preview"],
    ["button", "load-cancel-edit"], ["div", "load-list"], ["option", "concentrated-force-option"],
    ["span", "engineering-ready-value"], ["span", "artifact-capability-value"],
    ["span", "compatibility-export-value"], ["ul", "engineering-issue-list"], ["dl", "load-summary"],
    ["div", "durable-region-list"], ["div", "durable-assumption-list"], ["ul", "revision-history-list"],
  ];
  for (const [tag, id] of ids) {
    const node = document.add(tag, id);
    if (["INPUT", "SELECT", "BUTTON"].includes(node.tagName)) node.formEditor = true;
  }
  for (const [id, value] of [
    ["analysis-type", "static_structural"], ["analysis-dimensionality", "3d_solid"],
    ["analysis-coordinate-system", "global_cartesian"], ["analysis-solver-target", "calculix"],
    ["mesh-size-unit", "mm"], ["material-e-unit", "MPa"], ["material-density-unit", "kg/m^3"],
    ["bc-type", "fixed_displacement"], ["load-type", "resultant_surface_force"],
  ]) document.ids.get(id).value = value;
  for (const value of ["displacement", "stress"]) {
    document.add("input", "", { name: "result_field", value, checked: true, formEditor: true });
  }
  for (const name of ["fixed_axis", "prescribed_axis"]) {
    for (const axis of ["x", "y", "z"]) {
      document.add("input", "", { name, value: axis, formEditor: true });
    }
  }
  for (const axis of ["x", "y", "z"]) {
    document.add("input", "", { dataset: { axis }, formEditor: true });
    document.add("select", "", { dataset: { axisUnit: axis }, value: "mm", formEditor: true });
  }
  return document;
}

export const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

export function deferred() {
  let resolve;
  const promise = new Promise((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}

export async function waitFor(predicate, message) {
  for (let index = 0; index < 100; index += 1) {
    if (predicate()) return;
    await tick();
  }
  assert.fail(message);
}

export function dispatch(node, type) {
  node.dispatchEvent({ type });
}

export function button(container, action, index = null) {
  const visit = (node) => {
    if (node.tagName === "BUTTON"
      && node.dataset.action === action
      && (index === null || node.dataset.index === String(index))) return node;
    for (const child of node.children) {
      const found = visit(child);
      if (found) return found;
    }
    return null;
  };
  return visit(container);
}

export function actionButton(container, label) {
  const visit = (node) => {
    if (node.tagName === "BUTTON" && node.textContent === label) return node;
    for (const child of node.children) {
      const found = visit(child);
      if (found) return found;
    }
    return null;
  };
  return visit(container);
}
