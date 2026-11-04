// ComfyUI-COF: dropdowns filled from the .cof file itself.
// - COF Load Character: the presets of the selected file (and character).
// - the category nodes (COF Appearance, Postures, Voice, 3D, Motion, Files, Memories, Psyche): the matching types, images,
//   samples, files, memories or gist languages by name, read from the COF Load Character node they are connected to.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const DEFAULT = "(default)";
// Answers of /cof/* are kept for a few seconds (a node asks the same list several times while a graph loads), never for the
// life of the page: a .cof rewritten under the same name must show its new presets. The cache is also emptied when the user
// changes the loader's file / preset / character, and when ComfyUI's node definitions are refreshed (R).
const TTL = 8000;
const cache = {};
const dropCache = () => { for (const k of Object.keys(cache)) delete cache[k]; };

async function getJSON(url) {
  const hit = cache[url];
  if (hit && Date.now() - hit.t < TTL) return hit.v;
  try {
    const r = await api.fetchApi(url, { cache: "no-store" });
    if (!r.ok) return null;
    const v = await r.json();
    cache[url] = { t: Date.now(), v };
    return v;
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
  COFMemories: { widget: "pick", node: "memories", params: n => ({ personal: String(!!W(n, "include_personal")?.value) }), watch: ["include_personal", "pick"],
                 extras: [{ widget: "media", node: "memory-media", params: n => ({ personal: String(!!W(n, "include_personal")?.value), pick: token(W(n, "pick")?.value) }) }] },
  COFPsyche: { widget: "language", node: "psyche", params: n => ({ personal: String(!!W(n, "include_personal")?.value) }), watch: ["include_personal"] },
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
  for (const x of spec.extras || []) {   // a second list that depends on the first (the images of the chosen memory)
    const e = await getJSON(`/cof/choices?${new URLSearchParams({ node: x.node, ...base, ...x.params(node) })}`);
    if (e?.values) { setValues(W(node, x.widget), e.values); node.setDirtyCanvas?.(true, true); }
  }
}

function hook(node, names, fn) {
  for (const name of names) {
    const w = W(node, name);
    if (!w) continue;
    const cb = w.callback;
    w.callback = (...a) => { const out = cb?.apply(w, a); fn(node); return out; };
  }
}

// COF Psyche: drop the illustration preview an earlier run left on the node
function clearPreview(node) {
  if (!node.imgs?.length) return;
  node.imgs = undefined;
  node.imageIndex = null;
  node.setDirtyCanvas?.(true, true);
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

// R (refresh node definitions / combo lists): forget the cached answers and refill every loader's lists
function onRefreshCombos() {
  dropCache();
  for (const n of app.graph?._nodes || []) if (n.comfyClass === "COFLoadCharacter") refreshLoader(n);
}

app.registerExtension({
  name: "cof.dropdowns",
  setup() {
    const rc = app.refreshComboInNodes;
    if (typeof rc === "function" && !rc.cofWrapped) {
      const wrapped = async function () { const r = await rc.apply(this, arguments); onRefreshCombos(); return r; };
      wrapped.cofWrapped = true;
      app.refreshComboInNodes = wrapped;
    }
  },
  nodeCreated(node) { setTimeout(() => bigNote(node), 300); },
  loadedGraphNode(node) { setTimeout(() => bigNote(node), 300); },
  async beforeRegisterNodeDef(nodeType, nodeData) {
    const isLoader = nodeData.name === "COFLoadCharacter", spec = PICKERS[nodeData.name];
    if (!isLoader && !spec) return;
    const refresh = isLoader ? n => { dropCache(); return refreshLoader(n); } : refreshPicker;   // a loader's change = fresh answers
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
    if (nodeData.name === "COFPsyche") {
      // illustrations: ComfyUI's frontend keeps node.imgs when a run sends no image, so the node would go on showing an illustration
      // its output no longer has. Cleared when a run sends an empty `ui.images` (no illustrated node in the selection) and when
      // show_illustrations is unticked (the node then sends no preview at all).
      const executed = nodeType.prototype.onExecuted;
      nodeType.prototype.onExecuted = function (message) {
        const r = executed?.apply(this, arguments);
        if (!message?.images?.length) clearPreview(this);
        return r;
      };
      const made = nodeType.prototype.onNodeCreated;
      nodeType.prototype.onNodeCreated = function () {
        const r = made?.apply(this, arguments);
        hook(this, ["show_illustrations"], n => { if (!W(n, "show_illustrations")?.value) clearPreview(n); });
        return r;
      };
    }
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
