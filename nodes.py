"""ComfyUI nodes for COF characters. COF Load Character covers most workflows; then one node per category of
the COF builder (Personality, Appearance, Identity weights, 3D, Voice, Postures, Motion, Files), plus Memories (COF 1.1)
and Psyche (COF 1.2), each taking the loader's `cof` output."""
from __future__ import annotations

import io
import os
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

try:                                   # inside ComfyUI
    import folder_paths
except ImportError:                    # tests outside ComfyUI
    folder_paths = None

from .cof_reader import COF, COFError, FULL_LINE_STRENGTH, PSYCHE_ORDER, REF_KINDS, english, short_clause, strip_toward

REFS = "references (face, reference set, sheet, body)"
# the variant types of each category, in the builder's order; "all" (and, for appearance, "references") come first
KINDS = {
    "appearance": [REFS, "all", "face", "body", "reference-set", "character-sheet", "hair", "clothing", "accessory",
                   "pilosity", "feature", "environment", "intimate"],
    "posture": ["all", "photo", "photos", "openpose", "silhouette"],
    "files": ["all", "skill", "memory", "document", "other"],
}
ALWAYS = {"appearance": 2, "posture": 1, "files": 1}       # how many leading choices are always listed
# builder icons (the builder's LoRA icon is a drawing: 🧬 stands for it)
ICON = {"load": "👤", "memories": "🕰️", "psyche": "🧠", "personality": "📝", "appearance": "🖼️", "weights": "🧬", "volume3d": "🧊", "voice": "👄",
        "posture": "🏃", "motion": "🎥", "files": "📁"}


def _register_folder():
    if folder_paths is None:
        return
    if "cof" not in folder_paths.folder_names_and_paths:
        folder_paths.folder_names_and_paths["cof"] = ([os.path.join(folder_paths.models_dir, "cof")], {".cof"})
    else:                                                  # registered by an older copy of these nodes with every extension: .cof only
        paths, _ = folder_paths.folder_names_and_paths["cof"]
        folder_paths.folder_names_and_paths["cof"] = (paths, {".cof"})
    inp = os.path.join(folder_paths.get_input_directory(), "cof")
    paths = folder_paths.folder_names_and_paths["cof"][0]
    if inp not in paths:
        paths.append(inp)


_register_folder()
_CACHE: dict[str, tuple[float, COF]] = {}
DEFAULT = "(default)"
SEP = " — "


def _open(name: str) -> COF:
    path = folder_paths.get_full_path("cof", name) if folder_paths else name
    if not path or not os.path.isfile(path):
        raise COFError(f"COF file not found: {name} (put .cof files in ComfyUI/models/cof or ComfyUI/input/cof)")
    mt = os.path.getmtime(path)
    hit = _CACHE.get(path)
    if hit and hit[0] == mt:
        return hit[1]
    c = COF(path)
    _CACHE[path] = (mt, c)
    return c


def _files() -> list[str]:
    """The .cof files of models/cof and input/cof — only those (the clips the Motion node extracts live in subfolders there)."""
    return [f for f in (folder_paths.get_filename_list("cof") if folder_paths else []) if f.lower().endswith(".cof")]


def _choices() -> tuple[list[str], list[str]]:
    """Every preset and character of every .cof file, as 'id — label'. The browser extension (web/cof.js)
    narrows the lists to the selected file and character; ComfyUI validates against the full lists."""
    presets, chars = [DEFAULT], [DEFAULT]
    for f in _files():
        try:
            c = _open(f)
        except Exception:
            continue
        for pid, label, _ in c.preset_choices():
            v = f"{pid}{SEP}{label}"
            if v not in presets:
                presets.append(v)
        for iid, name in c.character_choices():
            v = f"{iid}{SEP}{name}"
            if v not in chars:
                chars.append(v)
    return presets, chars


def _pick(v: str) -> str:
    """'(default)' → '', 'id — label' → 'id'; free text (API workflows) is kept as is."""
    v = (v or "").strip()
    return "" if v in ("", DEFAULT) else v.split(SEP, 1)[0].strip()


PAGE = 24                  # one limit: a mosaic holds at most 24 images (mosaic_all up to 24, otherwise pages of 24)


def _token(v: str) -> str:
    return (v or "").split(SEP, 1)[0].strip()


