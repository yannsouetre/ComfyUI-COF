"""End-to-end tests of the COF nodes inside a REAL, running ComfyUI (no stub).

Skipped when no ComfyUI answers. Setup (CPU is enough, no checkpoint needed):
  - link or copy this folder to ComfyUI/custom_nodes/ComfyUI-COF
  - put clara-mercier.cof (cof repo, viewer/samples/) and any other .cof in ComfyUI/models/cof
  - start ComfyUI (python main.py --cpu --port 8188)
Run:   COMFYUI_URL=http://127.0.0.1:8188 COF_CLI_PATH=/path/to/cof/cli python -m pytest tests/e2e_comfyui.py -v
Optional: COF_CLI_PATH (the cof-cli folder) enables the comparison of the soul output with `cof psyche text`;
COFTEST_FILES (default clara-mercier.cof) is the 1.2 reference file.
"""
import json, os, subprocess, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path
import pytest

URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
CLI = os.environ.get("COF_CLI_PATH", "")
REF = os.environ.get("COFTEST_FILE", "clara-mercier.cof")
ROOT = Path(__file__).resolve().parents[1]


def _get(path, raw=False):
    with urllib.request.urlopen(URL + path, timeout=30) as r:
        b = r.read()
    return b if raw else json.loads(b)


def _alive():
    try:
        _get("/system_stats")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _alive(), reason=f"no ComfyUI at {URL}")


