"""Builds workflows/cof_preview.json (UI format) and workflows/cof_preview_api.json (API format):
one node per category of the COF builder, with the builder's icons. Run: python tools/build_workflows.py"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTE = """## Read me — how this workflow works

**One node per category of the COF builder**: 📝 Personality · 🖼️ Appearance · 🧬 Identity weights · 🧊 3D · 👄 Voice · 🏃 Postures · 🎥 Motion · 📁 Files.

- **The nodes are a fixed template.** They do not appear or disappear with the file's content: only their **dropdown lists** follow the loaded file, preset and character.
- **Another type** (face, body, hair, accessory…): change **kind** in the node. The list shows only the types this preset has, with their number of images or files; a node keeps its type, shown "— 0 images" if this preset has none.
- **Add a node**: double-click an empty spot → type `COF` → choose the node → drag the **cof** output of *COF Load Character* to its **cof** input → connect a preview → Run.
- **pick**: `mosaic_all` = every image (24 at most; beyond, `mosaic_page_1`, `_2`…), `image_001`… = one image; `all` / `item_001`… for files.
- **Missing content** (no voice, no 3D…) gives silence, a blank image or an empty list, and says so: the run goes on.
- 🧬 **Identity weights** (LoRA, RefMods) need a model: see `cof_txt2img_api.json`.