def _image_choices(labels: list[str]) -> list[str]:
    """Dropdown values for a list of images: all of them as one mosaic (24 at most) or, beyond 24, pages of 24;
    then each image by name."""
    n = len(labels)
    if n <= PAGE:
        out = [f"mosaic_all{SEP}{n} image{'s' if n != 1 else ''}"]
    else:
        out = [f"mosaic_page_{p + 1}{SEP}images {p * PAGE + 1}–{min(n, (p + 1) * PAGE)} of {n}" for p in range((n + PAGE - 1) // PAGE)]
    return out + [f"image_{i + 1:03d}{SEP}{lab}" for i, lab in enumerate(labels)]


def _select(items: list, pick: str, what: str = "image"):
    """mosaic_all / mosaic_page_N / image_NNN (or all / item_NNN, sample_NNN) → the chosen items."""
    t = _token(pick) or "mosaic_all"
    if t == "all":
        return items
    if t == "mosaic_all":
        if len(items) > PAGE:
            raise COFError(f"{len(items)} images match: a mosaic holds {PAGE} at most — choose mosaic_page_1, mosaic_page_2… in the list")
        return items
    m = re.fullmatch(r"mosaic_page_(\d+)", t)
    if m:
        k = int(m.group(1))
        return items[(k - 1) * PAGE:k * PAGE]
    m = re.fullmatch(r"(?:image|item|sample|memory)_(\d+)", t)
    if m:
        k = int(m.group(1))
        if not 1 <= k <= len(items):
            raise COFError(f"{t}: this preset has {len(items)} {what if len(items) == 1 else 'memories' if what == 'memory' else what + 's'} — choose again in the list")
        return [items[k - 1]]
    raise COFError(f"unknown choice '{pick}'")


def _image_ids(c: COF, pid: str, kind: str, include_offered: bool, allow_intimate: bool, subject: str = "any",
               angle: str = "any", cat: str = "appearance") -> list[str]:
    kind = _token(kind)                  # "clothing — 4 images" → "clothing"
    kinds = None if kind in ("all", "") else set(REF_KINDS) if kind.startswith("references") else {kind}
    return c.images(pid, kinds=kinds, include_offered=include_offered, allow_intimate=allow_intimate,
                    subject="" if subject == "any" else subject, angle="" if angle == "any" else angle, cats={cat})


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


def _kind_choices(c: COF, pid: str, include_offered: bool, allow_intimate: bool, current: str = "", cat: str = "appearance") -> list[str]:
    """The variant types of one category that this preset really has, with their counts. The leading choices
    ("references", "all") and the node's current type always appear (with 0 if the preset has none of it)."""
    out, cur, kinds = [], _token(current), KINDS[cat]
    for i, k in enumerate(kinds):
        if cat == "files":
            n, word = len(c.media(pid, "files", None if k == "all" else {k})), "file"
        else:
            n, word = len(_image_ids(c, pid, k, include_offered, allow_intimate, cat=cat)), "image"
        if n or i < ALWAYS[cat] or k == cur:
            out.append(f"{k}{SEP}{_plural(n, word)}")
    return out


def _media_choices(items: list[dict]) -> list[str]:
    return [f"all{SEP}{_plural(len(items), 'file')}"] + [f"item_{i + 1:03d}{SEP}{x['label']}" for i, x in enumerate(items)]


def _voice_samples(c: COF, pid: str) -> list[tuple[str, dict]]:
    return [(aid, v) for v in c.voices(pid) for aid in (v.get("samples") or [])]


def _voice_label(v: dict) -> str:
    return f"{english(v.get('label') or v['_id'])} [{v.get('language', '?')}]"


def _to_tensor(images: list[Image.Image], max_size: int):
    """Batch of RGB images as a ComfyUI IMAGE tensor [B, H, W, 3] in 0..1; letterboxed to one size."""
    import torch
    if not images:
        return torch.zeros((1, 64, 64, 3), dtype=torch.float32)
    ims = []
    for im in images:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((max_size, max_size), Image.LANCZOS)
        ims.append(im)
    W = max(i.width for i in ims); H = max(i.height for i in ims)
    out = []
    for im in ims:
        canvas = Image.new("RGB", (W, H), (255, 255, 255))
        canvas.paste(im, ((W - im.width) // 2, (H - im.height) // 2))
        out.append(np.asarray(canvas, dtype=np.float32) / 255.0)
    return torch.from_numpy(np.stack(out))


def _images(cof: COF, aids: list[str]) -> list[Image.Image]:
    out = []
    for aid in aids:
        data = cof.asset_bytes(aid)
        if data:
            try:
                im = Image.open(io.BytesIO(data)); im.load()             # load now: a truncated body fails here, not in the tensor conversion
                out.append(im)
            except Exception as ex:                                      # a damaged or unsupported image: skipped, said once
                print(f"[COF] image {(cof.assets.get(aid) or {}).get('path', aid)}: {ex}")
    return out


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "character").lower()).strip("-") or "character"


class COFLoadCharacter:
    """Simple node: a .cof file + a preset → its primary image, its reference images, a prompt."""

    @classmethod
    def INPUT_TYPES(cls):
        files = _files()
        presets, chars = _choices()
        return {"required": {
            "cof_file": (files or ["(put .cof files in models/cof or input/cof)"],),
            "preset": (presets, {"default": DEFAULT, "tooltip": "the presets of the selected file and character; (default) = the character's default preset"}),
            "character": (chars, {"default": DEFAULT, "tooltip": "for a file with several characters; (default) = the file's default character"}),
            "max_size": ("INT", {"default": 1024, "min": 64, "max": 4096, "step": 64}),
        }, "optional": {
            "allow_intimate": ("BOOLEAN", {"default": False, "tooltip": "adult content: variants marked nudity (preset 18+, consent for a real person) and intimate ones (also needs the preset's effective allowSexualUsage)"}),
        }}

    @classmethod
    def VALIDATE_INPUTS(cls, preset, character):
        return True                      # older workflows may give a preset id or label as free text

    RETURN_TYPES = ("COF", "IMAGE", "IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("cof", "primary_image", "references", "prompt", "info")
    OUTPUT_TOOLTIPS = ("the loaded character, for the advanced COF nodes",
                       "one image: the preset's main image",
                       "a batch of every reference view (face, reference set, character sheet, body) — for nodes that take several reference images; pick one with COF Appearance",
                       "a ready text prompt for this preset", "rights, age, content flags and the presets of this character")
    FUNCTION = "load"
    CATEGORY = "COF"
    DESCRIPTION = "Loads a character from a COF (Character Open File): primary image, reference views and a ready prompt for the chosen preset."

    def load(self, cof_file, preset, character, max_size, allow_intimate=False):
        cof = _open(cof_file)
        pid = cof.find_preset(_pick(preset), _pick(character))
        prim = cof.primary_image(pid, allow_adult=cof.nudity_allowed(pid, allow_intimate))
        refs = cof.images(pid, kinds=set(REF_KINDS), allow_intimate=allow_intimate)
        sel = {"file": cof_file, "preset": pid, "allow_intimate": cof.intimate_allowed(pid, allow_intimate), "max_size": max_size}
        return ({"cof": cof, **sel}, _to_tensor(_images(cof, [prim] if prim else []), max_size),
                _to_tensor(_images(cof, refs), max_size), cof.prompt(pid), cof.summary(pid))


class _ImageNode:
    """Shared by COF Appearance and COF Postures: images of one category of the preset, by type, applied or also
    offered; one image, a page or all of them."""
    CAT = "appearance"
    RETURN_TYPES = ("IMAGE", "INT", "STRING")
    RETURN_NAMES = ("images", "count", "labels")
    OUTPUT_TOOLTIPS = ("the chosen image, page or batch", "how many images match", "one line per matching image: its name and what it shows")
    FUNCTION = "pick_images"
    CATEGORY = "COF"
    @classmethod
    def _pick(cls):
        return (["mosaic_all"] + [f"image_{i:03d}" for i in range(1, 65)], {"default": "mosaic_all",
                "tooltip": "the images that match, by name: mosaic_all = all of them as one batch (up to 24); beyond 24, mosaic_page_1, mosaic_page_2… = 24 at a time; image_NNN = one image"})

    @classmethod
    def VALIDATE_INPUTS(cls, kind, pick):
        return True                      # both lists are filled per file by web/cof.js

    def pick_images(self, cof, kind, include_offered, pick="mosaic_all", subject="any", angle="any"):
        c, pid = cof["cof"], cof["preset"]
        aids = _image_ids(c, pid, kind, include_offered, cof["allow_intimate"], subject, angle, self.CAT)
        labels = "\n".join(f"image_{i + 1:03d}: {c.image_label(a)}" for i, a in enumerate(aids)) \
            or f"(this preset has no {_token(kind) if _token(kind) not in ('all', '') and not _token(kind).startswith('references') else ('posture' if self.CAT == 'posture' else 'such')} image: a blank 64×64 image is returned)"
        chosen = _select(aids, pick) if aids else []
        return (_to_tensor(_images(c, chosen), cof["max_size"]), len(aids), labels)


OFFERED = ("BOOLEAN", {"default": True, "tooltip": "also the variants offered with this preset (outfits, accessories, poses… available but not applied by default)"})


class COFAppearance(_ImageNode):
    """Appearance: face, body, reference set, character sheet, hair, clothing, accessories, facial and body hair,
    features, environments (and intimate variants, only when the loader allows them and the file permits it)."""
    CAT = "appearance"

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "cof": ("COF",),
            "kind": (KINDS["appearance"], {"tooltip": "the appearance types this preset has, with their number of images"}),
            "include_offered": OFFERED,
            "pick": cls._pick(),
        }, "optional": {
            "subject": (["any", "head", "body", "hands", "hair", "outfit", "detail"],),
            "angle": (["any", "front", "left34", "left", "leftback34", "back", "rightback34", "right", "right34", "top", "low34"],),
        }}


class COFPostures(_ImageNode):
    """Postures: pose photos, OpenPose and silhouette images (for pose-guided generation)."""
    CAT = "posture"

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "cof": ("COF",),
            "kind": (KINDS["posture"], {"tooltip": "the posture types this preset has, with their number of images"}),
            "include_offered": OFFERED,
            "pick": cls._pick(),
        }}


