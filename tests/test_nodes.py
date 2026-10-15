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
    imgs, n, _ = nodes.COFImages().pick_images(c2[0], "clothing", True, "any", "any")
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
    allimg, n, labels = nodes.COFImages().pick_images(d, "environment", False, "any", "any")
    assert n == 2 and allimg.shape[0] == 2 and labels.splitlines()[1].startswith("image_002: ") and "mobile" in labels
    one, n2, _ = nodes.COFImages().pick_images(d, "environment — 2 images", False, "any", "any", "image_002 — Bright studio")
    assert n2 == 2 and one.shape[0] == 1


def test_persona_motion_voice(pack, tmp_path, monkeypatch):
    nodes, _ = pack
    d = nodes.COFLoadCharacter().load(str(SAMPLES / "clara-mercier.cof"), "", "", 256)[0]
    text, lang = nodes.COFPersona().text(d)
    assert "Clara" in text and lang == "en"
    monkeypatch.chdir(tmp_path)
    paths, labels, n = nodes.COFMotion3D().export(d, "motion clips", "item_001 — walk")
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
    clara = str(SAMPLES / "clara-mercier.cof")
    monkeypatch.setattr(nodes, "_files", lambda: [clara])
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
    st, d = call("/cof/choices", node="media", file=clara, preset="", what="motion clips")
    assert d["values"][0].startswith("all — ") and d["values"][1].startswith("item_001 — ")
    st, _ = call("/cof/choices", node="images", file="../../etc/passwd")
    assert st == 404


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
    imgs, n, lab = nodes.COFImages().pick_images(m, "clothing — 0 images", True, "any", "any", "mosaic_all — 0 images")
    assert n == 0 and imgs.shape == (1, 64, 64, 3) and "no clothing image" in lab
