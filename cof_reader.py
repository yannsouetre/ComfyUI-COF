"""Minimal, dependency-free reader for COF (Character Open File) 1.x containers.

Mirrors the reference rules of cof-cli (spec/SPEC.md, docs/IMPLEMENTING.md): presets with `extends`
(slots and alternatives replaced per category), `derives_from`, per-character ownership and rights,
per-preset age (applied + offered, ages in years), primary image, facet navigation.

Safety (SPEC § 4.5, § 9.2, § 9.4) is enforced here, not in the UI:
  - intimate variants are never returned unless the caller asks for them explicitly AND the preset's
    effective `allowSexualUsage` is true AND the preset's age is known and >= 18;
  - using the identity weights or the voice of a real person (`fictional: false`) without a consent record is
    warned (the author and the user are responsible, SPEC § 9.2); intimate content stays behind its hard rules.
No file is executed; only JSON, images, audio and .safetensors are read or extracted.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

MIMETYPE = "application/vnd.cof.character+zip"
ADULT = 18
AGE_CATS = {"appearance", "volume3d", "identity_weights"}
AGELESS = {"clothing", "accessory", "feature", "description", "environment"}
REF_KINDS = ("face", "reference-set", "character-sheet", "body")


class COFError(ValueError):
    pass


def age_years(a) -> float | None:
    if not isinstance(a, dict) or not isinstance(a.get("value"), (int, float)) or isinstance(a.get("value"), bool):
        return None
    u = a.get("unit")
    if u not in (None, "years", "months"):
        return 0.0                         # unknown unit: never counts as adult
    return a["value"] / 12 if u == "months" else float(a["value"])


def english(label: str) -> str:
    """Bilingual labels are written 'Français / English': keep the English half for prompts."""
    return label.split(" / ", 1)[1].strip() if label and " / " in label else (label or "")


class COF:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        try:
            self.zip = zipfile.ZipFile(self.path)
        except zipfile.BadZipFile as ex:
            raise COFError(f"{self.path.name}: not a ZIP container") from ex
        names = self.zip.namelist()
        if "manifest.json" not in names:
            raise COFError(f"{self.path.name}: manifest.json missing")
        if names and names[0] == "mimetype" and self.zip.read("mimetype").decode("ascii", "ignore").strip() != MIMETYPE:
            raise COFError(f"{self.path.name}: wrong mimetype")
        self.m = json.loads(self.zip.read("manifest.json").decode("utf-8"))
        ver = str(self.m.get("cof", ""))
        if not re.fullmatch(r"1\.\d+", ver):
            raise COFError(f"{self.path.name}: format {ver or '?'} — run `cof migrate` (COF 1.x expected)")
        self.assets = {a["id"]: a for a in self.m.get("assets", []) if "id" in a}

    # ---------- raw access ----------
    def read(self, rel: str) -> bytes:
        return self.zip.read(rel)

    def json_at(self, rel: str):
        try:
            return json.loads(self.read(rel).decode("utf-8"))
        except Exception:
            return None

    def asset_bytes(self, aid: str) -> bytes | None:
        a = self.assets.get(aid) or {}
        return self.read(a["path"]) if a.get("path") and a["path"] in self.zip.namelist() else None

    # ---------- characters ----------
    @property
    def presets(self) -> dict:
        return self.m.get("presets") or {}

    def identities(self) -> dict:
        return self.m.get("identities") or {}

    def default_identity(self):
        return self.m.get("default_identity") or next(iter(self.identities()), None)

    def identity(self, iid) -> dict:
        I = self.identities().get(iid)
        if I:
            if I.get("consent") is None and iid == self.default_identity() and len(self.identities()) == 1:
                return dict(I, consent=(self.m.get("rights") or {}).get("consent"))   # root copy = default character only
            return I
        return {k: self.m.get(k) for k in ("name", "nickname", "summary", "fictional", "morphology")} | {"consent": (self.m.get("rights") or {}).get("consent")}

    def variants(self, cat: str) -> dict:
        return ((self.m.get("elements") or {}).get(cat) or {}).get("variants") or {}

    def variant(self, cat: str, vid: str, _seen=None) -> dict | None:
        v = self.variants(cat).get(vid)
        if v is None:
            return None
        _seen = _seen or set()
        if vid in _seen:
            return dict(v, _id=vid, _cat=cat)
        _seen.add(vid)
        parent = self.variant(cat, v["derives_from"], _seen) if v.get("derives_from") else None
        out = dict(parent or {})
        out.update({k: x for k, x in v.items() if k != "derives_from"})
        out["_id"], out["_cat"] = vid, cat
        return out

    # ---------- presets ----------
    def chain(self, pid: str) -> list[str]:
        out, seen, q = [], set(), pid
        while q and q in self.presets and q not in seen:
            seen.add(q); out.append(q); q = self.presets[q].get("extends")
        return out

    def inherited(self, pid: str, key: str):
        for q in self.chain(pid):
            if self.presets[q].get(key) is not None:
                return self.presets[q][key]
        return None

    def _lists(self, pid: str, key: str) -> dict:
        out = {}
        for q in reversed(self.chain(pid)):
            for k, v in (self.presets[q].get(key) or {}).items():
                out[k] = list(v)
        return out

    def slots(self, pid: str) -> dict:
        return self._lists(pid, "slots")

    def offered(self, pid: str) -> dict:
        s, a = self.slots(pid), self._lists(pid, "alternatives")
        return {k: [x for x in v if x not in s.get(k, [])] for k, v in a.items() if [x for x in v if x not in s.get(k, [])]}

    def resolved(self, lists: dict) -> dict:
        return {cat: [v for v in (self.variant(cat, i) for i in ids) if v] for cat, ids in lists.items()}

    def owner(self, pid: str):
        return self.inherited(pid, "identity") or self.default_identity()

    def presets_of(self, iid) -> list[str]:
        me = iid or self.default_identity()
        return [p for p in self.presets if self.owner(p) == me]

    def default_preset(self, iid=None) -> str:
        me = iid or self.default_identity()
        d = (self.identities().get(me) or {}).get("default_preset")
        if d in self.presets:
            return d
        if self.m.get("default_preset") in self.presets and self.owner(self.m["default_preset"]) == me:
            return self.m["default_preset"]
        mine = self.presets_of(me)
        if not mine:
            raise COFError(f"{self.path.name}: no preset for character {me}")
        return mine[0]

    def find_preset(self, name: str, character: str = "") -> str:
        """Preset by id, by label (case-insensitive), or the character's default when name is empty."""
        iid = self.find_character(character)
        if not name:
            return self.default_preset(iid)
        if name in self.presets:
            return name
        low = name.strip().lower()
        for p, x in self.presets.items():
            if (x.get("label") or "").strip().lower() == low:
                return p
        raise COFError(f"unknown preset '{name}' — available: {', '.join(self.presets)}")

    def find_character(self, name: str):
        if not name:
            return self.default_identity()
        if name in self.identities():
            return name
        low = name.strip().lower()
        for i, x in self.identities().items():
            if low in ((x.get("name") or "").lower(), (x.get("nickname") or "").lower()):
                return i
        if not self.identities():
            return None
        raise COFError(f"unknown character '{name}' — available: {', '.join(self.identities())}")

    def preset_age(self, pid: str) -> float | None:
        ages = []
        for lists in (self.slots(pid), self.offered(pid)):
            for cat, vs in self.resolved(lists).items():
                if cat in AGE_CATS:
                    ages += [x for v in vs if v.get("kind") not in AGELESS for x in [age_years(v.get("age"))] if x is not None]
        ao = age_years(self.inherited(pid, "age_override"))
        if ao is not None:
            ages.append(ao)
        return min(ages) if ages else None

    def permissions(self, pid: str) -> dict:
        iid = self.owner(pid)
        rights = (self.identities().get(iid) or {}).get("rights") or self.m.get("rights") or {}
        return {**(rights.get("permissions") or {}), **(self.presets[pid].get("permissions_override") or {})}

    def intimate_allowed(self, pid: str, requested: bool) -> bool:
        age = self.preset_age(pid)
        I = self.identity(self.owner(pid))
        if I.get("fictional") is False:                    # a real person: only with their explicit consent (scope intimate)
            cons = I.get("consent") or {}
            if "intimate" not in (cons.get("scope") or []):
                return False
        return bool(requested and self.permissions(pid).get("allowSexualUsage") and age is not None and age >= ADULT)

    def nudity_allowed(self, pid: str, requested: bool) -> bool:
        """Nudity without genitals (variants marked `nudity`): only on request, in a preset of 18 or over, and for a
        real person only with consent scope nudity or intimate."""
        age = self.preset_age(pid)
        if not (requested and age is not None and age >= ADULT):
            return False
        I = self.identity(self.owner(pid))
        if I.get("fictional") is False:
            cons = I.get("consent") or {}
            return bool({"nudity", "intimate"} & set(cons.get("scope") or []))
        return True

    def is_real_without_consent(self, pid: str) -> bool:
        I = self.identity(self.owner(pid))
        return I.get("fictional") is False and not I.get("consent")

    def nude_assets(self) -> set:
        out = set()
        for cat, e in (self.m.get("elements") or {}).items():
            for vid in ((e or {}).get("variants") or {}):
                v = self.variant(cat, vid) or {}
                if v.get("nudity") is True or v.get("kind") == "intimate":
                    out |= set(v.get("images") or []) | set(v.get("derived") or [])
        return out

    def primary_image(self, pid: str, allow_adult: bool = False) -> str | None:
        nude = set() if allow_adult else self.nude_assets()
        e = self.inherited(pid, "primary_image")
        if e in self.assets and e not in nude:
            return e
        r = self.resolved(self.slots(pid))
        for k in REF_KINDS:
            for v in [x for x in r.get("appearance", []) if x.get("kind") == k]:
                for i in v.get("images") or []:
                    if i not in nude and str((self.assets.get(i) or {}).get("mediaType", "")).startswith("image/"):
                        return i
        return None

    # ---------- what nodes output ----------
    def images(self, pid: str, kinds=None, include_offered=False, allow_intimate=False, subject="", angle="") -> list[str]:
        lists = [self.slots(pid)] + ([self.offered(pid)] if include_offered else [])
        ok_intimate = self.intimate_allowed(pid, allow_intimate)
        ok_nudity = self.nudity_allowed(pid, allow_intimate)
        out = []
        for lst in lists:
            for cat, vs in self.resolved(lst).items():
                for v in vs:
                    k = v.get("kind")
                    if k == "intimate" and not ok_intimate:
                        continue
                    if v.get("nudity") is True and not ok_nudity:
                        continue
                    if kinds and k not in kinds and cat not in kinds:
                        continue
                    for aid in (v.get("images") or []):
                        a = self.assets.get(aid) or {}
                        if not str(a.get("mediaType", "")).startswith("image/"):
                            continue
                        if subject and a.get("subject") != subject:
                            continue
                        if angle and a.get("angle") != angle:
                            continue
                        if aid not in out:
                            out.append(aid)
        return out

    def prompt(self, pid: str) -> str:
        """A plain image-generation prompt for the preset: name, summary, look, style, LoRA trigger words.
        A preset `prompt` field (planned for 1.x) is used as is when present."""
        p = self.presets[pid]
        if isinstance(p.get("prompt"), str) and p["prompt"].strip():
            return p["prompt"].strip()
        I = self.identity(self.owner(pid))
        r = self.resolved(self.slots(pid))
        parts = [I.get("name") or self.m.get("name") or ""]
        if I.get("summary") or self.m.get("summary"):
            parts.append(english(I.get("summary") or self.m.get("summary")))
        age = self.preset_age(pid)
        if age is not None:
            parts.append(f"{int(age)} years old")
        app = r.get("appearance", [])
        wear = [english(v.get("label", "")) for v in app if v.get("kind") == "clothing" and v.get("label")]
        acc = [english(v.get("label", "")) for v in app if v.get("kind") == "accessory" and v.get("label")]
        env = [english(v.get("label", "")) for v in app if v.get("kind") == "environment" and v.get("label")]
        if wear:
            parts.append("wearing " + ", ".join(wear))
        if acc:
            parts.append("with " + ", ".join(acc))
        if env:
            parts.append("setting: " + env[0])
        if p.get("style"):
            parts.append(p["style"].replace("-", " ") + " style")
        trig = self.trigger_words(pid)
        if trig:
            parts.insert(0, ", ".join(trig))
        return ", ".join(x for x in parts if x)

    def weights(self, pid: str, kinds=("lora", "lycoris", "dora")) -> list[dict]:
        out = []
        for v in self.resolved(self.slots(pid)).get("identity_weights", []):
            if v.get("kind") not in kinds:
                continue
            spec = self.json_at(v["spec"]) if v.get("spec") else {}
            out.append({"variant": v, "spec": spec or {}, "asset": self.assets.get((spec or {}).get("file", ""))})
        return out

    def trigger_words(self, pid: str) -> list[str]:
        return [t for w in self.weights(pid, ("lora", "lycoris", "dora", "textual-inversion", "dreambooth")) for t in (w["spec"].get("trigger_words") or [])]

    def extract(self, aid: str, dest_dir: Path) -> Path:
        a = self.assets.get(aid) or {}
        if not a.get("path"):
            raise COFError(f"asset {aid} is a link ({a.get('uri', '?')}): download it yourself")
        rel = a["path"]
        if not rel.lower().endswith((".safetensors", ".wav", ".mp3", ".ogg", ".opus", ".flac", ".m4a", ".png", ".jpg", ".jpeg", ".webp")):
            raise COFError(f"refusing to extract {rel}: only weights, audio and images are extracted")
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = dest_dir / Path(rel).name
        data = self.read(rel)
        if rel.lower().endswith(".safetensors"):
            if len(data) < 8 or int.from_bytes(data[:8], "little") > len(data) - 8:
                raise COFError(f"{rel}: invalid safetensors header")
        out.write_bytes(data)
        return out

    def voices(self, pid: str) -> list[dict]:
        return self.resolved(self.slots(pid)).get("voice", [])

    def summary(self, pid: str) -> str:
        iid = self.owner(pid)
        I = self.identity(iid)
        age = self.preset_age(pid)
        perms = self.permissions(pid)
        lines = [f"{I.get('name')} — preset {pid} ({self.presets[pid].get('label', '')})"] + (["WARNING: real person without a consent record — you are responsible for its use"] if self.is_real_without_consent(pid) else []) + [
                 f"age: {'unknown' if age is None else int(age)} · fictional: {I.get('fictional') is not False}",
                 "rights: " + ", ".join(f"{k}={perms[k]}" for k in ("commercialUsage", "allowRedistribution", "allowVoiceCloning", "allowTraining", "allowSexualUsage") if k in perms),
                 "presets of this character: " + ", ".join(self.presets_of(iid))]
        if len(self.identities()) > 1:
            lines.append("characters: " + ", ".join(f"{i} ({x.get('name')})" for i, x in self.identities().items()))
        return "\n".join(lines)