class COFApplyLoRA:
    """Advanced: applies the preset's embedded LoRA (with its recommended weight) and returns its trigger words."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "model": ("MODEL",), "clip": ("CLIP",), "cof": ("COF",),
            "strength": ("FLOAT", {"default": -1.0, "min": -1.0, "max": 2.0, "step": 0.05, "tooltip": "-1 = the weight recommended in the file"}),
        }}

    RETURN_TYPES = ("MODEL", "CLIP", "STRING")
    RETURN_NAMES = ("model", "clip", "trigger_words")
    FUNCTION = "apply"
    CATEGORY = "COF"

    def apply(self, model, clip, cof, strength):
        import comfy.sd
        import comfy.utils
        c, pid = cof["cof"], cof["preset"]
        ws = c.weights(pid)
        if not ws:
            return (model, clip, "")
        if c.is_real_without_consent(pid):
            print("[COF] WARNING: real person without a consent record — you are responsible for this use (COF § 9.2)")
        cache = Path(folder_paths.get_temp_directory() if folder_paths else ".") / "cof" / _slug(c.path.stem)
        words = []
        for w in ws:
            if not w["asset"]:
                continue
            bm = (w["spec"].get("base_model") or {}).get("name", "?")
            print(f"[COF] applying {w['variant']['_id']} (base model: {bm})")
            f = c.extract(w["asset"]["id"], cache)
            sd = comfy.utils.load_torch_file(str(f), safe_load=True)
            s = w["spec"].get("recommended_weight", 0.8) if strength < 0 else strength
            model, clip = comfy.sd.load_lora_for_models(model, clip, sd, s, s)
            words += w["spec"].get("trigger_words") or []
        return (model, clip, ", ".join(words))


class COFReferenceLatents:
    """Advanced: exports the preset's pre-encoded references (kind reference-latent, e.g. MiniMax H3 RefMods)
    into a models folder, so that the matching loader nodes can pick them up."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"cof": ("COF",), "folder": ("STRING", {"default": "refmods", "tooltip": "models subfolder to export to"})}}

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("paths", "base_models")
    FUNCTION = "export"
    CATEGORY = "COF"

    def export(self, cof, folder):
        c, pid = cof["cof"], cof["preset"]
        if c.is_real_without_consent(pid):
            print("[COF] WARNING: real person without a consent record — you are responsible for this use (COF § 9.2)")
        root = Path(folder_paths.models_dir if folder_paths else ".") / re.sub(r"[^A-Za-z0-9_.-]", "", folder) / "cof" / _slug(c.path.stem)
        paths, bases = [], []
        for w in c.weights(pid, ("reference-latent",)):
            if w["asset"]:
                paths.append(str(c.extract(w["asset"]["id"], root)))
                bases.append((w["spec"].get("base_model") or {}).get("name", "?"))
        return ("\n".join(paths), "\n".join(bases))


def _load_audio(path: str):
    """(waveform [channels, samples], sample_rate) with whatever ComfyUI offers: its own loader, torchaudio, or PyAV."""
    try:
        from comfy_extras.nodes_audio import load as comfy_load
        return comfy_load(path)
    except Exception:
        pass
    try:
        import torchaudio
        return torchaudio.load(path)
    except Exception:
        pass
    import av
    import torch
    with av.open(path) as f:
        st = f.streams.audio[0]; sr = st.rate; chunks = []
        for fr in f.decode(st):
            a = fr.to_ndarray().astype(np.float32)
            if fr.format.is_packed:
                a = a.reshape(-1, len(fr.layout.channels)).T
            if np.issubdtype(fr.to_ndarray().dtype, np.integer):
                a = a / float(np.iinfo(fr.to_ndarray().dtype).max)
            chunks.append(a)
    return torch.from_numpy(np.concatenate(chunks, axis=1)), sr


