"""ComfyUI nodes for COF characters. One simple node (COF Load Character) covers most workflows;
the advanced nodes take its COF output to pick images, apply the embedded LoRA, export pre-encoded
references (RefMods) or play a voice sample."""
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

from .cof_reader import COF, COFError, REF_KINDS

KIND_CHOICES = ["references (face, reference set, sheet, body)", "all", "face", "body", "reference-set", "character-sheet",
                "hair", "clothing", "accessory", "environment", "posture"]


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
        files = folder_paths.get_filename_list("cof") if folder_paths else []
        return {"required": {
            "cof_file": (files or ["(put .cof files in models/cof or input/cof)"],),
            "preset": ("STRING", {"default": "", "tooltip": "preset id or label; empty = the character's default preset"}),
            "character": ("STRING", {"default": "", "tooltip": "for a file with several characters: id or name; empty = default character"}),
            "max_size": ("INT", {"default": 1024, "min": 64, "max": 4096, "step": 64}),
        }, "optional": {
            "allow_intimate": ("BOOLEAN", {"default": False, "tooltip": "adult content: variants marked nudity (preset 18+, consent for a real person) and intimate ones (also needs the preset's effective allowSexualUsage)"}),
        }}

    RETURN_TYPES = ("COF", "IMAGE", "IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("cof", "primary_image", "references", "prompt", "info")
    FUNCTION = "load"
    CATEGORY = "COF"
    DESCRIPTION = "Loads a character from a COF (Character Open File): primary image, reference views and a ready prompt for the chosen preset."

    def load(self, cof_file, preset, character, max_size, allow_intimate=False):
        cof = _open(cof_file)
        pid = cof.find_preset(preset.strip(), character.strip())
        prim = cof.primary_image(pid, allow_adult=cof.nudity_allowed(pid, allow_intimate))
        refs = cof.images(pid, kinds=set(REF_KINDS), allow_intimate=allow_intimate)
        sel = {"file": cof_file, "preset": pid, "allow_intimate": cof.intimate_allowed(pid, allow_intimate), "max_size": max_size}
        return ({"cof": cof, **sel}, _to_tensor(_images(cof, [prim] if prim else []), max_size),
                _to_tensor(_images(cof, refs), max_size), cof.prompt(pid), cof.summary(pid))


class COFImages:
    """Advanced: pick images of the preset by kind, subject and angle, applied or also offered."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "cof": ("COF",),
            "kind": (KIND_CHOICES,),
            "include_offered": ("BOOLEAN", {"default": False, "tooltip": "also the outfits, accessories… offered with this preset"}),
            "subject": (["any", "head", "body", "hands", "hair", "outfit", "detail"],),
            "angle": (["any", "front", "left34", "left", "leftback34", "back", "rightback34", "right", "right34", "top", "low34"],),
        }}

    RETURN_TYPES = ("IMAGE", "INT")
    RETURN_NAMES = ("images", "count")
    FUNCTION = "pick"
    CATEGORY = "COF"

    def pick(self, cof, kind, include_offered, subject, angle):
        c, pid = cof["cof"], cof["preset"]
        kinds = None if kind == "all" else set(REF_KINDS) if kind.startswith("references") else {kind}
        aids = c.images(pid, kinds=kinds, include_offered=include_offered, allow_intimate=cof["allow_intimate"],
                        subject="" if subject == "any" else subject, angle="" if angle == "any" else angle)
        return (_to_tensor(_images(c, aids), cof["max_size"]), len(aids))


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


class COFVoice:
    """Advanced: a voice sample of the preset as AUDIO (for voice-cloning or lip-sync nodes)."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"cof": ("COF",), "index": ("INT", {"default": 0, "min": 0, "max": 64})}}

    RETURN_TYPES = ("AUDIO", "STRING")
    RETURN_NAMES = ("audio", "transcript")
    FUNCTION = "voice"
    CATEGORY = "COF"

    def voice(self, cof, index):
        c, pid = cof["cof"], cof["preset"]
        if c.is_real_without_consent(pid):
            print("[COF] WARNING: real person without a consent record — you are responsible for this use (COF § 9.2)")
        samples = [aid for v in c.voices(pid) for aid in (v.get("samples") or [])]
        if not samples:
            raise COFError(f"preset {pid} has no voice sample")
        aid = samples[min(index, len(samples) - 1)]
        tmp = Path(folder_paths.get_temp_directory() if folder_paths else ".") / "cof" / _slug(c.path.stem)
        f = c.extract(aid, tmp)
        from comfy_extras.nodes_audio import load as load_audio
        wave, sr = load_audio(str(f))
        tr = ""
        t = (c.assets.get(aid) or {}).get("transcript")
        if t:
            try:
                tr = c.read(t).decode("utf-8")
            except Exception:
                tr = ""
        return ({"waveform": wave.unsqueeze(0), "sample_rate": sr}, tr)


NODE_CLASS_MAPPINGS = {"COFLoadCharacter": COFLoadCharacter, "COFImages": COFImages, "COFApplyLoRA": COFApplyLoRA,
                       "COFReferenceLatents": COFReferenceLatents, "COFVoice": COFVoice}
NODE_DISPLAY_NAME_MAPPINGS = {"COFLoadCharacter": "COF Load Character", "COFImages": "COF Images (advanced)",
                              "COFApplyLoRA": "COF Apply LoRA (advanced)", "COFReferenceLatents": "COF Reference Latents / RefMods (advanced)",
                              "COFVoice": "COF Voice (advanced)"}
