// ComfyUI-COF: dropdowns filled from the .cof file itself.
// - COF Load Character: the presets of the selected file (and character).
// - the category nodes (COF Appearance, Postures, Voice, 3D, Motion, Files): the matching types, images, samples
//   or files by name, read from the COF Load Character node they are connected to.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const DEFAULT = "(default)";
const cache = {};

async function getJSON(url) {
  if (cache[url]) return cache[url];
  try {
    const r = await api.fetchApi(url);
    if (!r.ok) return null;
    return (cache[url] = await r.json());
  } catch (e) { return null; }
}
const W = (node, n) => node.widgets?.find(w => w.name === n);
const token = v => String(v ?? "").split(" — ")[0].trim();

function setValues(widget, values) {
  if (!widget || !values?.length) return;
  widget.options.values = values;
  // keep the same choice when it still exists (compare the token before " — ")
  const same = values.find(v => token(v) === token(widget.value));
  widget.value = same ?? values[0];
}

function linkOf(id) {
  const L = app.graph?.links;
  return L ? (typeof L.get === "function" ? L.get(id) : L[id]) : null;
}

// the COF Load Character node feeding this node's "cof" input
function sourceLoader(node) {
  const inp = node.inputs?.find(i => i.name === "cof");
  if (!inp || inp.link == null) return null;
  const link = linkOf(inp.link);
  const src = link && app.graph.getNodeById(link.origin_id);
  return src && src.comfyClass === "COFLoadCharacter" ? src : null;
}

async function refreshLoader(node) {
  const fileW = W(node, "cof_file"), presetW = W(node, "preset"), charW = W(node, "character");
  if (!fileW || !presetW || !charW) return;
  const d = await getJSON(`/cof/info?file=${encodeURIComponent(fileW.value)}`);
  if (!d) return;
  setValues(charW, [DEFAULT, ...d.characters]);
  const ch = charW.value === DEFAULT ? d.default_character : charW.value;
  setValues(presetW, [DEFAULT, ...d.presets.filter(p => !p.character || !ch || p.character === ch).map(p => p.value)]);
  node.setDirtyCanvas?.(true, true);
  // update the pickers connected to this loader
  for (const n of app.graph?._nodes || []) if (PICKERS[n.comfyClass] && sourceLoader(n) === node) refreshPicker(n);
}

// one picker per category node: which widget it fills, from which route, with which category
const imageParams = n => ({ kind: token(W(n, "kind")?.value), offered: W(n, "include_offered")?.value ? "true" : "false",
  subject: W(n, "subject")?.value ?? "any", angle: W(n, "angle")?.value ?? "any" });
const PICKERS = {
  COFAppearance: { widget: "pick", node: "images", cat: "appearance", kinds: true, params: imageParams, watch: ["kind", "include_offered", "subject", "angle"] },
  COFPostures: { widget: "pick", node: "images", cat: "posture", kinds: true, params: imageParams, watch: ["kind", "include_offered"] },
  COFVoice: { widget: "sample", node: "voice", params: () => ({}), watch: [] },
  COF3D: { widget: "pick", node: "media", cat: "volume3d", params: () => ({}), watch: [] },
  COFMotion: { widget: "pick", node: "media", cat: "motion", params: () => ({}), watch: [] },
  COFFiles: { widget: "pick", node: "media", cat: "files", kinds: true, params: n => ({ kind: token(W(n, "kind")?.value) }), watch: ["kind"] },
};

async function refreshPicker(node) {
  const spec = PICKERS[node.comfyClass], src = sourceLoader(node);
  if (!spec || !src) return;
  const base = {
    file: W(src, "cof_file")?.value ?? "", preset: W(src, "preset")?.value ?? "",
    character: W(src, "character")?.value ?? "", allow: W(src, "allow_intimate")?.value ? "true" : "false" };
  if (spec.kinds) {                       // first the kinds this preset really has, with their counts
    const k = await getJSON(`/cof/choices?${new URLSearchParams({ node: "kinds", cat: spec.cat, ...base,
      offered: W(node, "include_offered")?.value ? "true" : "false", current: token(W(node, "kind")?.value) })}`);
    if (k?.values) setValues(W(node, "kind"), k.values);
  }
  const q = new URLSearchParams({ node: spec.node, ...(spec.cat ? { cat: spec.cat } : {}), ...base, ...spec.params(node) });
  const d = await getJSON(`/cof/choices?${q}`);
  if (d?.values) { setValues(W(node, spec.widget), d.values); node.setDirtyCanvas?.(true, true); }
}

function hook(node, names, fn) {
  for (const name of names) {
    const w = W(node, name);
    if (!w) continue;
    const cb = w.callback;
    w.callback = (...a) => { const out = cb?.apply(w, a); fn(node); return out; };
  }
}

// the Read me note of the COF workflows (property cof_readme): text twice the size set in ComfyUI's settings
function bigNote(node, tries = 10) {
  if (!node?.properties?.cof_readme) return;
  const w = node.widgets?.find(x => x.inputEl || x.element);
  const el = w?.inputEl || w?.element;
  const tip = el?.querySelector?.(".tiptap") || el;
  if (!tip || tip.dataset.cofBig) return;
  const px = parseFloat(getComputedStyle(tip).fontSize);
  if (!px) { if (tries > 0) setTimeout(() => bigNote(node, tries - 1), 200); return; }   // not in the page yet
  tip.style.fontSize = `${px * 2}px`;
  tip.dataset.cofBig = "1";
}

app.registerExtension({
  name: "cof.dropdowns",
  nodeCreated(node) { setTimeout(() => bigNote(node), 300); },
  loadedGraphNode(node) { setTimeout(() => bigNote(node), 300); },
  async beforeRegisterNodeDef(nodeType, nodeData) {
    const isLoader = nodeData.name === "COFLoadCharacter", spec = PICKERS[nodeData.name];
    if (!isLoader && !spec) return;
    const refresh = isLoader ? refreshLoader : refreshPicker;
    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = created?.apply(this, arguments);
      hook(this, isLoader ? ["cof_file", "character", "preset", "allow_intimate"] : spec.watch, refresh);
      setTimeout(() => refresh(this), 60);
      return r;
    };
    const configured = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const r = configured?.apply(this, arguments);
      setTimeout(() => refresh(this), 120);
      return r;
    };
    if (spec) {
      const changed = nodeType.prototype.onConnectionsChange;
      nodeType.prototype.onConnectionsChange = function () {
        const r = changed?.apply(this, arguments);
        setTimeout(() => refresh(this), 30);
        return r;
      };
    }
  },
});