class COFVoice:
    """Advanced: a voice sample of the preset as AUDIO (for voice-cloning, text-to-speech or lip-sync nodes)."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"cof": ("COF",), "sample": ([f"sample_{i:03d}" for i in range(1, 17)], {"default": "sample_001", "tooltip": "the list shows the preset's voice samples by name and language"})}}

    @classmethod
    def VALIDATE_INPUTS(cls, sample):
        return True

    RETURN_TYPES = ("AUDIO", "STRING", "STRING")
    RETURN_NAMES = ("audio", "transcript", "labels")
    FUNCTION = "voice"
    CATEGORY = "COF"

    def voice(self, cof, sample="sample_001"):
        c, pid = cof["cof"], cof["preset"]
        if c.is_real_without_consent(pid):
            print("[COF] WARNING: real person without a consent record — you are responsible for this use (COF § 9.2)")
        samples = _voice_samples(c, pid)
        if not samples:                  # a preset without voice must not stop the whole workflow
            import torch
            print(f"[COF] preset {pid} has no voice sample: one second of silence instead")
            return ({"waveform": torch.zeros((1, 1, 44100)), "sample_rate": 44100}, "", "(this preset has no voice sample)")
        labels = "\n".join(f"sample_{i + 1:03d}: {_voice_label(v)}" for i, (_, v) in enumerate(samples))
        aid = _select(samples, sample, "voice sample")[0][0]
        tmp = Path(folder_paths.get_temp_directory() if folder_paths else ".") / "cof" / _slug(c.path.stem)
        f = c.extract(aid, tmp)
        wave, sr = _load_audio(str(f))
        tr = ""
        t = (c.assets.get(aid) or {}).get("transcript")
        if t:
            try:
                tr = c.read(t).decode("utf-8")
            except Exception:
                tr = ""
        return ({"waveform": wave.unsqueeze(0), "sample_rate": sr}, tr, labels)


class COFPersonality:
    """Personality: the preset's personality as plain text (for a language-model node: system prompt, persona)."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"cof": ("COF",)}}

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("persona", "language")
    FUNCTION = "text"
    CATEGORY = "COF"

    def text(self, cof):
        return cof["cof"].persona(cof["preset"])


class _FileNode:
    """Shared by COF 3D and COF Motion: exports the preset's files of one category to ComfyUI's input folder and
    returns their paths (3D files go to input/3d, where Load 3D finds them)."""
    CAT, SUB = "volume3d", "3d"
    RETURN_TYPES = ("STRING", "STRING", "INT")
    RETURN_NAMES = ("paths", "labels", "count")
    FUNCTION = "export"
    CATEGORY = "COF"

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"cof": ("COF",),
                             "pick": (["all"] + [f"item_{i:03d}" for i in range(1, 65)], {"default": "all", "tooltip": "the preset's files by name; all = every one"})}}

    @classmethod
    def VALIDATE_INPUTS(cls, pick):
        return True

    def export(self, cof, pick="all"):
        c, pid = cof["cof"], cof["preset"]
        items = c.media(pid, self.CAT)
        labels = "\n".join(f"item_{i + 1:03d}: {x['label']}" for i, x in enumerate(items)) or f"(this preset has no {self.WHAT})"
        chosen = _select(items, "all" if _token(pick) == "all" else pick, "file") if items else []
        dest = Path(folder_paths.get_input_directory() if folder_paths else ".") / self.SUB / f"cof-{_slug(c.path.stem)}"
        paths = [str(c.extract(x["asset"], dest) if x.get("asset") else c.extract_path(x["path"], dest)) for x in chosen]
        return ("\n".join(paths), labels, len(items))


class COF3D(_FileNode):
    """3D: the preset's 3D files (VRM, GLB, USDZ, splat, 3MF…), exported to input/3d."""
    CAT, SUB, WHAT = "volume3d", "3d", "3D file"


class COFMotion(_FileNode):
    """Motion: the preset's motion clips (BVH, VRMA) and short videos, exported to input/cof."""
    CAT, SUB, WHAT = "motion", "cof", "motion clip"


