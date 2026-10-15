"""ComfyUI nodes for COF characters. COF Load Character covers most workflows; then one node per category of
the COF builder (Personality, Appearance, Identity weights, 3D, Voice, Postures, Motion, Files), each taking the
loader's `cof` output."""
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

from .cof_reader import COF, COFError, REF_KINDS, english

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
ICON = {"load": "👤", "personality": "📝", "appearance": "🖼️", "weights": "🧬", "volume3d": "🧊", "voice": "👄",
        "posture": "🏃", "motion": "🎥", "files": "📁"}


def _register_folder():
    if folder_paths is None:
        return
    if "cof" not in folder_paths.folder_names_and_paths:
        folder_paths.folder_names_and_paths["cof"] = ([os.path.join(folder_paths.models_dir, "cof")], {".cof"})
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
    return folder_paths.get_filename_list("cof") if folder_paths else []


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
    m = re.fullmatch(r"(?:image|item|sample)_(\d+)", t)
    if m:
        k = int(m.group(1))
        if not 1 <= k <= len(items):
            raise COFError(f"{t}: this preset has {len(items)} {what}{'s' if len(items) != 1 else ''} — choose again in the list")
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
            out.append(Image.open(io.BytesIO(data)))
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
                       "a ready text prompt for this preset", "rights, age and the presets of this character")
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


NODE_CLASS_MAPPINGS = {"COFLoadCharacter": COFLoadCharacter, "COFPersonality": COFPersonality, "COFAppearance": COFAppearance,
                       "COFApplyLoRA": COFApplyLoRA, "COFReferenceLatents": COFReferenceLatents, "COF3D": COF3D,
                       "COFVoice": COFVoice, "COFPostures": COFPostures, "COFMotion": COFMotion, "COFFiles": COFFiles}
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
                              "COFFiles": f"{ICON['files']} COF Files"}


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
