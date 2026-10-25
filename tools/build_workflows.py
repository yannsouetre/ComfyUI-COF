"""Builds workflows/cof_preview.json (UI format) and workflows/cof_preview_api.json (API format):
one node per category of the COF builder, with the builder's icons. Run: python tools/build_workflows.py"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTE = """## Read me — how this workflow works

**One node per category of the COF builder**, each in its own group below: 📝 Personality · 🖼️ Appearance · 🧊 3D · 👄 Voice · 🏃 Postures · 🎥 Motion · 📁 Files — and 🕰️ Memories (COF 1.1) last.

- **The nodes are a fixed template**: only their **dropdown lists** follow the loaded file, preset and character. Another type (face, body, hair…): change **kind**; a node keeps its type, shown "— 0 images" if this preset has none.
- **Add a node**: double-click an empty spot → type `COF` → choose the node → drag the **cof** output of *COF Load Character* to its **cof** input → connect a preview → Run.
- **pick**: `mosaic_all` = every image (24 at most, then `mosaic_page_1`, `_2`…), `image_001`… = one image; `all` / `item_001`… for files.
- **Missing content** gives silence, a blank image or an empty list, and says so: the run goes on. 🧬 Identity weights (LoRA, RefMods) need a model: see `cof_txt2img_api.json`.

More: github.com/yannsouetre/ComfyUI-COF"""

MEMNOTE = """## 🕰️ COF Memories — COF 1.1

The character's memories in timeline order, as **one text** for a language model (the gist in the preset's language, a when-marker, nature / disclosure / fidelity, the remembered lines) and **their images** as a batch.

- Needs a **1.1 file**: the COF repository's `spec/examples/10-clara-mercier` has sixteen (five in the default preset, nine at 65). A 1.0 file gives an empty text and a count of 0.
- **include personal** is off: a user's personal memories never come out unless asked. The media of an explicit memory come only when the loader allows adult content.
- Collapse any group with the dot at its top left; collapse a node with the dot in its title."""

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
node(4, "PreviewAny", (1640, 0), (420, 210), title="Prompt", inputs=[STR]); link(1, 3, 4, "source")
node(5, "PreviewAny", (1640, 240), (420, 210), title="Info and rights", inputs=[STR]); link(1, 4, 5, "source")
node(6, "MarkdownNote", (0, 480), (2060, 300), [NOTE], title="Read me", )
nodes[-1].update({"color": "#0f6b5c", "bgcolor": "#18201f"})
nodes[-1]["properties"]["cof_readme"] = True        # web/cof.js shows this note's text at twice the usual size

IMGOUT = [("images", "IMAGE"), ("count", "INT"), ("labels", "STRING")]
FILEOUT = [("paths", "STRING"), ("labels", "STRING"), ("count", "INT")]
COF_IN = [("cof", "COF")]

# ---- layout: groups stacked in two columns; inside a group, nodes at relative positions; group bounds from node sizes
# (title bar ≈ 30 px above pos, not counted in size; previews under-report their height: give them room)
TITLE, PAD, GAP, COLX = 30, 24, 70, (0, 1120)
GROUPS, cursor = [], [-20 + 0, 0]
col_y = [860, 860]                       # first row of groups, under the Read me
def place(title, color, items, col):
    """items: (nid, type, (dx, dy), size, widgets, title, inputs, outputs, link_from) — positions relative to the group's top left."""
    gx, gy = COLX[col], col_y[col]
    made = []
    for nid, typ, (dx, dy), size, widgets, ttl, inputs, outputs, lf in items:
        n = node(nid, typ, (gx + PAD + dx, gy + TITLE + PAD + dy), size, widgets, title=ttl, inputs=inputs, outputs=outputs)
        made.append(n)
        for src, slot, inp in lf:
            link(src, slot, nid, inp)
    right = max(n["pos"][0] + n["size"][0] for n in made) + PAD
    bottom = max(n["pos"][1] + n["size"][1] for n in made) + PAD
    GROUPS.append({"title": title, "bounding": [gx, gy, right - gx, bottom - gy], "color": color, "font_size": 22})
    col_y[col] = bottom + GAP
    return bottom

S, P, PI = (380, 0), (520, 0), (440, 0)  # widths: source node, text preview, image preview
def src(nid, typ, dy, h, widgets, outputs, extra_inputs=()):
    return (nid, typ, (0, dy), (380, h), widgets, None, COF_IN + list(extra_inputs), outputs, [(1, 0, "cof")])
def txt(nid, dx, dy, h, title, from_, w=520):
    return (nid, "PreviewAny", (dx, dy), (w, h), None, title, [STR], (), [(from_[0], from_[1], "source")])
def img(nid, dx, dy, h, title, from_, w=440):
    return (nid, "PreviewImage", (dx, dy), (w, h), None, title, [IMG], (), [(from_[0], from_[1], "images")])