class COFFiles:
    """Files: the preset's agent skills, memories and documents. Text files only (Markdown, text, JSON, YAML,
    CSV) are read and exported — never scripts; a skill keeps its folder layout."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"cof": ("COF",),
                             "kind": (KINDS["files"], {"tooltip": "the file types this preset has, with their counts"}),
                             "pick": (["all"] + [f"item_{i:03d}" for i in range(1, 65)], {"default": "all", "tooltip": "the files by name; all = every one"})}}

    @classmethod
    def VALIDATE_INPUTS(cls, kind, pick):
        return True

    RETURN_TYPES = ("STRING", "STRING", "STRING", "INT")
    RETURN_NAMES = ("text", "paths", "labels", "count")
    OUTPUT_TOOLTIPS = ("the chosen text files, one after the other, each under its path", "where they were exported",
                       "one line per file", "how many files match")
    FUNCTION = "export"
    CATEGORY = "COF"

    def export(self, cof, kind="all", pick="all"):
        c, pid = cof["cof"], cof["preset"]
        k = _token(kind)
        items = c.media(pid, "files", None if k in ("all", "") else {k})
        labels = "\n".join(f"item_{i + 1:03d}: {x['label']}" for i, x in enumerate(items)) or ("(this preset has no file)" if k in ("all", "") else f"(this preset has no {k} file)")
        chosen = [x for x in (_select(items, "all" if _token(pick) == "all" else pick, "file") if items else []) if x.get("asset")]
        dest = Path(folder_paths.get_input_directory() if folder_paths else ".") / "cof" / f"cof-{_slug(c.path.stem)}" / "files"
        texts = [f"### {x['rel'].split('/' + x['variant'] + '/', 1)[-1]}\n\n{c.read_text(x['asset']).strip()}" for x in chosen]
        paths = [str(c.extract_text_file(x["asset"], x["variant"], dest)) for x in chosen]
        return ("\n\n".join(texts), "\n".join(paths), labels, len(items))


def _memory_media(c: COF, cof: dict, chosen: list[dict]) -> list[tuple[dict, dict]]:
    """(memory, asset) for every media of the chosen memories — none of an explicit memory without the opt-in (RFC 0002 § 5)."""
    out = []
    for m in chosen:
        if m.get("explicit", m["node"].get("explicit")) and not cof["allow_intimate"]:
            continue
        out += [(m, c.assets.get(a) or {}) for a in m["media"] if c.assets.get(a)]
    return out


def _memory_image_choices(pairs: list[tuple[dict, dict]]) -> list[str]:
    imgs = [(m, a) for m, a in pairs if str(a.get("mediaType") or "").startswith("image/") and not a.get("derived_from")]
    return [f"all{SEP}{len(imgs)} image{'s' if len(imgs) != 1 else ''}"] + [f"image_{i + 1:03d}{SEP}{english(m['label'])} · {Path(a.get('path') or a['id']).name}" for i, (m, a) in enumerate(imgs)]


class COFMemories:
    """Memories (COF 1.1): the preset's memories in timeline order — the gists as one text for a language model,
    the images of the memories as a batch (a memory's explicit media only when the loader allows adult content),
    one line per memory. Personal memories (a user's data) are left out unless asked."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"cof": ("COF",),
                             "pick": (["all"] + [f"memory_{i:03d}" for i in range(1, 65)], {"default": "all", "tooltip": "the memories by name, oldest first; all = every one"})},
                "optional": {"media": (["all"] + [f"image_{i:03d}" for i in range(1, 25)], {"default": "all", "tooltip": "one image of the chosen memory (image_001…), or all of them as a batch"}),
                             "include_personal": ("BOOLEAN", {"default": False, "tooltip": "also the memories marked personal (a user's data, never shared by default)"}),
                             "include_text_media": ("BOOLEAN", {"default": True, "tooltip": "append the short text media of each memory (remembered lines) after its gist"})}}

    @classmethod
    def VALIDATE_INPUTS(cls, pick, media="all"):
        return True

    RETURN_TYPES = ("STRING", "IMAGE", "AUDIO", "STRING", "STRING", "INT")
    RETURN_NAMES = ("text", "images", "audio", "media_paths", "labels", "count")
    OUTPUT_TOOLTIPS = ("the memories as text, oldest first: a heading per memory with its when-marker, then the gist (in the preset's language) and its remembered lines",
                       "the images of the chosen memories, or the one image chosen in `media` (a blank image when there is none)",
                       "the first sound of the chosen memories (silence when there is none) — for a voice or lip-sync node",
                       "the sound and video files of the chosen memories, extracted under input/cof/ — one path per line, for a node that loads a video or a sound from a path",
                       "one line per memory: its name and when", "how many memories the preset lists")
    FUNCTION = "recall"
    CATEGORY = "COF"
    DESCRIPTION = "The character's memories (COF 1.1): gists in timeline order for a prompt, and their images."

    def recall(self, cof, pick="all", media="all", include_personal=False, include_text_media=True):
        c, pid = cof["cof"], cof["preset"]
        if not hasattr(c, "memories"):          # the loader came from another copy of ComfyUI-COF (0.5.0 still in custom_nodes): reopen with this one
            c = COF(c.path)
            print("[ComfyUI-COF] COF Memories: the file was loaded by an older ComfyUI-COF — keep a single ComfyUI-COF folder in custom_nodes and restart")
        mems = c.memories(pid, include_personal)
        labels = "\n".join(f"memory_{i + 1:03d}: {english(m['label'])} — {m['when']}" for i, m in enumerate(mems)) or "(this preset has no memory)"
        chosen = _select(mems, "all" if _token(pick) == "all" else pick, "memory") if mems else []
        lines, aids = [], []
        for m in chosen:
            n = m["node"]
            marks = [x for x in (n.get("nature") or []) if x != "experienced"]
            marks += [x for x in (n.get("disclosure"), n.get("fidelity")) if x and x not in ("open", "clear")]
            if m.get("explicit", n.get("explicit")):
                marks.append("explicit")
            lines.append(f"### {m['label']}  ·  {m['when']}" + (f"  ·  {' · '.join(marks)}" if marks else ""))
            lines.append(m["gist"].strip())
            if m.get("explicit", n.get("explicit")) and not cof["allow_intimate"]:
                media_ = []                                                  # RFC 0002 § 5: never without the opt-in
            else:
                media_ = [c.assets.get(a) or {} for a in m["media"]]
            if include_text_media:
                for a in media_:
                    if str(a.get("mediaType") or "").startswith("text/") and (not a.get("language") or a["language"].split("-")[0] == m["lang"].split("-")[0]):
                        try:
                            lines.append(c.read_text(a["id"]).strip())
                        except COFError:
                            pass
            aids += [a["id"] for a in media_ if str(a.get("mediaType") or "").startswith("image/") and not a.get("derived_from")]
            lines.append("")
        t = _token(media)
        m_ = re.fullmatch(r"image_(\d+)", t)
        if m_:                                                               # one image of the chosen memories
            k = int(m_.group(1))
            if not 1 <= k <= len(aids):
                raise COFError(f"{t}: the chosen memories have {len(aids)} image(s)")
            aids = [aids[k - 1]]
        # the other media: sounds, videos and texts, extracted for the nodes that read files; the first sound as AUDIO
        pairs = _memory_media(c, cof, chosen)
        dest = Path(folder_paths.get_input_directory() if folder_paths else ".") / "cof" / f"cof-{_slug(c.path.stem)}" / "memories"
        paths, audio = [], None
        for m, a in pairs:
            mt = str(a.get("mediaType") or "")
            if not (mt.startswith("audio/") or mt.startswith("video/")):   # images are the batch; texts are in the text output
                continue
            try:
                f = c.extract(a["id"], dest / _slug(m["id"]))                # the id is manifest data: slugged, never a path
            except COFError as ex:
                print(f"[COF] memory media {a.get('path')}: {ex}")
                continue
            paths.append(str(f))
            if audio is None and mt.startswith("audio/"):
                try:
                    wave, sr = _load_audio(str(f))
                    audio = {"waveform": wave.unsqueeze(0), "sample_rate": sr}
                except Exception as ex:
                    print(f"[COF] memory sound {a.get('path')}: {ex}")
        if audio is None:
            import torch
            audio = {"waveform": torch.zeros((1, 1, 44100)), "sample_rate": 44100}
        return ("\n".join(lines).strip(), _to_tensor(_images(c, aids), cof["max_size"]), audio, "\n".join(paths), labels, len(mems))


