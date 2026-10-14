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
    imgs, n = nodes.COFImages().pick(c2[0], "clothing", True, "any", "any")
    assert n >= 1 and imgs.shape[0] == n


def test_cast_and_unknowns(pack):
    nodes, _ = pack
    out = nodes.COFLoadCharacter().load(str(SAMPLES / "lea-martien.cof"), "", "Martien", 256)
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