place("📝 Personality", "#2a3533", [src(10, "COFPersonality", 0, 60, None, [("persona", "STRING"), ("language", "STRING")]), txt(11, 420, 0, 330, "Personality", (10, 0))], 0)
place("🖼️ Appearance — outfits", "#2a3533", [src(12, "COFAppearance", 0, 200, ["clothing", True, "mosaic_all", "any", "any"], IMGOUT), img(13, 420, 0, 330, "Outfits", (12, 0), 520)], 1)
place("🖼️ Appearance — a place", "#2a3533", [src(20, "COFAppearance", 0, 200, ["environment", True, "image_001", "any", "any"], IMGOUT), img(21, 420, 0, 290, "Place", (20, 0), 520)], 0)
place("🧊 3D", "#2a3533", [src(23, "COF3D", 0, 80, ["all"], FILEOUT), txt(24, 420, 0, 120, "3D: files", (23, 1))], 1)
place("👄 Voice", "#2a3533", [src(30, "COFVoice", 0, 80, ["sample_001"], [("audio", "AUDIO"), ("transcript", "STRING"), ("labels", "STRING")]),
                             (31, "PreviewAudio", (420, 0), (520, 110), None, "Voice sample", [("audio", "AUDIO")], (), [(30, 0, "audio")]),
                             txt(32, 420, 150, 110, "Transcript", (30, 1))], 0)
place("🏃 Postures", "#2a3533", [src(33, "COFPostures", 0, 120, ["all", True, "mosaic_all"], IMGOUT), img(34, 420, 0, 300, "Postures", (33, 0), 520)], 1)
place("🎥 Motion", "#2a3533", [src(40, "COFMotion", 0, 80, ["all"], FILEOUT), txt(41, 420, 0, 180, "Motion: clips", (40, 1))], 0)
place("📁 Files", "#2a3533", [src(42, "COFFiles", 0, 100, ["all", "all"], [("text", "STRING"), ("paths", "STRING"), ("labels", "STRING"), ("count", "INT")]), txt(43, 420, 0, 240, "Files: text", (42, 0))], 1)

# memories: full width, under both columns
col_y[0] = col_y[1] = max(col_y)
node(7, "MarkdownNote", (COLX[0], col_y[0] + TITLE), (2060, 230), [MEMNOTE], title="Read me — Memories (COF 1.1)")
nodes[-1].update({"color": "#0f6b5c", "bgcolor": "#18201f"}); nodes[-1]["properties"]["cof_readme"] = True
MEM_OUT = [("text", "STRING"), ("images", "IMAGE"), ("audio", "AUDIO"), ("media_paths", "STRING"), ("labels", "STRING"), ("count", "INT")]
ytop = col_y[0] + TITLE + 230 + 40
items = [(50, "COFMemories", (0, ytop - col_y[0] - TITLE - PAD), (380, 230), ["all", "all", False, True], None, COF_IN, MEM_OUT, [(1, 0, "cof")]),
         txt(51, 420, ytop - col_y[0] - TITLE - PAD, 380, "Memories: text", (50, 0), 560),
         img(52, 1020, ytop - col_y[0] - TITLE - PAD, 380, "Memories: images", (50, 1), 480),
         (53, "PreviewAudio", (1540, ytop - col_y[0] - TITLE - PAD), (480, 110), None, "Memories: sound", [("audio", "AUDIO")], (), [(50, 2, "audio")]),
         txt(54, 1540, ytop - col_y[0] - TITLE - PAD + 150, 110, "Memories: video and sound files", (50, 3), 480),
         txt(55, 0, ytop - col_y[0] - TITLE - PAD + 270, 110, "Memories: labels", (50, 4), 380)]
# the memories group starts at the note: place() puts nodes relative to col_y; the note sits inside the same group
g0 = len(GROUPS)
place("🕰️ Memories (COF 1.1)", "#0f6b5c", items, 0)
g = GROUPS[g0]; g["bounding"][1] = col_y[1] if False else g["bounding"][1]
# widen the memories group to both columns and include the note (which sits at its top)
g["bounding"][2] = COLX[1] + 1120 - COLX[0] - 60 + 20

GROUPS.insert(0, {"title": "Load", "bounding": [-20, -60, 420, 330], "color": "#0f6b5c", "font_size": 22})
ui = {"last_node_id": max(n["id"] for n in nodes), "last_link_id": len(links), "nodes": nodes, "links": links,
      "groups": GROUPS,
      "config": {}, "extra": {"ds": {"scale": 0.55, "offset": [60, 80]}}, "version": 0.4}
(ROOT / "workflows" / "cof_preview.json").write_text(json.dumps(ui, ensure_ascii=False, indent=1), encoding="utf-8")

# API format: same graph without the note and the previews' layout
NAMES = {"COFLoadCharacter": ["cof_file", "preset", "character", "max_size", "allow_intimate"],
         "COFAppearance": ["kind", "include_offered", "pick", "subject", "angle"], "COFPostures": ["kind", "include_offered", "pick"],
         "COFVoice": ["sample"], "COFMotion": ["pick"], "COF3D": ["pick"], "COFFiles": ["kind", "pick"], "COFMemories": ["pick", "media", "include_personal", "include_text_media"]}
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