# ---- COF Psyche (COF 1.2, RFC 0004) ---------------------------------------------------------------------------------

# the words of cof-cli's "Who she is" section (SOUL.md), English and French; another language gets the English words
PSYCHE_WORDS = {
    "en": {"title": "Who {pron} is", "pron": {"female": "she", "male": "he"}, "pron_default": "they",
           "intro": "Rendered from the psyche nodes (RFC 0004) in a fixed order: wound, values, beliefs, wants and needs, fears, stances, tastes, habits, contradictions, rules. "
                    "A whole sentence weighs most (strength {full} or more); a short clause weighs less. Markers: `lie` — a belief {pron} holds that is false; "
                    "`guarded` — not opened with, only when it matters; `lived` — came from use, not from the author.",
           "kinds": {"wound": "Wound", "value": "Values", "belief": "Beliefs", "want": "Wants", "need": "Needs", "fear": "Fears", "stance": "Stances",
                     "taste": "Tastes", "habit": "Habits", "contradiction": "Contradictions", "rule": "Rules"},
           "secret": "There are things {pron} does not tell.", "lie": "lie", "guarded": "guarded", "lived": "lived", "about": "about",
           "toward": "toward", "inappropriate": "inappropriate", "dangerous": "dangerous", "secret_mark": "secret"},
    "fr": {"title": "Qui {pron} est", "pron": {"female": "elle", "male": "il"}, "pron_default": "iel",
           "intro": "Rendu depuis les nœuds de la psyché (RFC 0004) dans un ordre fixe : blessure, valeurs, croyances, désirs et besoins, peurs, positions, goûts, habitudes, contradictions, règles. "
                    "Une phrase entière pèse le plus (force {full} ou plus) ; une courte proposition pèse moins. Marqueurs : `mensonge` — une croyance qu'{pron} tient pour vraie et qui est fausse ; "
                    "`réservé` — jamais d'emblée, seulement quand cela compte ; `vécu` — venu de l'usage, pas de l'auteur.",
           "kinds": {"wound": "Blessure", "value": "Valeurs", "belief": "Croyances", "want": "Désirs", "need": "Besoins", "fear": "Peurs", "stance": "Positions",
                     "taste": "Goûts", "habit": "Habitudes", "contradiction": "Contradictions", "rule": "Règles"},
           "secret": "Il y a des choses qu'{pron} ne dit pas.", "lie": "mensonge", "guarded": "réservé", "lived": "vécu", "about": "envers",
           "toward": "envers", "inappropriate": "inapproprié", "dangerous": "dangereux", "secret_mark": "secret"},
}
LANGUAGES = ["(preset)", "en", "fr", "de", "es", "it", "pt", "nl", "ja", "ko", "zh", "ru"]


def _psyche_words(lang: str) -> dict:
    return PSYCHE_WORDS["fr" if (lang or "").lower().startswith("fr") else "en"]


def _psyche_marks(n: dict, W: dict, include_secret: bool) -> list[str]:
    """The markers after a node's text: lie, the subject of a stance, guarded, lived, secret (when shown), inappropriate, dangerous (when kept)."""
    marks = []
    if n["lie"]:
        marks.append(W["lie"])
    if n["kind"] == "stance" and n["subject"]:
        marks.append(f"{W['about']}: {n['subject']}")
    if n["disclosure"] == "guarded":
        marks.append(W["guarded"])
    if n["disclosure"] == "secret" and include_secret:
        marks.append(W["secret_mark"])
    if n["origin"] == "lived":
        marks.append(W["lived"])
    if n["risk"] in ("inappropriate", "dangerous"):
        marks.append(W[n["risk"]])
    return marks


def _by_kind(nodes: list[dict]) -> list[tuple[str, list[dict]]]:
    """The nodes grouped in cof-cli's fixed kind order (an unknown kind after the known ones), slot order inside a kind."""
    kinds = list(PSYCHE_ORDER) + sorted({n["kind"] for n in nodes} - set(PSYCHE_ORDER))
    return [(k, [n for n in nodes if n["kind"] == k]) for k in kinds if any(n["kind"] == k for n in nodes)]


def _psyche_line(n: dict) -> str:
    return n["gist"] if n["strength"] >= FULL_LINE_STRENGTH else short_clause(n["gist"])


def psyche_soul(nodes: list[dict], lang: str, pron_gender: str | None, include_secret: bool) -> str:
    """cof-cli's "Who she is" section (RFC 0004 § 3 quinquies), reproduced: title, intro, one heading per kind in the fixed
    order, one line per node (the whole gist at strength ≥ 70, a short clause below), markers, secret nodes left out with
    one line saying so — plus the `inappropriate` / `dangerous` markers cof-cli does not write."""
    W = _psyche_words(lang)
    pron = W["pron"].get(pron_gender or "", W["pron_default"])
    lines = [f"## {W['title'].format(pron=pron)}", "", W["intro"].format(full=FULL_LINE_STRENGTH, pron=pron), ""]
    secret = 0
    for kind, group in _by_kind(nodes):
        rows = []
        for n in group:
            if n["disclosure"] == "secret" and not include_secret:
                secret += 1; continue
            marks = _psyche_marks(n, W, include_secret)
            rows.append(f"- {_psyche_line(n)}" + (f"  ·  {' · '.join(marks)}" if marks else ""))
        if rows:
            lines += [f"### {W['kinds'].get(kind, kind.capitalize())}", *rows, ""]
    if secret:
        lines += [W["secret"].format(pron=pron), ""]
    return "\n".join(lines).rstrip() + "\n"


def psyche_lines(nodes: list[dict], lang: str, include_secret: bool) -> str:
    """One line per node, `kind · gist` (the whole gist) with the markers, in the fixed kind order — for prompt building."""
    W = _psyche_words(lang)
    out = []
    for _, group in _by_kind(nodes):
        for n in group:
            if n["disclosure"] == "secret" and not include_secret:
                continue
            marks = _psyche_marks(n, W, include_secret)
            out.append(f"{n['kind']} · {n['gist']}" + (f"  ·  {' · '.join(marks)}" if marks else ""))
    return "\n".join(out)


