"""Run: python -m pytest tests (outside ComfyUI: torch and comfy are stubbed)."""
import importlib, json, shutil, sys, types, zipfile
from pathlib import Path
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = Path(__file__).parent / "samples"


@pytest.fixture(scope="session")
def pack():
    t = types.ModuleType("torch"); t.from_numpy = lambda a: a; t.zeros = lambda shape, dtype=None: np.zeros(shape, np.float32); t.float32 = np.float32
    sys.modules.setdefault("torch", t)
    applied = []
    comfy = types.ModuleType("comfy"); sd = types.ModuleType("comfy.sd"); ut = types.ModuleType("comfy.utils")
    sd.load_lora_for_models = lambda m, c, lora, a, b: (applied.append(a) or (m + "+lora", c + "+lora"))
    ut.load_torch_file = lambda p, safe_load=True: {"p": p}
    comfy.sd, comfy.utils = sd, ut
    sys.modules.update({"comfy": comfy, "comfy.sd": sd, "comfy.utils": ut})
    spec = importlib.util.spec_from_file_location("comfyui_cof", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    m = importlib.util.module_from_spec(spec); sys.modules["comfyui_cof"] = m; spec.loader.exec_module(m)
    return importlib.import_module("comfyui_cof.nodes"), applied


def test_load_clara_default_and_facets(pack):
    nodes, _ = pack
    cof, prim, refs, prompt, info = nodes.COFLoadCharacter().load(str(SAMPLES / "clara-mercier.cof"), "", "", 512)
    assert prim.shape[0] == 1 and prim.shape[-1] == 3 and prim.max() <= 1.0
    assert refs.shape[0] >= 1 and "Clara" in prompt and "29 years old" in prompt
    assert cof["preset"] == "default" and "rights:" in info
    c2 = nodes.COFLoadCharacter().load(str(SAMPLES / "clara-mercier.cof"), "Clara at 65 (EN)", "", 256)
    assert c2[0]["preset"] == "en-65" and "65 years old" in c2[3]
    imgs, n, _ = nodes.COFAppearance().pick_images(c2[0], "clothing", True)
    assert n >= 1 and imgs.shape[0] == n


def test_cast_and_unknowns(pack):
    nodes, _ = pack
    out = nodes.COFLoadCharacter().load(str(SAMPLES / "clara-martien.cof"), "", "Martien", 256)
    assert "Martien" in out[4].splitlines()[0]
    with pytest.raises(Exception, match="unknown preset"):
        nodes.COFLoadCharacter().load(str(SAMPLES / "clara-mercier.cof"), "nope", "", 256)


def test_refmod_and_lora(pack, tmp_path):
    nodes, applied = pack
    out = nodes.COFLoadCharacter().load(str(SAMPLES / "refmod.cof"), "", "", 256)
    paths, bases = nodes.COFReferenceLatents().export(out[0], "refmods")
    assert paths.endswith(".safetensors") and "MiniMax H3" in bases and Path(paths).is_file()
    m, c, words = nodes.COFApplyLoRA().apply("M", "C", out[0], -1.0)
    assert (m, c, words) == ("M", "C", "")          # reference-latent is not a LoRA


def test_intimate_never_without_rights(pack):
    from comfyui_cof.cof_reader import COF
    c = COF(SAMPLES / "mixed-adult-minor.cof")
    assert not c.intimate_allowed("young", True)
    imgs = c.images("default", kinds=None, allow_intimate=False)
    assert all(not a.startswith("a-i1") for a in imgs)
    assert any(a == "a-i1-placeholder" for a in c.images("default", kinds=None, allow_intimate=True))


def test_nudity_needs_opt_in(pack):
    from comfyui_cof.cof_reader import COF
    c = COF(SAMPLES / "mixed-adult-minor.cof")
    c.m["elements"]["appearance"]["variants"]["b1"]["nudity"] = True
    on = c.images("default", kinds={"body"}, allow_intimate=True); off = c.images("default", kinds={"body"})
    assert on and not off
    assert not c.nudity_allowed("young", True)


def test_dropdown_values_and_index(pack):
    nodes, _ = pack
    assert nodes._pick("(default)") == "" and nodes._pick("en-65 — Clara at 65 (EN)") == "en-65" and nodes._pick("en-65") == "en-65"
    assert nodes._choices() == (["(default)"], ["(default)"])               # no ComfyUI folders in tests
    c = nodes.COFLoadCharacter().load(str(SAMPLES / "clara-mercier.cof"), "en-65 — Clara at 65 (EN)", "(default)", 256)
    assert c[0]["preset"] == "en-65"
    d = nodes.COFLoadCharacter().load(str(SAMPLES / "clara-mercier.cof"), "(default)", "(default)", 256)[0]
    allimg, n, labels = nodes.COFAppearance().pick_images(d, "environment", False)
    assert n == 2 and allimg.shape[0] == 2 and labels.splitlines()[1].startswith("image_002: ") and "mobile" in labels
    one, n2, _ = nodes.COFAppearance().pick_images(d, "environment — 2 images", False, "image_002 — Bright studio")
    assert n2 == 2 and one.shape[0] == 1


def test_persona_motion_voice(pack, tmp_path, monkeypatch):
    nodes, _ = pack
    d = nodes.COFLoadCharacter().load(str(SAMPLES / "clara-mercier.cof"), "", "", 256)[0]
    text, lang = nodes.COFPersonality().text(d)
    assert "Clara" in text and lang == "en"
    monkeypatch.chdir(tmp_path)
    paths, labels, n = nodes.COFMotion().export(d, "item_001 — walk")
    assert n >= 1 and paths.endswith(".bvh") and Path(paths).is_file() and labels.startswith("item_001: ")

    class W:
        def unsqueeze(self, i): return "batched"
    monkeypatch.setattr(nodes, "_load_audio", lambda p: (W(), 44100))
    audio, transcript, vlabels = nodes.COFVoice().voice(d, "sample_001")
    assert audio == {"waveform": "batched", "sample_rate": 44100} and vlabels.startswith("sample_001: ")


def test_choices_pages_and_limits(pack):
    nodes, _ = pack
    v = nodes._image_choices([f"img {i}" for i in range(70)])
    assert [x.split(" — ")[0] for x in v[:3]] == ["mosaic_page_1", "mosaic_page_2", "mosaic_page_3"]
    assert v[0] == "mosaic_page_1 — images 1–24 of 70" and v[2] == "mosaic_page_3 — images 49–70 of 70" and v[3].startswith("image_001 — img 0")
    items = list(range(70))
    assert nodes._select(items, "mosaic_page_3") == list(range(48, 70)) and nodes._select(items, "image_070 — img 69") == [69]
    with pytest.raises(Exception, match="24 at most"):
        nodes._select(items, "mosaic_all")
    with pytest.raises(Exception, match="has 70 images"):
        nodes._select(items, "image_071")
    assert nodes._image_choices(["a", "b"]) == ["mosaic_all — 2 images", "image_001 — a", "image_002 — b"]
    assert len(nodes._image_choices([str(i) for i in range(24)])) == 25 and nodes._select(list(range(24)), "mosaic_all") == list(range(24))


def test_routes_fill_the_dropdowns(pack, monkeypatch):
    """/cof/info and /cof/choices, called with a fake ComfyUI server."""
    import asyncio, json as _json
    nodes, _ = pack
    routes = {}

    class Routes:
        def get(self, path):
            def deco(fn):
                routes[path] = fn
                return fn
            return deco
    server = types.ModuleType("server"); server.PromptServer = types.SimpleNamespace(instance=types.SimpleNamespace(routes=Routes()))
    web = types.ModuleType("aiohttp.web"); web.json_response = lambda data, status=200: (status, data)
    aiohttp = types.ModuleType("aiohttp"); aiohttp.web = web
    monkeypatch.setitem(sys.modules, "server", server); monkeypatch.setitem(sys.modules, "aiohttp", aiohttp); monkeypatch.setitem(sys.modules, "aiohttp.web", web)
    clara, psy = str(SAMPLES / "clara-mercier.cof"), str(SAMPLES / "clara-psyche.cof")
    monkeypatch.setattr(nodes, "_files", lambda: [clara, psy])
    nodes._routes()
    call = lambda path, **q: asyncio.run(routes[path](types.SimpleNamespace(query=q)))
    st, d = call("/cof/info", file=clara)
    assert st == 200 and any(p["value"].startswith("en-65 — ") for p in d["presets"])
    st, d = call("/cof/choices", node="images", file=clara, preset="(default)", character="(default)", kind="environment", offered="false", subject="any", angle="any", allow="false")
    assert st == 200 and d["values"][0] == "mosaic_all — 2 images" and d["values"][2].startswith("image_002 — ")
    st, d = call("/cof/choices", node="kinds", file=clara, preset="(default)", offered="true", allow="false")
    toks = [v.split(" — ")[0] for v in d["values"]]
    assert toks[:2] == ["references (face, reference set, sheet, body)", "all"] and "clothing" in toks and "intimate" not in toks
    assert all(" — " in v and "image" in v for v in d["values"])
    st, d = call("/cof/choices", node="images", file=clara, preset="(default)", kind="environment — 2 images", offered="false", subject="any", angle="any", allow="false")
    assert d["values"][0] == "mosaic_all — 2 images"
    st, d = call("/cof/choices", node="voice", file=clara, preset="fr")
    assert d["values"][0].startswith("sample_001 — ") and "[fr]" in d["values"][0]
    st, d = call("/cof/choices", node="media", cat="motion", file=clara, preset="")
    assert d["values"][0].startswith("all — ") and d["values"][1].startswith("item_001 — ")
    st, _ = call("/cof/choices", node="images", file="../../etc/passwd")
    assert st == 404
    st, d = call("/cof/choices", node="kinds", cat="posture", file=clara, preset="(default)", offered="true", allow="false")
    assert st == 200 and d["values"][0] == "all — 3 images"
    st, d = call("/cof/choices", node="kinds", cat="files", file=clara, preset="en-agent", allow="false")
    assert st == 200 and "skill — 3 files" in d["values"]
    st, d = call("/cof/choices", node="media", cat="files", kind="skill", file=clara, preset="en-agent")
    assert st == 200 and d["values"][0] == "all — 3 files"
    st, d = call("/cof/choices", node="images", cat="voice", file=clara, preset="")
    assert st == 400
    st, d = call("/cof/choices", node="psyche", file=psy, preset="fr", personal="false")
    assert st == 200 and d["values"] == ["(preset) — fr", "en — 22 nodes", "fr — 22 nodes"]
    st, d = call("/cof/choices", node="psyche", file=clara, preset="")
    assert st == 200 and len(d["values"]) == 1 and d["values"][0].startswith("(preset) — ")


def test_preset_without_voice_and_other_ages(pack):
    """Martien has no voice sample and no clothing; Clara at 12 is not described as the adult designer."""
    nodes, _ = pack
    m = nodes.COFLoadCharacter().load(str(SAMPLES / "clara-martien.cof"), "(default)", "martien — Martien", 256)[0]
    audio, transcript, labels = nodes.COFVoice().voice(m, "sample_001")
    assert audio["sample_rate"] == 44100 and audio["waveform"].shape == (1, 1, 44100) and transcript == ""
    assert "no voice sample" in labels
    kinds = nodes._kind_choices(m["cof"], m["preset"], True, False, "clothing — 4 images")
    assert "clothing — 0 images" in kinds and kinds[0].startswith("references")
    from comfyui_cof.cof_reader import COF
    c = COF(str(SAMPLES / "clara-mercier.cof"))
    assert "designer" in c.prompt("default") and "designer" not in c.prompt("fr-12") and "12 years old" in c.prompt("fr-12")
    env = [c.image_label(a) for a in c.images("fr-12", kinds={"environment"})]
    assert env[0].endswith("landscape") and env[1].endswith("portrait")
    imgs, n, lab = nodes.COFAppearance().pick_images(m, "clothing — 0 images", True, "mosaic_all — 0 images")
    assert n == 0 and imgs.shape == (1, 64, 64, 3) and "no clothing image" in lab


def test_one_node_per_builder_category(pack, tmp_path, monkeypatch):
    """Appearance and Postures stay in their category; Files reads text only and keeps a skill's layout."""
    nodes, _ = pack
    assert len({n.split(" ", 1)[0] for n in nodes.NODE_DISPLAY_NAME_MAPPINGS.values()}) >= 9   # every node has its icon
    d = nodes.COFLoadCharacter().load(str(SAMPLES / "clara-mercier.cof"), "", "", 256)[0]
    app_all = nodes.COFAppearance().pick_images(d, "all", True)[1]
    poses = nodes.COFPostures().pick_images(d, "all", True)
    assert poses[1] == 3 and "pose" in poses[2].lower()
    assert all(not l.lower().startswith("image") or "(photo)" not in l for l in nodes.COFAppearance().pick_images(d, "all", True)[2].splitlines())
    kinds = nodes._kind_choices(d["cof"], d["preset"], True, False, "", "posture")
    assert kinds[0] == "all — 3 images" and "photo — 3 images" in kinds
    assert "posture" not in " ".join(nodes._kind_choices(d["cof"], d["preset"], True, False, "", "appearance"))
    monkeypatch.chdir(tmp_path)
    d = nodes.COFLoadCharacter().load(str(SAMPLES / "clara-mercier.cof"), "en-agent", "", 256)[0]
    text, paths, labels, n = nodes.COFFiles().export(d, "skill — 3 files", "all")
    assert n == 3 and "### SKILL.md" in text and paths.count("\n") == 2
    assert (tmp_path / "cof" / "cof-clara-mercier" / "files" / "f1" / "reference" / "color-math.md").is_file()
    assert nodes._kind_choices(d["cof"], d["preset"], True, False, "", "files")[:2] == ["all — 3 files", "skill — 3 files"]
    p3, l3, n3 = nodes.COF3D().export(d)
    assert n3 == 0 and "no 3D file" in l3
    m = nodes.COFLoadCharacter().load(str(SAMPLES / "clara-martien.cof"), "(default)", "martien — Martien", 256)[0]
    assert nodes.COFPostures().pick_images(m, "posture", True)[1] == 0
    assert nodes.COFFiles().export(m)[2] == "(this preset has no file)"


def test_memories_node(pack):
    """COF Memories (1.1): gists in timeline order in the preset's language, images of the chosen memories,
    personal memories left out unless asked, explicit media never without the opt-in."""
    nodes, _ = pack
    d = nodes.COFLoadCharacter().load(str(SAMPLES / "clara-memories.cof"), "fr-agent", "", 256)[0]
    text, imgs, _, _, labels, n = nodes.COFMemories().recall(d)
    assert n == 5 and labels.startswith("memory_001: ") and labels.index("grand") < labels.index("agence") < labels.index("Martian")
    assert "grand-mère" in text and "age 8" in text and text.index("huit ans") < text.index("Martien") and "guarded" in text and "fragment" in text
    assert "Mange d'abord" in text and "Eat first" not in text          # the French text media only (preset language)
    assert imgs.shape[0] >= 9                                           # three + two + two + three images, no poster
    text1, imgs1 = nodes.COFMemories().recall(d, "memory_002")[:2]
    assert "agence" in text1 and imgs1.shape[0] == 1 and imgs1.shape[1] == 64   # the period has no image: a blank one
    d_en = nodes.COFLoadCharacter().load(str(SAMPLES / "clara-memories.cof"), "en-agent", "", 256)[0]
    assert "Eat first" in nodes.COFMemories().recall(d_en)[0]
    # a 1.0 file: no memory, no crash
    d0 = nodes.COFLoadCharacter().load(str(SAMPLES / "clara-mercier.cof"), "", "", 256)[0]
    t0, i0, _, _, l0, n0 = nodes.COFMemories().recall(d0)
    assert n0 == 0 and t0 == "" and l0.startswith("(this preset has no memory)")


SOUL_ORDER = ["Wound", "Values", "Beliefs", "Wants", "Needs", "Fears", "Stances", "Tastes", "Habits", "Contradictions"]


def test_psyche_node(pack):
    """COF Psyche (1.2): Clara's 22 nodes in cof-cli's "Who she is" order, the three formats, the positions, focus on the
    Martian (the stance and the core nodes), max_nodes, the preset's language or the one asked, the persona clause;
    a 1.1 file gives empty outputs and no error."""
    nodes, _ = pack
    d = nodes.COFLoadCharacter().load(str(SAMPLES / "clara-psyche.cof"), "", "", 256)[0]
    assert d["preset"] == "default"
    soul, pos, n = nodes.COFPsyche().render(d, "soul")
    assert n == 22 and soul.startswith("## Who she is\n")
    assert [l[4:] for l in soul.splitlines() if l.startswith("### ")] == SOUL_ORDER
    assert "- Lightness: do less, better — a page should cost" in soul            # strength 95: the whole gist
    assert "- Accept being helped\n" in soul and "studio is not one person" not in soul   # strength 65: a short clause
    assert "- If I slow down, I disappear  ·  lie" in soul and "about: the Martian" in soul and "does not tell" not in soul
    assert pos.splitlines() == ["toward the Martian: protective curiosity — the one visitor who looked at the sheep before the screens.",
                                "toward design agencies: no rancour, no return."]
    lines, pos2, n2 = nodes.COFPsyche().render(d, "lines")
    rows = lines.splitlines()
    assert n2 == 22 and len(rows) == 22 and rows[0].startswith("wound · Four years") and rows[-1].startswith("contradiction · ") and pos2 == pos
    assert "need · Accept being helped — a studio is not one person at night." in rows      # lines: always the whole gist
    assert [r.split(" · ")[0] for r in rows] == sorted((r.split(" · ")[0] for r in rows), key=nodes.PSYCHE_ORDER.index)
    prose = nodes.COFPsyche().render(d, "prose")[0]
    assert prose.startswith("Values: Lightness: do less, better; Accessibility") and prose.index("Toward the Martian: protective curiosity; toward design agencies: no rancour") < prose.index("Wound:")
    assert "\n" not in prose and "sheep before the screens" not in prose              # one paragraph, short clauses
    # focus: "what she thinks of the Martian" = the stance hanging on that memory, plus every core node
    t, p, nf = nodes.COFPsyche().render(d, "lines", focus="the Martian")
    assert nf == 6 and {r.split(" · ")[0] for r in t.splitlines()} == {"wound", "value", "belief", "stance", "habit"}
    assert "about: the Martian" in t and "agencies" not in t and "Four years of shipping" in t and p == pos.splitlines()[0]
    assert nodes.COFPsyche().render(d, "lines", focus="martien")[2] == 6             # accents and case do not matter
    assert nodes.COFPsyche().render(d, "lines", focus="grandmother")[2] == 7           # val4 and taste1 hang on ev1
    # max_nodes: the strongest, core first
    t5, _, n5 = nodes.COFPsyche().render(d, "lines", max_nodes=5)
    assert n5 == 5 and t5.count("\n") == 4 and "Four years" in t5 and "Lightness" in t5 and "structure her ideas" in t5
    # languages: the preset's, or the one asked
    dfr = nodes.COFLoadCharacter().load(str(SAMPLES / "clara-psyche.cof"), "fr", "", 256)[0]
    sfr, pfr, _ = nodes.COFPsyche().render(dfr, "soul")
    assert sfr.startswith("## Qui elle est\n") and "### Valeurs\n- La légèreté : faire moins, mieux" in sfr and pfr.startswith("envers le Martien: une curiosité protectrice")
    assert "La légèreté" in nodes.COFPsyche().render(d, "soul", language="fr")[0] and "Lightness" in nodes.COFPsyche().render(dfr, "lines", language="en — 22 nodes")[0]
    assert nodes._psyche_language_choices(d["cof"], "default") == ["(preset) — en", "en — 22 nodes", "fr — 22 nodes"]
    # the 12-year-old holds three nodes and no stance
    d12 = nodes.COFLoadCharacter().load(str(SAMPLES / "clara-psyche.cof"), "en-12", "", 256)[0]
    t12, p12, n12 = nodes.COFPsyche().render(d12, "soul")
    assert n12 == 3 and p12 == "" and "Empathy before cleverness" in t12 and "agency" not in t12.lower()
    # the persona text gains the strongest value and stance, one short clause each
    persona = nodes.COFPersonality().text(d)[0]
    assert "\n\nCore value: Lightness: do less, better\n\nStance toward design agencies: no rancour, no return" in persona
    assert persona.index("Long term") < persona.index("Core value") < persona.index("Opening line")
    dag = nodes.COFLoadCharacter().load(str(SAMPLES / "clara-psyche.cof"), "en-agent", "", 256)[0]
    assert nodes.COFPersonality().text(dag)[0].index("Narrative role") < nodes.COFPersonality().text(dag)[0].index("Core value")
    assert nodes.COFPersonality().text(d12)[0].endswith("Core value: Empathy before cleverness")
    # a 1.1 file: no psyche, empty outputs, no persona clause
    d11 = nodes.COFLoadCharacter().load(str(SAMPLES / "clara-memories.cof"), "fr-agent", "", 256)[0]
    assert nodes.COFPsyche().render(d11, "soul") == ("", "", 0) and nodes.COFPsyche().render(d11, "prose", focus="Martien") == ("", "", 0)
    assert "Core value" not in nodes.COFPersonality().text(d11)[0]
    assert "COFPsyche" in nodes.NODE_CLASS_MAPPINGS and nodes.NODE_DISPLAY_NAME_MAPPINGS["COFPsyche"].endswith(" COF Psyche")


def test_psyche_risk_secret_personal(pack):
    """A synthetic dangerous belief (and the contradiction that leans on it) is dropped by default and kept with a marker on
    request; the file-level flag shows in the info; a secret node is never output unless asked; a personal node neither."""
    nodes, _ = pack
    from comfyui_cof.cof_reader import COF
    c = COF(SAMPLES / "clara-psyche.cof")
    V = c.m["elements"]["psyche"]["variants"]
    V["bel9"] = {"kind": "belief", "label": "Others are tools", "node": "elements/psyche/bel9/psyche-node.json", "identity": "id1"}
    V["contra9"] = {"kind": "contradiction", "label": "Tools and empathy", "node": "elements/psyche/contra9/psyche-node.json", "identity": "id1"}
    c.m["presets"]["default"]["slots"]["psyche"] += ["bel9", "contra9"]
    c.m["tags"]["content"] = ["psyche-dangerous"]
    synth = {"elements/psyche/bel9/psyche-node.json": {"gist": {"en": "Other people are tools to be used."}, "strength": 99, "stability": "core", "risk": "dangerous"},
             "elements/psyche/contra9/psyche-node.json": {"gist": {"en": "Uses people, preaches empathy."}, "strength": 60,
                                                           "links": [{"to": "psyche/bel9", "type": "conflicts"}, {"to": "psyche/val4", "type": "conflicts"}]}}
    orig = c.json_at
    c.json_at = lambda rel: synth[rel] if rel in synth else orig(rel)
    cof = {"cof": c, "preset": "default", "allow_intimate": False, "max_size": 256}
    # decision D3 (3 November): kept by default, marked — a graph generates for the person who built it, it does not act
    t2, _, n2 = nodes.COFPsyche().render(cof, "lines")
    assert n2 == 24 and "belief · Other people are tools to be used.  ·  dangerous" in t2 and "contradiction · Uses people, preaches empathy." in t2
    t, _, n = nodes.COFPsyche().render(cof, "lines", drop_dangerous=True)
    assert n == 22 and "tools" not in t and "Uses people" not in t                    # the contradiction lost a side: gone too
    assert nodes.COFPsyche.INPUT_TYPES()["optional"]["drop_dangerous"][1]["default"] is False
    assert nodes.COFPsyche().render(cof, "lines", max_nodes=1)[0].startswith("belief · Other people")   # core, strength 99
    assert nodes.COFPsyche().render(cof, "lines", max_nodes=1, drop_dangerous=True)[0].startswith("value · Lightness")
    assert "content: psyche-dangerous (a psyche node that would make the character harmful" in c.summary("default")
    # a secret contradiction: left out, said once in the soul format, never in lines or prose; shown and marked on request
    synth["elements/psyche/contra1/psyche-node.json"] = dict(orig("elements/psyche/contra1/psyche-node.json"), disclosure="secret")
    s, _, ns = nodes.COFPsyche().render(cof, "soul", drop_dangerous=True)
    assert ns == 21 and "Preaches slowness" not in s and s.rstrip().endswith("There are things she does not tell.")
    l, _, nl = nodes.COFPsyche().render(cof, "lines", drop_dangerous=True)
    assert nl == 21 and "Preaches slowness" not in l and "does not tell" not in l and "Preaches slowness" not in nodes.COFPsyche().render(cof, "prose")[0]
    s2, _, ns2 = nodes.COFPsyche().render(cof, "soul", include_secret=True, drop_dangerous=True)
    assert ns2 == 22 and "- Preaches slowness, works at night  ·  secret" in s2 and "does not tell" not in s2
    # an inappropriate node is kept, with a marker; a personal one is a user's data
    synth["elements/psyche/taste2/psyche-node.json"] = dict(orig("elements/psyche/taste2/psyche-node.json"), risk="inappropriate", visibility="personal")
    l3 = nodes.COFPsyche().render(cof, "lines")[0]
    assert "Hates notifications" not in l3
    l4 = nodes.COFPsyche().render(cof, "lines", include_personal=True)[0]
    assert "taste · Hates notifications and interfaces that blink at you.  ·  inappropriate" in l4
    # the persona clause never takes a dangerous or secret node
    assert "Core value: Lightness" in c.persona("default")[0] and "tools" not in c.persona("default")[0]


def test_people_places_objects(pack):
    """RFC 0005 on Clara (example 10 at 1.2, `tests/samples/clara-psyche.cof`): the reader exposes a memory's people,
    places and objects and a psyche node's own `who` / `where` / `what`; COF Memories names the objects beside the
    people and places; « what she thinks of X » also matches them (the guitar, an element's label; the notebook, a name)."""
    nodes, _ = pack
    from comfyui_cof.cof_reader import COF
    c = COF(SAMPLES / "clara-psyche.cof")
    mem = {m["id"]: m for m in c.memories("fr-65")}
    assert mem["ev5"]["objects"] == ["le pendentif de ma grand-mère"] and mem["ev2"]["objects"] == ["Guitare folk « peace »"]
    assert mem["ev5"]["people"] and mem["ev2"]["places"] == ["mon atelier personnel face au parc, Lyon"]
    psy = {n["id"]: n for n in c.psyche("default")}
    assert psy["hab1"]["objects"] == ["the notebook"] and psy["contra1"]["objects"] == ["Folk guitar with peace motif"]
    assert psy["val4"]["people"] == ["my grandmother"] and psy["taste1"]["places"] == ["my grandmother's house, at the foot of the Alps"]
    assert psy["want2"]["places"] == ["Bright studio facing the park"] and psy["stance1"]["people"] == []
    d = nodes.COFLoadCharacter().load(str(SAMPLES / "clara-psyche.cof"), "fr-65", "", 256)[0]
    text = nodes.COFMemories().recall(d)[0]
    assert "objects: le pendentif de ma grand-mère_" in text and "· objects: Guitare folk « peace »" in text
    line = next(l for l in text.splitlines() if "pendentif de ma" in l and l.startswith("_"))
    assert line.index("where:") < line.index("with:") < line.index("objects:")
    dd = nodes.COFLoadCharacter().load(str(SAMPLES / "clara-psyche.cof"), "", "", 256)[0]
    assert any("guitar" in k.lower() for k in psy["contra1"]["keywords"])                   # contra1: its item's label (the focus then drops it,
    assert "guitar" not in nodes.COFPsyche().render(dd, "lines", focus="guitar")[0]           # as a contradiction without both its sides)
    assert "Keeps a small recycled-paper notebook" in nodes.COFPsyche().render(dd, "lines", focus="carnet")[0]   # hab1: its own object, any language
    assert "Co-found a fully ethical" in nodes.COFPsyche().render(dd, "lines", focus="studio")[0]                # want2: its place (an environment)
