// ComfyUI-COF: the preset and character dropdowns of "COF Load Character" show only the selected file's
// presets (and, in a file with several characters, only the selected character's).
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const DEFAULT = "(default)";
const cache = {};

async function info(file) {
  if (!file) return null;
  if (cache[file]) return cache[file];
  try {
    const r = await api.fetchApi(`/cof/info?file=${encodeURIComponent(file)}`);
    if (!r.ok) return null;
    return (cache[file] = await r.json());
  } catch (e) { return null; }
}

function setValues(widget, values) {
  widget.options.values = values;
  if (!values.includes(widget.value)) widget.value = values[0];
}

async function refresh(node) {
  const W = n => node.widgets?.find(w => w.name === n);
  const fileW = W("cof_file"), presetW = W("preset"), charW = W("character");
  if (!fileW || !presetW || !charW) return;
  const d = await info(fileW.value);
  if (!d) return;
  setValues(charW, [DEFAULT, ...d.characters]);
  const ch = charW.value === DEFAULT ? d.default_character : charW.value;
  const mine = d.presets.filter(p => !p.character || !ch || p.character === ch).map(p => p.value);
  setValues(presetW, [DEFAULT, ...mine]);
  node.setDirtyCanvas?.(true, true);
}

app.registerExtension({
  name: "cof.LoadCharacter.dropdowns",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== "COFLoadCharacter") return;
    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = created?.apply(this, arguments);
      for (const name of ["cof_file", "character"]) {
        const w = this.widgets?.find(x => x.name === name);
        if (!w) continue;
        const cb = w.callback;
        w.callback = (...a) => { const out = cb?.apply(w, a); refresh(this); return out; };
      }
      setTimeout(() => refresh(this), 50);
      return r;
    };
    const configured = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const r = configured?.apply(this, arguments);
      setTimeout(() => refresh(this), 50);
      return r;
    };
  },
});