PROSE_TRAITS = 8           # prose: after the values and the stances, the strongest other nodes, this many at most


def psyche_prose(nodes: list[dict], lang: str, include_secret: bool) -> str:
    """One compact paragraph for an image or video generator: the values first, the stances next (each with its subject),
    then the strongest other nodes as short clauses, grouped by kind."""
    W = _psyche_words(lang)
    shown = [n for n in nodes if include_secret or n["disclosure"] != "secret"]
    sentence = lambda s: s[:1].upper() + s[1:] + ("" if s.endswith(("…", ".", "!", "?")) else ".")
    parts = []
    values = [short_clause(n["gist"]) for n in shown if n["kind"] == "value"]
    if values:
        parts.append(sentence(f"{W['kinds']['value']}: " + "; ".join(values)))
    stances = []
    for n in shown:
        if n["kind"] == "stance":
            cl = short_clause(n["gist"])
            stances.append(f"{W['toward']} {n['subject']}: {strip_toward(cl)}" if n["subject"] else cl)
    if stances:
        parts.append(sentence("; ".join(stances)))
    rest = sorted((n for n in shown if n["kind"] not in ("value", "stance")), key=lambda n: (-n["strength"], shown.index(n)))[:PROSE_TRAITS]
    for kind, group in _by_kind(rest):
        parts.append(sentence(f"{W['kinds'].get(kind, kind.capitalize())}: " + "; ".join(short_clause(n["gist"]) + (f" ({W['lie']})" if n["lie"] else "") for n in group)))
    return " ".join(parts)


def psyche_positions(nodes: list[dict], lang: str, include_secret: bool) -> str:
    """The stances only, one per line: 'toward X: …' (the gist's own 'Toward X:' opening dropped), with their markers."""
    W = _psyche_words(lang)
    out = []
    for n in nodes:
        if n["kind"] != "stance" or (n["disclosure"] == "secret" and not include_secret):
            continue
        marks = [m for m in _psyche_marks(n, W, include_secret) if not m.startswith(W["about"] + ":")]
        head = f"{W['toward']} {n['subject']}: {strip_toward(n['gist'])}" if n["subject"] else n["gist"]
        out.append(head + (f"  ·  {' · '.join(marks)}" if marks else ""))
    return "\n".join(out)