More: github.com/yannsouetre/ComfyUI-COF"""

nodes, links, api = [], [], {}
def node(nid, typ, pos, size, widgets=None, title=None, outputs=(), inputs=()):
    n = {"id": nid, "type": typ, "pos": list(pos), "size": list(size), "flags": {}, "order": len(nodes), "mode": 0,
         "inputs": [{"name": a, "type": t, "link": None} for a, t in inputs],
         "outputs": [{"name": a, "type": t, "links": [], "slot_index": i} for i, (a, t) in enumerate(outputs)],
         "properties": {"Node name for S&R": typ}}
    if widgets is not None:
        n["widgets_values"] = widgets
    if title:
        n["title"] = title
    nodes.append(n)
    return n
def link(src, slot, dst, inp):
    lid = len(links) + 1
    s = next(n for n in nodes if n["id"] == src); d = next(n for n in nodes if n["id"] == dst)
    t = s["outputs"][slot]["type"]
    s["outputs"][slot]["links"].append(lid)
    i = next(k for k, x in enumerate(d["inputs"]) if x["name"] == inp); d["inputs"][i]["link"] = lid
    links.append([lid, src, slot, dst, i, t])

IMG, STR = ("images", "IMAGE"), ("source", "*")
LOAD_OUT = [("cof", "COF"), ("primary_image", "IMAGE"), ("references", "IMAGE"), ("prompt", "STRING"), ("info", "STRING")]
node(1, "COFLoadCharacter", (0, 0), (380, 250), ["clara-mercier.cof", "(default)", "(default)", 1024, False], outputs=LOAD_OUT)
node(2, "PreviewImage", (560, 0), (300, 440), title="Primary image", inputs=[IMG]); link(1, 1, 2, "images")
node(3, "PreviewImage", (900, 0), (520, 440), title="References (batch)", inputs=[IMG]); link(1, 2, 3, "images")
node(4, "PreviewAny", (1800, 0), (420, 210), title="Prompt", inputs=[STR]); link(1, 3, 4, "source")
node(5, "PreviewAny", (1800, 240), (420, 210), title="Info and rights", inputs=[STR]); link(1, 4, 5, "source")
node(6, "MarkdownNote", (0, 500), (2240, 560), [NOTE], title="Read me", )
nodes[-1].update({"color": "#0f6b5c", "bgcolor": "#18201f"})
nodes[-1]["properties"]["cof_readme"] = True        # web/cof.js shows this note's text at twice the usual size

IMGOUT = [("images", "IMAGE"), ("count", "INT"), ("labels", "STRING")]
FILEOUT = [("paths", "STRING"), ("labels", "STRING"), ("count", "INT")]
COF_IN = [("cof", "COF")]
Y = [1130, 1570, 2030, 2470]
# laid out in the builder's order, left to right then top to bottom:
# Personality, Appearance (outfits), Appearance (a place), 3D, Voice, Postures, Motion, Files
node(10, "COFPersonality", (0, Y[0]), (380, 80), inputs=COF_IN, outputs=[("persona", "STRING"), ("language", "STRING")]); link(1, 0, 10, "cof")
node(11, "PreviewAny", (560, Y[0]), (520, 320), title="Personality", inputs=[STR]); link(10, 0, 11, "source")
node(12, "COFAppearance", (1240, Y[0]), (380, 230), ["clothing", True, "mosaic_all", "any", "any"], inputs=COF_IN, outputs=IMGOUT); link(1, 0, 12, "cof")
node(13, "PreviewImage", (1800, Y[0]), (440, 380), inputs=[IMG]); link(12, 0, 13, "images")
node(20, "COFAppearance", (0, Y[1]), (380, 230), ["environment", True, "image_001", "any", "any"], inputs=COF_IN, outputs=IMGOUT); link(1, 0, 20, "cof")
node(21, "PreviewImage", (560, Y[1]), (440, 260), inputs=[IMG]); link(20, 0, 21, "images")
node(22, "PreviewAny", (560, Y[1] + 290), (520, 130), title="Labels", inputs=[STR]); link(20, 2, 22, "source")
node(23, "COF3D", (1240, Y[1]), (380, 110), ["all"], inputs=COF_IN, outputs=FILEOUT); link(1, 0, 23, "cof")
node(24, "PreviewAny", (1800, Y[1]), (440, 160), title="3D: labels", inputs=[STR]); link(23, 1, 24, "source")
node(30, "COFVoice", (0, Y[2]), (380, 110), ["sample_001"], inputs=COF_IN, outputs=[("audio", "AUDIO"), ("transcript", "STRING"), ("labels", "STRING")]); link(1, 0, 30, "cof")
node(31, "PreviewAudio", (560, Y[2]), (520, 120), inputs=[("audio", "AUDIO")]); link(30, 0, 31, "audio")
node(32, "PreviewAny", (560, Y[2] + 150), (520, 150), title="Transcript", inputs=[STR]); link(30, 1, 32, "source")
node(33, "COFPostures", (1240, Y[2]), (380, 150), ["all", True, "mosaic_all"], inputs=COF_IN, outputs=IMGOUT); link(1, 0, 33, "cof")
node(34, "PreviewImage", (1800, Y[2]), (440, 360), inputs=[IMG]); link(33, 0, 34, "images")
node(40, "COFMotion", (0, Y[3]), (380, 110), ["all"], inputs=COF_IN, outputs=FILEOUT); link(1, 0, 40, "cof")
node(41, "PreviewAny", (560, Y[3]), (520, 240), title="Motion: labels", inputs=[STR]); link(40, 1, 41, "source")
node(42, "COFFiles", (1240, Y[3]), (380, 130), ["all", "all"], inputs=COF_IN, outputs=[("text", "STRING"), ("paths", "STRING"), ("labels", "STRING"), ("count", "INT")]); link(1, 0, 42, "cof")
node(43, "PreviewAny", (1800, Y[3]), (440, 320), title="Files: text", inputs=[STR]); link(42, 0, 43, "source")

ui = {"last_node_id": max(n["id"] for n in nodes), "last_link_id": len(links), "nodes": nodes, "links": links,
      "groups": [{"title": "Load", "bounding": [-20, -60, 420, 330], "color": "#0f6b5c", "font_size": 24},
                 {"title": "One node per category of the COF builder", "bounding": [-20, 1070, 2300, 1780], "color": "#2a3533", "font_size": 24}],
      "config": {}, "extra": {"ds": {"scale": 0.55, "offset": [60, 80]}}, "version": 0.4}
(ROOT / "workflows" / "cof_preview.json").write_text(json.dumps(ui, ensure_ascii=False, indent=1), encoding="utf-8")

# API format: same graph without the note and the previews' layout
NAMES = {"COFLoadCharacter": ["cof_file", "preset", "character", "max_size", "allow_intimate"],
         "COFAppearance": ["kind", "include_offered", "pick", "subject", "angle"], "COFPostures": ["kind", "include_offered", "pick"],
         "COFVoice": ["sample"], "COFMotion": ["pick"], "COF3D": ["pick"], "COFFiles": ["kind", "pick"]}
for n in nodes:
    if n["type"] == "MarkdownNote":
        continue
    inputs = dict(zip(NAMES.get(n["type"], []), n.get("widgets_values", [])))
    for i in n["inputs"]:
        l = next(x for x in links if x[0] == i["link"])
        inputs[i["name"]] = [str(l[1]), l[2]]
    api[str(n["id"])] = {"class_type": n["type"], "inputs": inputs, **({"_meta": {"title": n["title"]}} if n.get("title") else {})}
(ROOT / "workflows" / "cof_preview_api.json").write_text(json.dumps(api, ensure_ascii=False, indent=1), encoding="utf-8")
print(len(nodes), "nodes,", len(links), "links")