def post_prompt(prompt):
    req = urllib.request.Request(URL + "/prompt", json.dumps({"prompt": prompt}).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def run(prompt, timeout=120):
    """Queue an API prompt, wait for it. Returns (outputs, status_dict). Raises on a validation error."""
    code, body = post_prompt(prompt)
    if code != 200:
        raise PromptRejected(code, body)
    pid = body["prompt_id"]
    t0 = time.time()
    while time.time() - t0 < timeout:
        h = _get(f"/history/{pid}")
        if pid in h and h[pid].get("status", {}).get("completed") is not None and (h[pid]["status"].get("completed") or h[pid]["status"].get("status_str") == "error"):
            return h[pid]["outputs"], h[pid]["status"]
        time.sleep(0.2)
    raise TimeoutError(pid)


class PromptRejected(Exception):
    def __init__(self, code, body):
        self.code, self.body = code, body
        super().__init__(f"{code}: {json.dumps(body)[:600]}")


def wait_idle(timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        q = _get("/queue")
        if not q["queue_running"] and not q["queue_pending"]:
            return
        time.sleep(0.2)


def exec_error(status):
    for kind, data in status.get("messages", []):
        if kind == "execution_error":
            return data
    return None


def cof_chain(file=REF, preset="(default)", character="(default)", extra=None, allow_intimate=False):
    """A graph: loader '1' + the given extra nodes (id -> node). Every output of every extra node is previewed as text/images."""
    g = {"1": {"class_type": "COFLoadCharacter", "inputs": {"cof_file": file, "preset": preset, "character": character, "max_size": 256, "allow_intimate": allow_intimate}}}
    for k, v in (extra or {}).items():
        g[k] = {"class_type": v[0], "inputs": {"cof": ["1", 0], **v[1]}}
    return g


def text_outputs(prompt, node, outs, timeout=120):
    """Attach a PreviewAny to each listed output index of `node`; returns {index: text}."""
    p = json.loads(json.dumps(prompt))
    for i in outs:
        p[f"p{node}_{i}"] = {"class_type": "PreviewAny", "inputs": {"source": [node, i]}}
    o, st = run(p, timeout)
    err = exec_error(st)
    assert not err, err
    return {i: "".join(o[f"p{node}_{i}"]["text"]) for i in outs}


def psyche(file=REF, preset="(default)", **kw):
    inputs = {"format": "soul", "language": "(preset)", "focus": "", "max_nodes": 0, "include_secret": False, "drop_dangerous": False, "include_personal": False,
              "show_illustrations": False, "output_illustrations": False}
    inputs.update(kw)
    g = cof_chain(file, preset, extra={"2": ("COFPsyche", inputs)})
    t = text_outputs(g, "2", [0, 1, 2])
    return t[0], t[1], int(t[2])


import re
_RISK_MARK = re.compile(r"\s+·\s+(?:dangerous|inappropriate)\b")


def no_risk_marks(text):
    """The node marks dangerous / inappropriate nodes in the text (its own addition); cof-cli's SOUL.md does not."""
    return _RISK_MARK.sub("", text).strip()


def cli_text(file, preset, *flags):
    if not CLI:
        pytest.skip("COF_CLI_PATH not set")
    code = "import sys;from cof_cli.cli import main;sys.argv=['cof','psyche','text',sys.argv[1],'--preset',sys.argv[2],*sys.argv[3:]];main()"
    src = _find(file)
    r = subprocess.run([sys.executable, "-c", code, src, preset, *flags], env={**os.environ, "PYTHONPATH": CLI}, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


# ---------------------------------------------------------------- test files built from the reference .cof (needs COMFYUI_DIR)
EDITS = {"bel3": {"risk": "dangerous"}, "fear3": {"risk": "inappropriate"}, "need1": {"disclosure": "secret"}, "taste2": {"visibility": "personal"}, "want3": {"risk": "dangerous"}}


def build_variants(src: Path, out: Path):
    """e2e-risk.cof (Clara with dangerous / inappropriate / secret / personal psyche nodes) and corrupted files for the error paths."""
    import zipfile
    zin = zipfile.ZipFile(src)
    with zipfile.ZipFile(out / "e2e-risk.cof", "w", zipfile.ZIP_DEFLATED) as zo:
        for i in zin.infolist():
            data = zin.read(i.filename)
            for nid, ed in EDITS.items():
                if i.filename == f"elements/psyche/{nid}/psyche-node.json":
                    d = json.loads(data); d.update(ed); data = json.dumps(d, ensure_ascii=False).encode()
            zo.writestr(i.filename, data)
    raw = src.read_bytes()
    (out / "e2e-truncated.cof").write_bytes(raw[: len(raw) // 2])
    (out / "e2e-notzip.cof").write_bytes(b"this is not a zip file\n")
    (out / "e2e-empty.cof").write_bytes(b"")
    with zipfile.ZipFile(out / "e2e-badmanifest.cof", "w") as zo:
        zo.writestr("manifest.json", "{ not json")
    with zipfile.ZipFile(out / "e2e-nomanifest.cof", "w") as zo:
        zo.writestr("readme.txt", "hello")
    m = json.loads(zin.read("manifest.json"))                              # a valid ZIP with one corrupted member (a view of the character)
    infos = {i.filename: i for i in zin.infolist()}
    target = next(a["path"] for a in m["assets"] if a.get("role") in ("view", "reference") and a.get("path") in infos and str(a.get("mediaType", "")).startswith("image/"))
    zi, buf = infos[target], bytearray(raw)
    start = zi.header_offset + 30 + len(zi.filename.encode()) + 4
    for k in range(start + 100, start + 140):
        buf[k] ^= 0xFF
    (out / "e2e-badmember.cof").write_bytes(bytes(buf))


# Illustrated psyche nodes of the reference file: hab1 (media a-mem-ev1-1, a memory image, 1248x832) and contra1 (media a-ac-x1,
# the guitar accessory, 1280x714). Each file below changes the manifest (psyche variants, rights, identity, an appearance variant)
# and / or node JSON to exercise one safeguard of the illustrations (RFC 0004 § 3 decies, the rules of memory images).
SEXUAL_OK = {"rights.permissions.allowSexualUsage": True}
Y = lambda n: {"value": n, "unit": "years"}          # an age, as COF writes it
ILLUS = {
    # contra1 nude, showing the character at 30; the file permits sexual usage
    "e2e-ill-adult.cof": {**SEXUAL_OK, "psyche.contra1": {"nudity": True, "age": Y(30)}},
    # same, but the file's rating forbids sexual usage (Clara's own rights)
    "e2e-ill-rating.cof": {"psyche.contra1": {"nudity": True, "age": Y(30)}},
    # hab1 shows the character at 15 (not nude): the preset's safety age becomes 15, so contra1 (nude, 30) is held even on request
    "e2e-ill-minor.cof": {**SEXUAL_OK, "psyche.contra1": {"nudity": True, "age": Y(30)}, "psyche.hab1": {"age": Y(15)}},
    # contra1 nude at 30 but also shows a 16-year-old; hab1 shows a real passer-by (free name) without consent
    "e2e-ill-who.cof": {**SEXUAL_OK, "psyche.contra1": {"nudity": True, "age": Y(30)},
                        "node.contra1": {"who": [{"label": {"en": "a friend", "fr": "une amie"}, "age": Y(16), "shown": True}]},
                        "node.hab1": {"who": [{"label": {"en": "a passer-by", "fr": "un passant"}, "real": True, "shown": True}]}},
    # contra1 nude at 30 also shows an adult real passer-by (free name): no consent record, so never nude
    "e2e-ill-realnude.cof": {**SEXUAL_OK, "psyche.contra1": {"nudity": True, "age": Y(30)},
                             "node.contra1": {"who": [{"label": {"en": "a passer-by", "fr": "un passant"}, "real": True, "age": Y(40), "shown": True}]}},
    # the character is a real person without a consent record; hab1 shows her (age 30), contra1 shows only the guitar
    "e2e-ill-real.cof": {"identity.id1": {"fictional": False, "consent": None}, "psyche.hab1": {"age": Y(30)}},
    # the same real person, with a consent record (image only)
    "e2e-ill-realok.cof": {"identity.id1": {"fictional": False, "consent": {"scope": ["image", "likeness"], "date": "2026-01-01", "by": "e2e"}},
                           "psyche.hab1": {"age": Y(30)}},
    # contra1's image is the image of an appearance variant marked nudity (x1): nude through the asset, not the psyche flag
    "e2e-ill-appnude.cof": {"appearance.x1": {"nudity": True}},
    # hab1's image belongs to memory ev1, now explicit: nude through the memory (as COF Memories holds it back)
    "e2e-ill-memexp.cof": {"memnode.ev1": {"explicit": True}},
    # hab1 is a secret node: its illustration must follow the text (only with include_secret)
    "e2e-ill-secret.cof": {"node.hab1": {"disclosure": "secret"}},
    # hab1 is a dangerous node: drop_dangerous leaves out its text and its illustration
    "e2e-ill-danger.cof": {"node.hab1": {"risk": "dangerous"}},
}


def build_illus(src: Path, out: Path):
    import zipfile
    zin = zipfile.ZipFile(src)
    m0 = json.loads(zin.read("manifest.json"))
    for name, edits in ILLUS.items():
        m = json.loads(json.dumps(m0))
        node_edits = {}
        for key, val in edits.items():
            kind, _, ref = key.partition(".")
            if kind == "rights":
                d = m.setdefault("rights", {})
                *path, last = ref.split(".")
                for p in path:
                    d = d.setdefault(p, {})
                d[last] = val
            elif kind == "identity":
                m["identities"][ref].update(val)
            elif kind in ("psyche", "appearance"):
                m["elements"][kind]["variants"][ref].update(val)
            elif kind == "node":
                node_edits[m["elements"]["psyche"]["variants"][ref]["node"]] = val
            elif kind == "memnode":
                node_edits[m["elements"]["memories"]["variants"][ref]["memory"]] = val
        with zipfile.ZipFile(out / name, "w", zipfile.ZIP_DEFLATED) as zo:
            for i in zin.infolist():
                data = zin.read(i.filename)
                if i.filename == "manifest.json":
                    data = json.dumps(m, ensure_ascii=False).encode()
                elif i.filename in node_edits:
                    d = json.loads(data); d.update(node_edits[i.filename]); data = json.dumps(d, ensure_ascii=False).encode()
                zi = zipfile.ZipInfo(i.filename, i.date_time)          # a fresh ZipInfo: writestr mutates the one it is given (CRC, sizes)
                zi.compress_type = i.compress_type
                zo.writestr(zi, data)


@pytest.fixture(scope="session", autouse=True)
def variants():
    d = os.environ.get("COMFYUI_DIR")
    made = []
    if d and _alive():
        models = Path(d) / "models" / "cof"
        src = models / REF
        if src.exists():
            before = set(models.iterdir())
            build_variants(src, models)
            build_illus(src, models)
            made = [f for f in models.iterdir() if f not in before]
    yield
    for f in made:
        f.unlink(missing_ok=True)


def _find(file):
    d = os.environ.get("COMFYUI_DIR", "")
    for sub in ("models/cof", "input/cof"):
        p = Path(d) / sub / file
        if d and p.exists():
            return str(p)
    pytest.skip("COMFYUI_DIR not set: cannot find the .cof file for the CLI")


def has_file(name):
    oi = _get("/object_info/COFLoadCharacter")["COFLoadCharacter"]["input"]["required"]["cof_file"][0]
    return name in oi


def need(name):
    if not has_file(name):
        pytest.skip(f"{name} is not in ComfyUI's cof folders")


def image_info(entry):
    q = urllib.parse.urlencode({"filename": entry["filename"], "subfolder": entry.get("subfolder", ""), "type": entry["type"]})
    from PIL import Image
    import io
    im = Image.open(io.BytesIO(_get("/view?" + q, raw=True)))
    return im.size


# ---------------------------------------------------------------- 1. registration
EXPECTED = {   # class: (outputs, required inputs, optional inputs)
    "COFLoadCharacter": (["cof", "primary_image", "references", "prompt", "info"], ["cof_file", "preset", "character", "max_size"], ["allow_intimate"]),
    "COFPersonality": (["persona", "language"], ["cof"], []),
    "COFAppearance": (["images", "count", "labels"], ["cof", "kind", "include_offered", "pick"], ["subject", "angle"]),
    "COFApplyLoRA": (["model", "clip", "trigger_words"], ["model", "clip", "cof", "strength"], []),
    "COFReferenceLatents": (["paths", "base_models"], ["cof", "folder"], []),
    "COF3D": (["paths", "labels", "count"], ["cof", "pick"], []),
    "COFVoice": (["audio", "transcript", "labels"], ["cof", "sample"], []),
    "COFPostures": (["images", "count", "labels"], ["cof", "kind", "include_offered", "pick"], []),
    "COFMotion": (["paths", "labels", "count"], ["cof", "pick"], []),
    "COFFiles": (["text", "paths", "labels", "count"], ["cof", "kind", "pick"], []),
    "COFMemories": (["text", "images", "audio", "media_paths", "labels", "count"], ["cof", "pick"], ["media", "include_personal", "include_text_media"]),
    "COFPsyche": (["text", "positions", "count", "illustrations"], ["cof", "format"], ["language", "focus", "max_nodes", "include_secret", "drop_dangerous", "include_personal", "show_illustrations", "output_illustrations"]),
}


@pytest.mark.parametrize("cls", sorted(EXPECTED))
def test_registration(cls):
    oi = _get("/object_info")
    assert cls in oi, f"{cls} missing from /object_info"
    n = oi[cls]
    outs, req, opt = EXPECTED[cls]
    assert n["category"] == "COF"
    assert n["output_name"] == outs
    assert list(n["input"]["required"]) == req
    assert set(n["input"].get("optional", {})) == set(opt)


def test_no_unexpected_cof_nodes():
    oi = _get("/object_info")
    assert {k for k, v in oi.items() if v["category"] == "COF"} == set(EXPECTED)


def test_routes():
    need(REF)
    r = _get("/cof/info?file=" + urllib.parse.quote(REF))
    assert r["presets"] and r["characters"]
    assert any(p["value"].startswith("fr-12") for p in r["presets"]), r["presets"]
    ps = _get("/cof/choices?node=psyche&file=" + urllib.parse.quote(REF) + "&preset=default")
    assert ps["values"][0].startswith("(preset)")
    with pytest.raises(urllib.error.HTTPError) as e:
        _get("/cof/info?file=../../etc/passwd")
    assert e.value.code == 404


# ---------------------------------------------------------------- 2. example workflows
CORE_UI = {"PreviewImage", "PreviewAny", "PreviewAudio", "MarkdownNote", "Note", "SaveImage", "CheckpointLoaderSimple", "CLIPTextEncode", "EmptyLatentImage", "KSampler", "VAEDecode"}


def _wf(name):
    return json.loads((ROOT / "workflows" / name).read_text(encoding="utf-8"))


def test_workflow_api_preview_runs():
    need("clara-mercier.cof")
    g = _wf("cof_preview_api.json")
    o, st = run(g, timeout=300)
    assert st["status_str"] == "success", exec_error(st)
    texts = {k: "".join(v["text"]) for k, v in o.items() if "text" in v}
    imgs = {k: len(v["images"]) for k, v in o.items() if "images" in v}
    assert "Clara" in texts["4"] and "rights" in texts["5"]                    # prompt, info
    assert texts["11"].strip()                                                 # personality
    assert imgs["2"] == 1 and imgs["3"] >= 1 and imgs["52"] >= 1             # primary, references, memory images
    assert all(w > 0 and h > 0 for k in imgs for e_ in o[k]["images"] for w, h in [image_info(e_)])
    assert "audio" in o["31"] and "audio" in o["53"]
    assert set(o) >= {k for k, v in g.items() if v["class_type"].startswith("Preview")}, "a preview node produced nothing"


def test_workflow_api_txt2img_needs_a_checkpoint_and_COF_part_runs():
    need("clara-mercier.cof")
    g = _wf("cof_txt2img_api.json")
    code, body = post_prompt(g)                      # no checkpoint in this ComfyUI: a clean validation error on the loader (the rest still runs), not a crash
    assert "node_errors" in body, (code, body)
    assert any(e_["type"] == "value_not_in_list" for ne in body["node_errors"].values() for e_ in ne["errors"])
    wait_idle()
    sub = {k: v for k, v in g.items() if k in ("1", "10")}      # the COF part only: loader + references preview, prompt shown
    sub["11"] = {"class_type": "PreviewAny", "inputs": {"source": ["1", 3]}}
    o, st = run(sub)
    assert st["status_str"] == "success" and "Clara" in "".join(o["11"]["text"]) and len(o["10"]["images"]) >= 1


def test_workflow_ui_files_are_consistent():
    oi = _get("/object_info")
    for name in ("cof_preview.json",):
        wf = _wf(name)
        ids = {n["id"] for n in wf["nodes"]}
        links = {l[0]: l for l in wf["links"]}
        for n in wf["nodes"]:
            t = n["type"]
            assert t in oi or t in CORE_UI, f"{name}: unknown node type {t}"
            if t in oi:
                inp = oi[t]["input"]
                names = list(inp["required"]) + list(inp.get("optional", {}))
                wnames = [x for x in names if x in inp["required"] and isinstance(inp["required"][x][0], (list, str)) and x not in [i["name"] for i in n.get("inputs", []) if i.get("link") is not None] and (isinstance(inp["required"][x][0], list) or inp["required"][x][0] in ("INT", "FLOAT", "STRING", "BOOLEAN"))]
                assert len(n.get("widgets_values", [])) >= len(wnames) - 1, f"{name}: node {n['id']} {t}: widgets {n.get('widgets_values')} vs {wnames}"
            for i in n.get("inputs", []):
                if i.get("link") is not None:
                    l = links[i["link"]]
                    assert l[3] == n["id"] and l[1] in ids
            for o in n.get("outputs", []) or []:
                for lk in o.get("links") or []:
                    assert links[lk][1] == n["id"]


# ---------------------------------------------------------------- 3. 1.2 features
@pytest.mark.parametrize("preset", ["default", "fr-12", "en-65"])
def test_psyche_soul_equals_cli(preset):
    need(REF)
    node, _, _ = psyche(preset=preset, format="soul")
    assert node.strip() == cli_text(REF, preset, "--keep-dangerous").strip()
    node2, _, _ = psyche(preset=preset, format="soul", drop_dangerous=True)
    assert node2.strip() == cli_text(REF, preset).strip()


def test_psyche_soul_equals_cli_with_risk_secret_personal():
    need("e2e-risk.cof")
    for kw, flags in [({}, ("--keep-dangerous",)), ({"drop_dangerous": True}, ()), ({"include_personal": True}, ("--keep-dangerous", "--keep-personal"))]:
        node, _, _ = psyche("e2e-risk.cof", "default", format="soul", **kw)
        assert no_risk_marks(node) == no_risk_marks(cli_text("e2e-risk.cof", "default", *flags)), (kw, flags)


def test_psyche_formats_and_counts():
    need(REF)
    lines, pos, n = psyche(format="lines")
    rows = [l for l in lines.splitlines() if l.strip()]
    assert len(rows) == n == 22
    assert rows[0].startswith("wound · ") and rows[-1].startswith("contradiction · ")
    assert all(l.split(" · ")[0] in {"wound", "value", "belief", "want", "need", "fear", "stance", "taste", "habit", "contradiction", "rule"} for l in rows)
    assert "acts on it without knowing it" in lines and "half aware" in lines and "warmth +60" in lines   # aware / valence markers
    assert [l for l in pos.splitlines()] and all(l.startswith("toward ") for l in pos.splitlines())
    prose, pos2, n2 = psyche(format="prose")
    assert n2 == n and pos2 == pos and "\n" not in prose.strip() and prose.startswith("Values:") and "(warmth +60)" in prose
    assert "dangerous" not in prose
    fr, _, nf = psyche(preset="fr-12", format="lines", language="(preset)")
    assert nf == 3 and "L'empathie avant l'astuce." in fr


def test_psyche_language_override():
    need(REF)
    en, _, ne = psyche(preset="fr", format="lines", language="en")
    fr, _, nf = psyche(preset="fr", format="lines", language="fr")
    assert ne == nf and en != fr and "Accessibility" in en and "accessibilité" in fr
    ch = _get("/cof/choices?node=psyche&file=" + urllib.parse.quote(REF) + "&preset=fr")["values"]
    assert ch[0].startswith("(preset)") and any(v.startswith("en") for v in ch) and any(v.startswith("fr") for v in ch)
    t, _, _ = psyche(preset="fr", format="lines", language="en — 22 nodes")      # the dropdown's label form is accepted
    assert t == en


@pytest.mark.parametrize("focus,expect_in,expect_out", [
    ("the Martian", ["Toward the Martian"], ["notebook", "Hates notifications"]),                  # a person (a cast name / a stance subject)
    ("grandmother", ["Empathy before cleverness", "lime tree"], ["Toward the Martian"]),            # a free-name person + the place of a taste
    ("Alps", ["lime tree"], ["Toward the Martian"]),                                                # a place named in the free label
    ("guitar", ["guitar chords"], ["notebook"]),                                                    # an object: the contradiction, with its two sides
    ("notebook", ["recycled-paper notebook"], ["guitar"]),                                          # a free-name object
])
def test_psyche_focus(focus, expect_in, expect_out):
    need(REF)
    full, _, n_full = psyche(format="lines")
    t, _, n = psyche(format="lines", focus=focus)
    assert 5 <= n < n_full, "core nodes always kept, the rest narrowed"
    for s in expect_in:
        assert s in t, (focus, s)
    for s in expect_out:
        assert s not in t, (focus, s)


def test_psyche_focus_contradiction_brings_its_two_sides():
    need(REF)
    t, _, _ = psyche(format="lines", focus="guitar")
    assert "guitar chords" in t and "If I slow down, I disappear" in t and "Co-found a fully ethical" in t   # contra1 + bel3 + want2


def test_psyche_focus_on_a_memory_and_no_match():
    need(REF)
    core, _, n0 = psyche(format="lines", focus="zzzqq")
    assert n0 == 5
    t, _, n = psyche(format="lines", focus="the first morning in my own studio")
    assert n > n0 and "Co-found" in t                          # ev2's node (want2, where: e1) is tied to that memory


def test_psyche_max_nodes():
    need(REF)
    t, _, n = psyche(format="lines", max_nodes=7)
    assert n == 7 and len(t.strip().splitlines()) == 7


def test_psyche_risk_options():
    need("e2e-risk.cof")
    kept, _, nk = psyche("e2e-risk.cof", format="lines")                       # D3: dangerous kept by default, marked
    assert "· dangerous" in kept and kept.count("dangerous") == 2 and "inappropriate" in kept
    dropped, _, nd = psyche("e2e-risk.cof", format="lines", drop_dangerous=True)
    assert "dangerous" not in dropped and "inappropriate" in dropped            # inappropriate always kept
    assert "Preaches slowness" not in dropped and nd == nk - 3                  # both dangerous nodes + the contradiction that lost a side
    sec, _, ns = psyche("e2e-risk.cof", format="lines", include_secret=True)
    assert "· secret" in sec and ns == nk + 1 and "Accept being helped" not in kept
    per, _, npn = psyche("e2e-risk.cof", format="lines", include_personal=True)
    assert "Hates notifications" in per and "Hates notifications" not in kept and npn == nk + 1
    soul, _, _ = psyche("e2e-risk.cof", format="soul")
    assert "dangerous" in soul
    prose_kept, _, _ = psyche("e2e-risk.cof", format="prose")
    assert prose_kept.strip()


def test_psyche_on_files_without_psyche():
    for f in ("clara-martien.cof", "ex-01-clara-text-only.cof", "ex-03-clara-photos.cof", "ex-11-duo-clara-martien.cof"):
        if not has_file(f):
            continue
        for fmt in ("soul", "lines", "prose"):
            assert psyche(f, format=fmt) == ("", "", 0), (f, fmt)
        g = cof_chain(f, extra={"2": ("COFPersonality", {})})
        t = text_outputs(g, "2", [0])[0]
        assert "Core value" not in t and "Stance toward" not in t


def test_personality_clause_on_clara():
    need(REF)
    t = text_outputs(cof_chain(extra={"2": ("COFPersonality", {})}), "2", [0, 1])
    assert "Core value: Lightness: do less, better" in t[0] and "Stance toward design agencies: no rancour, no return" in t[0]


def test_memories_node_on_clara():
    need(REF)
    g = cof_chain(preset="en-65", extra={"2": ("COFMemories", {"pick": "all", "media": "all", "include_personal": False, "include_text_media": True})})
    t = text_outputs(g, "2", [0, 3, 4, 5])
    n = int(t[5])
    assert n >= 5 and len(t[4].strip().splitlines()) == n
    assert "objects: Folk guitar with peace motif" in t[0] and "with: the Martian" in t[0] and "where: " in t[0]
    assert all(Path(p).is_file() for p in t[3].splitlines())
    p = json.loads(json.dumps(g)); p["i"] = {"class_type": "PreviewImage", "inputs": {"images": ["2", 1]}}
    o, st = run(p)
    assert st["status_str"] == "success" and len(o["i"]["images"]) >= 1
    # one memory, one image
    g2 = cof_chain(extra={"2": ("COFMemories", {"pick": "memory_001", "media": "all", "include_personal": False, "include_text_media": False})})
    t2 = text_outputs(g2, "2", [0, 5])
    assert t2[0].count("### ") == 1 and "grandmother" in t2[0] and t2[5] == "1"          # count = the memories the text holds (0.7.0); labels still list the preset's


def test_memories_and_media_on_every_file_without_error():
    for f in ("ex-01-clara-text-only.cof", "ex-02-ecoliere.cof", "ex-07-clara-modulaire.cof", "clara-martien.cof", "ex-11-duo-clara-martien.cof"):
        if not has_file(f):
            continue
        g = cof_chain(f, extra={"2": ("COFMemories", {"pick": "all", "media": "all", "include_personal": False, "include_text_media": True})})
        g["i"] = {"class_type": "PreviewImage", "inputs": {"images": ["2", 1]}}
        t = text_outputs(g, "2", [0, 5])
        assert t[5].isdigit()
        if f.startswith("ex-0"):
            assert t[0] == "" and t[5] == "0"


def test_illustrated_psyche_nodes_do_not_break_anything():
    """contra1 and hab1 carry `media` (psyche-media): every node still runs on the file (COF Psyche outputs them only on request, see below)."""
    need(REF)
    for fmt in ("soul", "lines", "prose"):
        t, _, n = psyche(format=fmt, focus="guitar")
        assert n >= 5
    g = cof_chain(extra={"2": ("COFAppearance", {"kind": "all", "include_offered": True, "pick": "mosaic_all", "subject": "any", "angle": "any"})})
    g["i"] = {"class_type": "PreviewImage", "inputs": {"images": ["2", 0]}}
    o, st = run(g)
    assert st["status_str"] == "success"


def test_psyche_illustrations_in_a_real_comfyui():
    """show_illustrations: a preview in the node itself (ui.images, temp files); output_illustrations: the batch on the 4th output."""
    need(REF)
    inputs = {"format": "lines", "language": "(preset)", "focus": "", "max_nodes": 0, "include_secret": False, "drop_dangerous": False, "include_personal": False,
              "show_illustrations": True, "output_illustrations": True}
    g = cof_chain(extra={"2": ("COFPsyche", inputs)})
    g["i"] = {"class_type": "PreviewImage", "inputs": {"images": ["2", 3]}}
    g["t"] = {"class_type": "PreviewAny", "inputs": {"source": ["2", 0]}}
    o, st = run(g)
    assert st["status_str"] == "success", exec_error(st)
    assert len(o["2"]["images"]) == 2 and all(e["type"] == "temp" for e in o["2"]["images"])      # contra1 and hab1, shown in the node
    assert len(o["i"]["images"]) == 2                                                               # and output as a batch
    assert all(image_info(e)[0] > 64 for e in o["2"]["images"])
    # off by default: no preview in the node, a blank 64x64 image on the output
    g2 = cof_chain(extra={"2": ("COFPsyche", dict(inputs, show_illustrations=False, output_illustrations=False))})
    g2["i"] = {"class_type": "PreviewImage", "inputs": {"images": ["2", 3]}}
    o2, st2 = run(g2)
    assert st2["status_str"] == "success" and not o2.get("2", {}).get("images") and image_info(o2["i"]["images"][0]) == (64, 64)


# ---------------------------------------------------------------- 3 bis. psyche illustrations (0.7.0): options, selection, safeguards
def _log_size():
    """Now, on the server's clock (its log entries are stamped with the local time)."""
    from datetime import datetime
    return datetime.now().isoformat()


def _log_since(t0):
    """What the COF nodes printed since t0, read from ComfyUI's in-memory log (/internal/logs/raw: entries are recorded on write,
    whereas the console file is block-buffered when redirected). None when the route is missing."""
    try:
        entries = _get("/internal/logs/raw")["entries"]
    except Exception:
        return None
    return "".join(e["m"] for e in entries if e.get("t", "") >= t0)


def node_of(size):
    """Which illustration a preview is, from its size (loader max_size 256): hab1 is 1248x832 (-> 256x171), contra1 1280x714 (-> 256x143)."""
    w, h = size
    return "hab1" if h > 160 else "contra1"


def illus(file=REF, preset="(default)", allow_intimate=False, show=True, output=True, **kw):
    """Run loader + COF Psyche with the illustration options; returns (node ids previewed in the node, batch sizes, count, text, log lines)."""
    inputs = {"format": "lines", "language": "(preset)", "focus": "", "max_nodes": 0, "include_secret": False, "drop_dangerous": False,
              "include_personal": False, "show_illustrations": show, "output_illustrations": output}
    inputs.update(kw)
    g = cof_chain(file, preset, extra={"2": ("COFPsyche", inputs)}, allow_intimate=allow_intimate)
    g["i"] = {"class_type": "PreviewImage", "inputs": {"images": ["2", 3]}}
    g["c"] = {"class_type": "PreviewAny", "inputs": {"source": ["2", 2]}}
    g["t"] = {"class_type": "PreviewAny", "inputs": {"source": ["2", 0]}}
    pos = _log_size()
    o, st = run(g)
    assert st["status_str"] == "success", exec_error(st)
    ui_entries = o.get("2", {}).get("images", [])
    assert all(e["type"] == "temp" for e in ui_entries)
    ui = [node_of(image_info(e)) for e in ui_entries]
    batch = [image_info(e) for e in o["i"]["images"]]
    time.sleep(0.3)
    return ui, batch, int("".join(o["c"]["text"])), "".join(o["t"]["text"]), _log_since(pos)


BLANK = [(64, 64)]


def real_batch(batch, n):
    return len(batch) == n and all(s != (64, 64) and s[0] > 64 for s in batch)


def test_illus_options_off_on():
    """show_illustrations -> previews in the node only; output_illustrations -> the IMAGE batch only; the text never changes."""
    need(REF)
    ui00, b00, n00, t00, _ = illus(show=False, output=False)
    ui10, b10, n10, t10, _ = illus(show=True, output=False)
    ui01, b01, n01, t01, _ = illus(show=False, output=True)
    ui11, b11, n11, t11, _ = illus(show=True, output=True)
    assert ui00 == [] and b00 == BLANK
    assert ui10 == ["hab1", "contra1"] and b10 == BLANK
    assert ui01 == [] and real_batch(b01, 2)
    assert ui11 == ["hab1", "contra1"] and real_batch(b11, 2)
    assert n00 == n10 == n01 == n11 == 22 and t00 == t10 == t01 == t11


def test_illus_follow_focus_max_nodes_and_preset():
    need(REF)
    ui, b, n, _, _ = illus(focus="guitar")
    assert ui == ["contra1"] and real_batch(b, 1)                     # hab1 is not about the guitar
    ui, b, n, _, _ = illus(max_nodes=5)
    assert ui == [] and b == BLANK and n == 5                         # neither illustrated node is among the 5 strongest
    ui, b, n, _, _ = illus(max_nodes=20)
    assert ui == ["hab1", "contra1"] and real_batch(b, 2) and n == 20
    ui, b, n, _, _ = illus(preset="en-65")
    assert ui == ["hab1"] and real_batch(b, 1)                        # en-65 has hab1, not contra1
    ui, b, n, _, _ = illus(preset="en-12")
    assert ui == [] and b == BLANK and n == 3


def test_illus_of_a_secret_node_follow_the_text():
    need("e2e-ill-secret.cof")
    ui, b, n, t, _ = illus("e2e-ill-secret.cof")
    assert ui == ["contra1"] and real_batch(b, 1) and n == 21
    ui, b, n, t, _ = illus("e2e-ill-secret.cof", include_secret=True)
    assert ui == ["hab1", "contra1"] and real_batch(b, 2) and n == 22


def test_illus_of_a_dropped_dangerous_node_are_dropped_too():
    need("e2e-ill-danger.cof")
    ui, b, n, _, _ = illus("e2e-ill-danger.cof")
    assert ui == ["hab1", "contra1"] and real_batch(b, 2) and n == 22   # kept and marked by default
    ui, b, n, _, _ = illus("e2e-ill-danger.cof", drop_dangerous=True)
    assert ui == ["contra1"] and real_batch(b, 1) and n == 21


def test_psyche_prompt_without_the_new_inputs_still_runs():
    """An API prompt saved before 0.7.0 has no show_illustrations / output_illustrations: both default to off, 4 outputs."""
    need(REF)
    inputs = {"format": "lines", "language": "(preset)", "focus": "", "max_nodes": 0, "include_secret": False, "drop_dangerous": False, "include_personal": False}
    g = cof_chain(extra={"2": ("COFPsyche", inputs)})
    g["i"] = {"class_type": "PreviewImage", "inputs": {"images": ["2", 3]}}
    g["c"] = {"class_type": "PreviewAny", "inputs": {"source": ["2", 2]}}
    o, st = run(g)
    assert st["status_str"] == "success", exec_error(st)
    assert not o.get("2", {}).get("images") and [image_info(e) for e in o["i"]["images"]] == BLANK and "".join(o["c"]["text"]) == "22"


def test_illus_nudity_only_with_allow_intimate():
    need("e2e-ill-adult.cof")
    ui, b, _, _, log = illus("e2e-ill-adult.cof", allow_intimate=False)
    assert ui == ["hab1"] and real_batch(b, 1)
    assert log is None or "contra1 (nudity)" in log, log
    ui, b, _, _, _ = illus("e2e-ill-adult.cof", allow_intimate=True)
    assert ui == ["hab1", "contra1"] and real_batch(b, 2)            # adult preset, file permits it, the person shown is 30


def test_illus_nudity_never_when_the_rating_forbids_it():
    need("e2e-ill-rating.cof")
    for allow in (False, True):
        ui, b, _, _, log = illus("e2e-ill-rating.cof", allow_intimate=allow)
        assert ui == ["hab1"] and real_batch(b, 1), (allow, ui)
        assert log is None or "contra1 (nudity)" in log, log


def test_illus_age_of_an_illustration_makes_the_preset_a_minor():
    """hab1 shows the character at 15: the preset's safety age is 15, so no nudity at all, even an adult one, even on request."""
    need("e2e-ill-minor.cof")
    ui, b, _, _, log = illus("e2e-ill-minor.cof", allow_intimate=True)
    assert ui == ["hab1"] and real_batch(b, 1)                        # hab1 (not nude) is shown; contra1 (nude) never
    assert log is None or "contra1 (nudity)" in log, log


def test_illus_nudity_with_a_minor_shown_and_real_person_without_consent():
    """contra1 is nude and shows a 16-year-old: never. hab1 is not nude and shows a real passer-by (free name, no consent
    record): given, as a memory image would be, with the console warning (D9: consent mandatory for nudity only)."""
    need("e2e-ill-who.cof")
    for allow in (False, True):
        ui, b, _, _, log = illus("e2e-ill-who.cof", allow_intimate=allow)
        assert ui == ["hab1"] and real_batch(b, 1), (allow, ui)
        if log is not None:
            assert "hab1 (" not in log, log
            assert ("contra1 (nudity with a person under 18 or of unknown age)" if allow else "contra1 (nudity)") in log, log
            assert "real person without a consent record — you are responsible" in log, log


def test_illus_real_person_without_consent_like_memory_images():
    """A real character without a consent record: her illustration is given (consent recommended, mandatory only for nudity —
    the rule of memory images and reference images, D9), with the console warning; with a consent record too."""
    need("e2e-ill-real.cof")
    for allow in (False, True):
        ui, b, _, _, log = illus("e2e-ill-real.cof", allow_intimate=allow)
        assert ui == ["hab1", "contra1"] and real_batch(b, 2), (allow, ui)
        assert log is None or "real person without a consent record — you are responsible" in log, log
    ui, b, _, _, _ = illus("e2e-ill-realok.cof")
    assert ui == ["hab1", "contra1"] and real_batch(b, 2)


def test_illus_nudity_of_a_real_person_without_the_matching_consent():
    """contra1 nude, an adult real passer-by (free name) shown: held even with allow_intimate — a `who[].real` person has no consent record."""
    need("e2e-ill-realnude.cof")
    ui, b, _, _, log = illus("e2e-ill-realnude.cof", allow_intimate=True)
    assert ui == ["hab1"] and real_batch(b, 1), ui
    assert log is None or "contra1 (nudity of a real person without the matching consent)" in log, log


def test_illus_nude_asset_of_an_appearance_variant():
    need("e2e-ill-appnude.cof")
    for allow in (False, True):
        ui, b, _, _, log = illus("e2e-ill-appnude.cof", allow_intimate=allow)
        assert ui == ["hab1"] and real_batch(b, 1), (allow, ui)


def test_illus_image_of_an_explicit_memory_is_held_like_in_cof_memories():
    need("e2e-ill-memexp.cof")
    ui, b, _, _, log = illus("e2e-ill-memexp.cof")
    assert ui == ["contra1"] and real_batch(b, 1)
    assert log is None or "hab1 (nudity)" in log, log
    # COF Memories holds back the same memory's images (memory_001 = ev1)
    g = cof_chain("e2e-ill-memexp.cof", extra={"2": ("COFMemories", {"pick": "memory_001", "media": "all", "include_personal": False, "include_text_media": False})})
    g["i"] = {"class_type": "PreviewImage", "inputs": {"images": ["2", 1]}}
    o, st = run(g)
    assert st["status_str"] == "success" and [image_info(e) for e in o["i"]["images"]] == BLANK
    # and gives them on the reference file
    g = cof_chain(REF, extra={"2": ("COFMemories", {"pick": "memory_001", "media": "all", "include_personal": False, "include_text_media": False})})
    g["i"] = {"class_type": "PreviewImage", "inputs": {"images": ["2", 1]}}
    o, st = run(g)
    assert st["status_str"] == "success" and real_batch([image_info(e) for e in o["i"]["images"]], 3)


@pytest.mark.parametrize("f", ["clara-martien.cof", "ex-01-clara-text-only.cof", "ex-03-clara-photos.cof", "ex-11-duo-clara-martien.cof"])
def test_illus_on_files_without_psyche(f):
    need(f)
    for fmt in ("soul", "lines", "prose"):
        ui, b, n, t, _ = illus(f, format=fmt)
        assert ui == [] and b == BLANK and n == 0 and t == ""


def test_memory_images_still_work_next_to_psyche_illustrations():
    need(REF)
    inputs = {"format": "lines", "language": "(preset)", "focus": "", "max_nodes": 0, "include_secret": False, "drop_dangerous": False,
              "include_personal": False, "show_illustrations": True, "output_illustrations": True}
    g = cof_chain(preset="en-65", extra={"2": ("COFPsyche", inputs),
                                         "3": ("COFMemories", {"pick": "all", "media": "all", "include_personal": False, "include_text_media": True})})
    g["i"] = {"class_type": "PreviewImage", "inputs": {"images": ["2", 3]}}
    g["m"] = {"class_type": "PreviewImage", "inputs": {"images": ["3", 1]}}
    o, st = run(g)
    assert st["status_str"] == "success", exec_error(st)
    mem = [image_info(e) for e in o["m"]["images"]]
    assert len(mem) >= 5 and all(s[0] > 64 for s in mem)
    assert [node_of(image_info(e)) for e in o["2"]["images"]] == ["hab1"] and real_batch([image_info(e) for e in o["i"]["images"]], 1)


# ---------------------------------------------------------------- all nodes x all files
ALL_FILES =["clara-martien.cof", "clara-mercier.cof", "ex-01-clara-text-only.cof", "ex-02-ecoliere.cof", "ex-03-clara-photos.cof", "ex-04-martien.cof",
             "ex-06-homme-medieval.cof", "ex-07-clara-modulaire.cof", "ex-08-agent-skills.cof", "ex-10-clara-mercier.cof", "ex-11-duo-clara-martien.cof"]


@pytest.mark.parametrize("f", ALL_FILES)
def test_every_file_loads_with_the_text_nodes(f):
    if not has_file(f):
        pytest.skip(f"{f} not in ComfyUI")
    g = cof_chain(f, extra={"2": ("COFPersonality", {}), "3": ("COFPsyche", {"format": "lines"}), "4": ("COFFiles", {"kind": "all", "pick": "all"}),
                           "5": ("COF3D", {"pick": "all"}), "6": ("COFMotion", {"pick": "all"}), "7": ("COFMemories", {"pick": "all", "media": "all", "include_text_media": False})})
    for k, outs in (("2", [0]), ("3", [0, 1, 2]), ("4", [0, 3]), ("5", [2]), ("6", [2]), ("7", [0, 5])):
        for i in outs:
            g[f"p{k}{i}"] = {"class_type": "PreviewAny", "inputs": {"source": [k, i]}}
    g["info"] = {"class_type": "PreviewAny", "inputs": {"source": ["1", 4]}}
    g["img"] = {"class_type": "PreviewImage", "inputs": {"images": ["1", 1]}}
    o, st = run(g, timeout=200)
    assert st["status_str"] == "success", exec_error(st)
    assert "rights" in "".join(o["info"]["text"]).lower() or "license" in "".join(o["info"]["text"]).lower()


# ---------------------------------------------------------------- 4. error paths
def _with_output(g, node="2", idx=0):
    g["out"] = {"class_type": "PreviewAny", "inputs": {"source": [node, idx]}}
    return g


def _alive_after():
    assert _alive()
    need(REF)
    t, _, n = psyche(format="lines")
    assert n == 22


def test_error_unknown_file():
    code, body = post_prompt(_with_output(cof_chain("nope.cof", extra={"2": ("COFPsyche", {"format": "soul"})})))
    assert code == 400
    msgs = json.dumps(body)
    assert "nope.cof" in msgs and "not in list" in msgs
    _alive_after()


def test_error_unknown_preset():
    need(REF)
    g = _with_output(cof_chain(preset="does-not-exist", extra={"2": ("COFPsyche", {"format": "soul"})}))
    try:
        o, st = run(g)
    except PromptRejected as e:
        assert "preset" in str(e).lower()
    else:
        err = exec_error(st)
        assert err and "preset" in err["exception_message"].lower() and "does-not-exist" in err["exception_message"], err
    _alive_after()


def test_error_unknown_character():
    need(REF)
    g = _with_output(cof_chain(character="nobody", extra={"2": ("COFPsyche", {"format": "soul"})}))
    try:
        o, st = run(g)
    except PromptRejected as e:
        assert "character" in str(e).lower()
    else:
        err = exec_error(st)
        assert err and "nobody" in err["exception_message"], err
    _alive_after()


@pytest.mark.parametrize("f", ["e2e-truncated.cof", "e2e-notzip.cof", "e2e-empty.cof", "e2e-badmanifest.cof", "e2e-nomanifest.cof", "e2e-badmember.cof"])
def test_error_corrupted_cof(f):
    need(f)
    g = cof_chain(f, extra={"2": ("COFPsyche", {"format": "soul"})})
    g["p"] = {"class_type": "PreviewAny", "inputs": {"source": ["2", 0]}}
    try:
        o, st = run(g)
    except PromptRejected as e:
        msg = str(e)
    else:
        err = exec_error(st)
        assert err, "a corrupted .cof must give an execution error"
        msg = err["exception_message"]
    assert msg.strip() and "Traceback" not in msg
    assert f in msg, msg                                             # every message starts with, or at least names, the file
    assert any(w in msg.lower() for w in ("cof", "zip", "manifest", "json", "crc")), msg
    _alive_after()


def test_error_bad_picks():
    need(REF)
    for cls, extra in (("COFMemories", {"pick": "memory_999", "media": "all"}), 
                       ("COFAppearance", {"kind": "all", "include_offered": True, "pick": "image_999"}), ("COFVoice", {"sample": "sample_999"})):
        g = cof_chain(extra={"2": (cls, extra)})
        g["p"] = {"class_type": "PreviewAny", "inputs": {"source": ["2", 0 if cls != "COFAppearance" else 2]}}
        try:
            o, st = run(g)
        except PromptRejected:
            continue
        err = exec_error(st)
        assert err and err["exception_message"].strip(), cls
    _alive_after()


# ---------------------------------------------------------------- more
def _presets(f):
    return [p["value"].split(" — ")[0] for p in _get("/cof/info?file=" + urllib.parse.quote(f))["presets"]]


def test_every_preset_of_clara_runs_and_soul_equals_cli():
    need(REF)
    ids = _presets(REF)
    assert len(ids) == 12
    for pid in ids:
        g = cof_chain(REF, preset=pid, extra={"2": ("COFPsyche", {"format": "soul"}), "3": ("COFPsyche", {"format": "prose"}), "4": ("COFPersonality", {}),
                                              "5": ("COFMemories", {"pick": "all", "media": "all", "include_text_media": True})})
        for k, i in (("2", 0), ("3", 0), ("4", 0), ("5", 0)):
            g[f"p{k}"] = {"class_type": "PreviewAny", "inputs": {"source": [k, i]}}
        g["img"] = {"class_type": "PreviewImage", "inputs": {"images": ["1", 1]}}
        o, st = run(g, timeout=180)
        assert st["status_str"] == "success", (pid, exec_error(st))
        if os.environ.get("COMFYUI_DIR") and CLI:
            assert "".join(o["p2"]["text"]).strip() == cli_text(REF, pid, "--keep-dangerous").strip(), pid


def test_reference_latents_without_refmods_is_a_clear_error_or_empty():
    need(REF)
    g = cof_chain(extra={"2": ("COFReferenceLatents", {"folder": "refmods"})})
    g["p"] = {"class_type": "PreviewAny", "inputs": {"source": ["2", 0]}}
    o, st = run(g)
    err = exec_error(st)
    assert err is None or err["exception_message"].strip()
    _alive_after()


def test_files_pick_out_of_range_is_an_error():
    need("ex-08-agent-skills.cof")
    g = cof_chain("ex-08-agent-skills.cof", extra={"2": ("COFFiles", {"kind": "all", "pick": "item_999"})})
    g["p"] = {"class_type": "PreviewAny", "inputs": {"source": ["2", 0]}}
    o, st = run(g)
    assert "item_999" in exec_error(st)["exception_message"]


def test_a_cof_rewritten_on_disk_is_reloaded():
    d = os.environ.get("COMFYUI_DIR")
    if not d:
        pytest.skip("COMFYUI_DIR not set")
    need(REF), need("e2e-risk.cof")
    models = Path(d) / "models" / "cof"
    tmp = models / "e2e-tmp.cof"
    try:
        tmp.write_bytes((models / "e2e-risk.cof").read_bytes())
        assert psyche("e2e-tmp.cof", format="lines")[2] == 20
        time.sleep(1.1)
        tmp.write_bytes((models / REF).read_bytes())
        assert psyche("e2e-tmp.cof", format="lines")[2] == 22
    finally:
        tmp.unlink(missing_ok=True)


def test_server_log_has_no_cof_traceback():
    d = os.environ.get("COMFYUI_LOG")
    if not d or not Path(d).exists():
        pytest.skip("COMFYUI_LOG not set")
    log = Path(d).read_text(errors="replace")
    bad = [l for l in log.splitlines() if "ComfyUI-COF" in l and ("Traceback" in l or "Error" in l) and "Cannot import" in l]
    assert not bad