def _psyche_language_choices(c: COF, pid: str, include_personal: bool = False) -> list[str]:
    """`(preset) — fr`, then the languages the preset's gists carry with their counts."""
    nodes = c.psyche(pid, "", include_personal)
    pl = c._wanted(pid)
    counts: dict[str, int] = {}
    for n in nodes:
        for k in n["node"].get("gist") or {}:
            counts[k] = counts.get(k, 0) + 1
    out = [f"(preset){SEP}{pl[0] if pl else 'first language of each node'}"]
    return out + [f"{k}{SEP}{_plural(v, 'node')}" for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


class COFPsyche:
    """Psyche (COF 1.2, RFC 0004): the preset's psyche nodes — values, beliefs, wants, needs, fears, wound, stances, tastes,
    habits, contradictions, rules — as text for a language model or a prompt, in cof-cli's "Who she is" order."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"cof": ("COF",),
                             "format": (["prose", "lines", "soul"], {"default": "lines", "tooltip": "prose = one compact paragraph for an image or video generator (values and stances first, then the strongest traits); "
                                                                                                "lines = one line per node, kind · gist, for prompt building; soul = the \"Who she is\" section of cof-cli's SOUL.md (same order and rules)"})},
                "optional": {"language": (LANGUAGES, {"default": "(preset)", "tooltip": "the language of the gists: the preset's by default; each node falls back to the languages it has"}),
                             "focus": ("STRING", {"default": "", "multiline": False, "tooltip": "what she thinks of X: keep the nodes whose subject, tags or because / about targets (a memory, a character) match these words, plus every core node; empty = every node"}),
                             "max_nodes": ("INT", {"default": 0, "min": 0, "max": 500, "tooltip": "the strongest N nodes (core first), 0 = all"}),
                             "include_secret": ("BOOLEAN", {"default": False, "tooltip": "also the nodes marked secret (what the character never tells) — off: left out, with one line saying so in the soul format"}),
                             "drop_dangerous": ("BOOLEAN", {"default": True, "tooltip": "leave out the nodes whose declared risk is dangerous (would make the character harmful if acted on). A ComfyUI graph generates, it does not act: fiction needs villains, so turn this off for them — but the default stays on the safe side. inappropriate nodes are kept, with a marker"}),
                             "include_personal": ("BOOLEAN", {"default": False, "tooltip": "also the nodes marked personal (a user's data, never shared by default)"})}}

    @classmethod
    def VALIDATE_INPUTS(cls, language="(preset)"):
        return True                      # the language list is filled per file by web/cof.js (free text accepted)

    RETURN_TYPES = ("STRING", "STRING", "INT")
    RETURN_NAMES = ("text", "positions", "count")
    OUTPUT_TOOLTIPS = ("the psyche in the chosen format (empty for a file without psyche)",
                       "the stances only, one per line: toward X: …", "how many nodes the text holds")
    FUNCTION = "render"
    CATEGORY = "COF"
    DESCRIPTION = "The character's psyche (COF 1.2): values, beliefs, wants, fears, stances… as text for a prompt, in cof-cli's SOUL.md order; dangerous nodes dropped by default."

    def render(self, cof, format="lines", language="(preset)", focus="", max_nodes=0, include_secret=False, drop_dangerous=True, include_personal=False):
        c, pid = cof["cof"], cof["preset"]
        if not hasattr(c, "psyche"):             # the loader came from an older copy of ComfyUI-COF left in custom_nodes
            c = COF(c.path)
            print("[ComfyUI-COF] COF Psyche: the file was loaded by an older ComfyUI-COF — keep a single ComfyUI-COF folder in custom_nodes and restart")
        lang = _token(language)
        lang = "" if lang in ("", "(preset)") else lang
        nodes = c.psyche(pid, lang, include_personal)
        if not nodes:
            return ("", "", 0)
        kept, stats = c.psyche_select(nodes, focus, drop_dangerous, max_nodes)
        out_lang = lang or (c._wanted(pid) or ["en"])[0]
        if stats["dangerous"]:
            print(f"[COF] psyche: {_plural(stats['dangerous'], 'dangerous node')} left out (drop_dangerous)")
        if not drop_dangerous and any(n["risk"] == "dangerous" for n in kept):
            print("[COF] psyche: dangerous nodes kept — this character would be harmful if it acted on them (RFC 0004 § 3 septies): generation only, never an agent")
        shown = [n for n in kept if include_secret or n["disclosure"] != "secret"]
        fmt = _token(format)
        if fmt == "soul":
            text = psyche_soul(kept, out_lang, c.identity_view(pid).get("gender"), include_secret)
        elif fmt == "prose":
            text = psyche_prose(kept, out_lang, include_secret)
        else:
            text = psyche_lines(kept, out_lang, include_secret)
        return (text, psyche_positions(kept, out_lang, include_secret), len(shown))


NODE_CLASS_MAPPINGS = {"COFLoadCharacter": COFLoadCharacter, "COFPersonality": COFPersonality, "COFAppearance": COFAppearance,
                       "COFApplyLoRA": COFApplyLoRA, "COFReferenceLatents": COFReferenceLatents, "COF3D": COF3D,
                       "COFVoice": COFVoice, "COFPostures": COFPostures, "COFMotion": COFMotion, "COFFiles": COFFiles,
                       "COFMemories": COFMemories, "COFPsyche": COFPsyche}
# one node per category of the COF builder, in the builder's order, with the builder's icons
NODE_DISPLAY_NAME_MAPPINGS = {"COFLoadCharacter": f"{ICON['load']} COF Load Character",
                              "COFPersonality": f"{ICON['personality']} COF Personality",
                              "COFAppearance": f"{ICON['appearance']} COF Appearance",
                              "COFApplyLoRA": f"{ICON['weights']} COF Identity Weights · Apply LoRA",
                              "COFReferenceLatents": f"{ICON['weights']} COF Identity Weights · Reference Latents (RefMods)",
                              "COF3D": f"{ICON['volume3d']} COF 3D",
                              "COFVoice": f"{ICON['voice']} COF Voice",
                              "COFPostures": f"{ICON['posture']} COF Postures",
                              "COFMotion": f"{ICON['motion']} COF Motion",
                              "COFFiles": f"{ICON['files']} COF Files",
                              "COFMemories": f"{ICON['memories']} COF Memories",
                              "COFPsyche": f"{ICON['psyche']} COF Psyche"}


def _routes():
    """GET /cof/info?file=<name>: presets and characters of one .cof, for the dropdowns of web/cof.js."""
    try:
        from aiohttp import web
        from server import PromptServer
    except Exception:
        return

    @PromptServer.instance.routes.get("/cof/info")
    async def cof_info(request):
        name = request.query.get("file", "")
        if name not in _files():                     # only files ComfyUI lists — no arbitrary paths
            return web.json_response({"error": "unknown file"}, status=404)
        try:
            c = _open(name)
        except Exception as ex:
            return web.json_response({"error": str(ex)}, status=400)
        return web.json_response({
            "presets": [{"value": f"{p}{SEP}{lab}", "character": f"{i}{SEP}{(c.identities().get(i) or {}).get('name') or i}" if c.identities() else ""}
                        for p, lab, i in c.preset_choices()],
            "characters": [f"{i}{SEP}{n}" for i, n in c.character_choices()],
            "default_character": next((f"{i}{SEP}{n}" for i, n in c.character_choices() if i == c.default_identity()), ""),
        })


    @PromptServer.instance.routes.get("/cof/choices")
    async def cof_choices(request):
        q = request.query
        name = q.get("file", "")
        if name not in _files():
            return web.json_response({"error": "unknown file"}, status=404)
        try:
            c = _open(name)
            pid = c.find_preset(_pick(q.get("preset", "")), _pick(q.get("character", "")))
            node = q.get("node", "")
            cat = q.get("cat", "appearance")
            if node == "kinds":
                if cat not in KINDS:
                    return web.json_response({"error": "unknown category"}, status=400)
                values = _kind_choices(c, pid, q.get("offered") == "true", q.get("allow") == "true", q.get("current", ""), cat)
            elif node == "images":
                if cat not in ("appearance", "posture"):
                    return web.json_response({"error": "unknown category"}, status=400)
                aids = _image_ids(c, pid, q.get("kind", "all"), q.get("offered") == "true", q.get("allow") == "true",
                                  q.get("subject", "any"), q.get("angle", "any"), cat)
                values = _image_choices([c.image_label(a) for a in aids])
            elif node == "memories":
                mems = c.memories(pid, q.get("personal") == "true")
                values = [f"all{SEP}{len(mems)} memor{'y' if len(mems) == 1 else 'ies'}"] + [f"memory_{i + 1:03d}{SEP}{english(m['label'])} — {m['when']}" for i, m in enumerate(mems)]
            elif node == "memory-media":
                mems = c.memories(pid, q.get("personal") == "true")
                pk = _token(q.get("pick", "all"))
                chosen = _select(mems, "all" if pk == "all" else pk, "memory") if mems else []
                values = _memory_image_choices(_memory_media(c, {"allow_intimate": c.intimate_allowed(pid, q.get("allow") == "true")}, chosen))
            elif node == "psyche":
                values = _psyche_language_choices(c, pid, q.get("personal") == "true")
            elif node == "voice":
                values = [f"sample_{i + 1:03d}{SEP}{_voice_label(v)}" for i, (_, v) in enumerate(_voice_samples(c, pid))] or [f"none{SEP}this preset has no voice sample"]
            elif node == "media":
                if cat not in ("volume3d", "motion", "files"):
                    return web.json_response({"error": "unknown category"}, status=400)
                k = _token(q.get("kind", "all"))
                values = _media_choices(c.media(pid, cat, None if cat != "files" or k in ("all", "") else {k}))
            else:
                return web.json_response({"error": "unknown node"}, status=400)
        except Exception as ex:
            return web.json_response({"error": str(ex)}, status=400)
        return web.json_response({"values": values})


_routes()
