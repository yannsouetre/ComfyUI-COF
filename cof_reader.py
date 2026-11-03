"""Minimal, dependency-free reader for COF (Character Open File) 1.x containers (1.0, 1.1 memories, 1.2 psyche).

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

import json
import re
import zipfile
from pathlib import Path

MIMETYPE = "application/vnd.cof.character+zip"
ADULT = 18
AGE_CATS = {"appearance", "volume3d", "identity_weights"}
AGELESS = {"clothing", "accessory", "feature", "description", "environment"}
REF_KINDS = ("face", "reference-set", "character-sheet", "body")
TEXT_EXT = (".md", ".txt", ".json", ".yaml", ".yml", ".csv")       # files of the Files category that may be read or extracted
# psyche (COF 1.2, RFC 0004): the eleven kinds in cof-cli's SOUL.md order (§ 3 quinquies); an unknown kind is kept after them
PSYCHE_ORDER = ("wound", "value", "belief", "want", "need", "fear", "stance", "taste", "habit", "contradiction", "rule")
FULL_LINE_STRENGTH = 70                                            # at or above: the whole gist; below: a short clause
PSYCHE_FLAGS = {"psyche-dangerous": "a psyche node that would make the character harmful if it acted on it",
                "psyche-inappropriate": "a psyche node unsuitable for some audiences or uses"}
# words of a `focus` that select nothing by themselves ("what she thinks of the Martian" → martian)
FOCUS_STOP = {"the", "and", "for", "with", "about", "toward", "towards", "what", "she", "her", "his", "him", "they", "them",
              "think", "thinks", "feel", "feels", "les", "des", "une", "aux", "est", "elle", "ils", "elles", "son", "ses", "sur",
              "envers", "pense", "que", "qui", "quoi", "avec", "pour", "dans"}


class COFError(ValueError):
    pass


def age_years(a) -> float | None:
    if not isinstance(a, dict) or not isinstance(a.get("value"), (int, float)) or isinstance(a.get("value"), bool):
        return None
    u = a.get("unit")
    if u not in (None, "years", "months"):
        return 0.0                         # unknown unit: never counts as adult
    return a["value"] / 12 if u == "months" else float(a["value"])


_FR = re.compile(r"[éèêàçùûôîœ]|\b(le|la|les|de|du|des|et|un|une|au|aux|ans|avec|sur)\b", re.I)
EXTRACTABLE = (".safetensors", ".wav", ".mp3", ".ogg", ".opus", ".flac", ".m4a", ".png", ".jpg", ".jpeg", ".webp",
               ".bvh", ".vrma", ".vrm", ".glb", ".gltf", ".obj", ".fbx", ".stl", ".3mf", ".ply", ".duf", ".mp4", ".webm")


_EN = re.compile(r"\b(the|a|an|of|and|my|our|in|at|on|with|for|first|years|house|morning|night|day|wedding|family)\b", re.I)


def english(label: str) -> str:
    """Bilingual labels are written 'Français / English' or 'English / Français': keep the English half
    (French marks count against a half, English marks for it; a tie keeps the second half, the usual place of the English)."""
    if not label or " / " not in label:
        return label or ""
    parts = [x.strip() for x in label.split(" / ") if x.strip()]
    if not parts:
        return label
    return min(reversed(parts), key=lambda x: len(_FR.findall(x)) - len(_EN.findall(x)))


def label_in(label: str, lang: str) -> str:
    """A bilingual 'Français / English' label in the wanted language: the English half for English, the other half
    otherwise (the French of 'La rencontre avec le Martien / Meeting the Martian' for a French rendering)."""
    if not label or " / " not in label:
        return label or ""
    en = english(label)
    if (lang or "en").lower().startswith("en"):
        return en
    other = [x.strip() for x in label.split(" / ") if x.strip() and x.strip() != en]
    return other[0] if other else en


def text_in(obj, lang: str) -> str:
    """A language-keyed text (gist, label) in the wanted language, else its base language (fr-CA → fr), else its first entry."""
    if isinstance(obj, str):
        return obj
    if not isinstance(obj, dict) or not obj:
        return ""
    low = {k.lower(): v for k, v in obj.items()}
    want = (lang or "").lower()
    if want in low:
        return low[want]
    base = want.split("-")[0]
    for k, v in low.items():
        if base and k.split("-")[0] == base:
            return v
    return next(iter(obj.values()))


def fold(s: str) -> str:
    """Lower-case, accents stripped: 'Martien' and 'martian' are compared on their letters."""
    import unicodedata
    return "".join(ch for ch in unicodedata.normalize("NFKD", str(s or "")) if not unicodedata.combining(ch)).lower()


def short_clause(text: str, limit: int = 100) -> str:
    """cof-cli's short form of a psyche node (RFC 0004 § 3 quinquies): the first sentence, the elaboration after a dash
    dropped, cut at a word boundary past `limit` characters — deterministic."""
    t = " ".join(str(text).split())
    for sep in (" — ", " – "):
        i = t.find(sep)
        if i > 0:
            t = t[:i]
    m = re.search(r"[.!?](\s|$)", t)
    if m and m.start() > 0:
        t = t[:m.start()]
    t = t.rstrip(" ,;:")
    if len(t) > limit:
        cut = t.rfind(" ", 0, limit)
        t = (t[:cut] if cut > 0 else t[:limit]).rstrip(" ,;:") + "…"
    return t


def visual_summary(summary: str, name: str = "") -> str:
    """The summary as a prompt fragment: English half, without the character's name and without age ranges
    (the preset's own age is added separately)."""
    t = english(summary or "").strip().rstrip(".")
    keep = []
    for c in re.split(r",\s*|;\s*", t):
        c = c.strip()
        if not c or (name and c.lower() == name.lower()):
            continue
        if re.search(r"\d", c) and re.search(r"\b(age|aged|ages|years?|ans|depicted|déclinée)\b", c, re.I):
            continue
        keep.append(c)
    return ", ".join(keep)


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
        try:
            self.m = json.loads(self.zip.read("manifest.json").decode("utf-8"))
        except ValueError as ex:
            raise COFError(f"{self.path.name}: manifest.json is not valid JSON ({ex})") from ex
        if not isinstance(self.m, dict):
            raise COFError(f"{self.path.name}: manifest.json is not an object")
        ver = str(self.m.get("cof", ""))
        if not re.fullmatch(r"1\.\d+", ver):               # every 1.x is read: 1.1 (memories) and 1.2 (psyche) are additive
            raise COFError(f"{self.path.name}: format {ver or '?'} — run `cof migrate` (COF 1.x expected)")
        self.assets = {a["id"]: a for a in self.m.get("assets", []) if "id" in a}

    # ---------- raw access ----------
    def read(self, rel: str) -> bytes:
        try:
            return self.zip.read(rel)
        except KeyError as ex:
            raise COFError(f"{self.path.name}: {rel} is listed in the manifest but missing from the archive") from ex

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
    def identity_view(self, pid: str) -> dict:
        """The identity as this preset presents it: identity_override of the chain merged field by field (COF 1.1, RFC 0003 § 4)."""
        I = dict(self.identity(self.owner(pid)))
        seen = {}
        for q in self.chain(pid):
            for k, v in (self.presets[q].get("identity_override") or {}).items():
                if k in ("name", "given_name", "family_name", "nicknames", "gender", "gender_label", "summary") and k not in seen and v is not None:
                    seen[k] = v
        I.update(seen)
        return I

    def preset_prompts(self, pid: str) -> dict:
        """`prompts.scene` / `prompts.greeting` of this preset only — not inherited: a scene fits one presentation (RFC 0003 § 4 bis); language maps."""
        return {k: v for k, v in (self.presets[pid].get("prompts") or {}).items() if k in ("scene", "greeting") and v}

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

    def safety_age(self, pid: str) -> float | None:
        """The preset's lowest age for the adult rules: `preset_age` plus the ages carried by its memories (RFC 0002 § 5.2 —
        a preset that lists a memory showing the character as a minor is a minor preset). `preset_age` alone is the age
        shown (the prompt, the summary): a 65-year-old preset remembering her wedding at 31 is still shown at 65."""
        ages = [self.preset_age(pid)]
        for lists in (self.slots(pid), self.offered(pid)):
            ages += [age_years(v.get("age")) for v in self.resolved(lists).get("memories", [])]
        ages = [a for a in ages if a is not None]
        return min(ages) if ages else None

    def intimate_allowed(self, pid: str, requested: bool) -> bool:
        age = self.safety_age(pid)
        I = self.identity(self.owner(pid))
        if I.get("fictional") is False:                    # a real person: only with their explicit consent (scope intimate)
            cons = I.get("consent") or {}
            if "intimate" not in (cons.get("scope") or []):
                return False
        return bool(requested and self.permissions(pid).get("allowSexualUsage") and age is not None and age >= ADULT)

    def nudity_allowed(self, pid: str, requested: bool) -> bool:
        """Nudity without genitals (variants marked `nudity`): only on request, in a preset of 18 or over, and for a
        real person only with consent scope nudity or intimate."""
        age = self.safety_age(pid)
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
                    out |= set(v.get("images") or []) | set(v.get("derived") or []) | set(v.get("media") or [])
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
    def images(self, pid: str, kinds=None, include_offered=False, allow_intimate=False, subject="", angle="", cats=None) -> list[str]:
        lists = [self.slots(pid)] + ([self.offered(pid)] if include_offered else [])
        ok_intimate = self.intimate_allowed(pid, allow_intimate)
        ok_nudity = self.nudity_allowed(pid, allow_intimate)
        out = []
        for lst in lists:
            for cat, vs in self.resolved(lst).items():
                if cats and cat not in cats:
                    continue
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
        I = self.identity_view(pid)
        r = self.resolved(self.slots(pid))
        parts = [I.get("name") or self.m.get("name") or ""]
        age = self.preset_age(pid)
        # the character's summary describes it at its default age: left out of a preset of another age
        # (Clara "a designer from Lyon" is not a designer at 12)
        try:
            same_age = age == self.preset_age(self.default_preset(self.owner(pid)))
        except COFError:
            same_age = True
        vs = visual_summary(I.get("summary") or self.m.get("summary") or "", parts[0]) if same_age else ""
        if vs:
            parts.append(vs)
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
        g = I.get("gender")
        if g in ("female", "male", "non-binary") and I.get("morphology", {}).get("class") in ("human", "humanoid", None):
            parts.insert(1 if trig else 0, {"female": "woman", "male": "man", "non-binary": "person"}[g] if (age is None or age >= 18) else {"female": "girl", "male": "boy", "non-binary": "child"}[g])
        text = ", ".join(x for x in parts if x)
        scene = self.preset_prompts(pid).get("scene")                       # RFC 0003 § 4 bis: the preset's default scene comes first
        if scene:
            lang = next((v.get("language") for v in r.get("personality", []) if v.get("language")), None)
            sc = (scene.get(lang) if lang else None) or next(iter(scene.values()), "")
            if sc.strip():
                text = sc.strip().rstrip(".") + ". " + text
        return text

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
        if not rel.lower().endswith(EXTRACTABLE):
            raise COFError(f"refusing to extract {rel}: only weights, audio, images, motion clips and 3D files are extracted")
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = dest_dir / Path(rel).name
        data = self.read(rel)
        if rel.lower().endswith(".safetensors"):
            if len(data) < 8 or int.from_bytes(data[:8], "little") > len(data) - 8:
                raise COFError(f"{rel}: invalid safetensors header")
        out.write_bytes(data)
        return out

    # ---------- choices, labels, text, motion and 3D ----------
    def preset_choices(self) -> list[tuple[str, str, str]]:
        """(preset id, label, character id) for every preset, in file order."""
        return [(p, x.get("label") or p, self.owner(p) or "") for p, x in self.presets.items()]

    def character_choices(self) -> list[tuple[str, str]]:
        return [(i, x.get("name") or i) for i, x in self.identities().items()]

    def image_label(self, aid: str) -> str:
        if not hasattr(self, "_labels"):
            self._labels = {}
            for cat, e in (self.m.get("elements") or {}).items():
                for vid, v in ((e or {}).get("variants") or {}).items():
                    for a in (v.get("images") or []):
                        self._labels.setdefault(a, f"{english(v.get('label') or vid)} ({v.get('kind', cat)})")
        a = self.assets.get(aid) or {}
        extra = " ".join(x for x in (a.get("subject"), a.get("angle")) if x) or Path(a.get("path", aid)).stem
        w, h = a.get("width"), a.get("height")
        size = f" · {w}×{h} {'landscape' if w > h else 'portrait' if h > w else 'square'}" if isinstance(w, int) and isinstance(h, int) and w and h else ""
        return f"{self._labels.get(aid, aid)} · {extra}{size}"

    def persona(self, pid: str) -> tuple[str, str]:
        """The preset's personality as plain text for a language model, and its language(s)."""
        out, langs = [], []
        for v in self.resolved(self.slots(pid)).get("personality", []):
            if v.get("language"):
                langs.append(v["language"])
            if v.get("card"):
                d = (self.json_at(v["card"]) or {}).get("data") or {}
                for k, lead in (("description", ""), ("personality", "Personality: "), ("scenario", "Scenario: "), ("system_prompt", "")):
                    if isinstance(d.get(k), str) and d[k].strip():
                        out.append(lead + d[k].strip())
            elif isinstance(v.get("text"), str) and v["text"].strip():
                out.append(v["text"].strip())
            if v.get("psyche"):                                   # RFC 0003 (1.1): archetype and narrative role, one line each
                ps = self.json_at(v["psyche"]) or {}
                a = ps.get("archetype") or {}
                if isinstance(a, dict) and a.get("value"):
                    name = (a.get("label") or {}).get(v.get("language") or "en") or next(iter((a.get("label") or {}).values()), None) or str(a["value"]).replace("-", " ").title()
                    line = f"Archetype: {name}"
                    if a.get("secondary"):
                        line += " (secondary: " + ", ".join(str(x).title() for x in a["secondary"]) + ")"
                    out.append(line)
                if ps.get("role"):
                    out.append(f"Narrative role: {str(ps['role']).replace('-', ' ')}")
        out += self.psyche_clause(pid)                                       # COF 1.2 (RFC 0004): the strongest value and stance, one clause each
        greet = self.preset_prompts(pid).get("greeting")                     # RFC 0003 § 4 bis
        if greet:
            lang = langs[0] if langs else None
            gt = (greet.get(lang) if lang else None) or next(iter(greet.values()), "")
            if gt.strip():
                out.append("Opening line: " + gt.strip())
        return "\n\n".join(out), ", ".join(dict.fromkeys(langs))

    def media(self, pid: str, cat: str, kinds=None) -> list[dict]:
        """Files of the preset's variants in one category — motion clips ('motion'), 3D files ('volume3d') or agent
        files ('files': skills, memories, documents): [{label, kind, variant, asset or path, rel}]."""
        out = []
        for v in self.resolved(self.slots(pid)).get(cat, []):
            k = v.get("kind", "")
            if kinds and k not in kinds:
                continue
            lab = english(v.get("label") or v["_id"])
            for aid in (v.get("clips") or []) + (v.get("files") or []):
                a = self.assets.get(aid) or {}
                rel = a.get("path") or ""
                ok = rel.lower().endswith(TEXT_EXT if cat == "files" else EXTRACTABLE)
                if rel and (ok or cat == "files"):
                    shown = rel.split(f"/{v['_id']}/", 1)[-1] if cat == "files" else Path(rel).stem
                    out.append({"label": f"{lab} · {shown}" + ("" if ok else " (not extracted: not a text file)"),
                                "kind": k, "variant": v["_id"], "asset": aid if ok else None, "rel": rel})
            rel = v.get("path") or (v.get("mesh") or {}).get("path")
            if cat != "files" and rel and rel.lower().endswith(EXTRACTABLE) and rel in self.zip.namelist():
                out.append({"label": f"{lab} · {Path(rel).stem}", "kind": k, "variant": v["_id"], "path": rel, "rel": rel})
        return out

    def read_text(self, aid: str) -> str:
        a = self.assets.get(aid) or {}
        rel = a.get("path") or ""
        if not rel.lower().endswith(TEXT_EXT):
            raise COFError(f"refusing to read {rel}: only text files (.md, .txt, .json, .yaml, .csv) are read")
        return self.read(rel).decode("utf-8", errors="replace")

    def extract_text_file(self, aid: str, variant: str, dest_root: Path) -> Path:
        """Writes a text file of the Files category under dest_root/<variant>/<its path inside the variant>,
        so a skill keeps its layout (SKILL.md, reference/…); never a script."""
        a = self.assets.get(aid) or {}
        rel = a.get("path") or ""
        if not rel.lower().endswith(TEXT_EXT):
            raise COFError(f"refusing to extract {rel}: only text files are extracted from the Files category")
        sub = [x for x in rel.split(f"/{variant}/", 1)[-1].split("/") if x not in ("", ".", "..")]
        out = dest_root.joinpath(re.sub(r"[^A-Za-z0-9_.-]", "", variant) or "files", *sub)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(self.read(rel))
        return out

    def extract_path(self, rel: str, dest_dir: Path) -> Path:
        if not rel.lower().endswith(EXTRACTABLE):
            raise COFError(f"refusing to extract {rel}")
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = dest_dir / Path(rel).name
        out.write_bytes(self.read(rel))
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
        flags = self.content_flags()
        if flags:
            lines.append("content: " + ", ".join(f"{f} ({PSYCHE_FLAGS[f]})" if f in PSYCHE_FLAGS else f for f in flags))
        return "\n".join(lines)

    def content_flags(self) -> list[str]:
        """`tags.content` of the file (nudity, intimate, violence…; `psyche-dangerous` / `psyche-inappropriate` since COF 1.2,
        RFC 0004 § 3 septies), 'none' left out — what a host decides from without opening the elements."""
        tags = (self.m.get("tags") or {}).get("content") or []
        return [str(t) for t in tags if t and t != "none"] if isinstance(tags, list) else []


    # ---- memories (COF 1.1, RFC 0002) ----------------------------------------------------------------------

    def memory_media(self, vid: str) -> list[str]:
        """Asset ids of a memory node's media, following `media_from` (shared media)."""
        seen, cur = set(), vid
        while cur and cur not in seen:
            seen.add(cur)
            v = self.variant("memories", cur) or {}
            if v.get("media"):
                return list(v["media"])
            cur = str(v.get("media_from") or "").partition("/")[2]
        return []

    def memory_explicit(self, vid: str) -> bool:
        """Explicit media (RFC 0002 § 5): the node's `explicit`, the variant's 1.0 `nudity` flag, or those of the node the
        media are borrowed from (`media_from`) — a borrower never shows more than its lender allows."""
        seen, cur = set(), vid
        while cur and cur not in seen:
            seen.add(cur)
            v = self.variant("memories", cur) or {}
            n = self.json_at(v.get("memory")) if v.get("memory") else None
            if v.get("nudity") is True or (isinstance(n, dict) and n.get("explicit")):
                return True
            if v.get("media"):
                return False
            cur = str(v.get("media_from") or "").partition("/")[2]
        return False

    # ---- people, places and objects (COF 1.2, RFC 0005; a memory's who / where are RFC 0002) ---------------------

    def _element_label(self, ref: str, lang: str) -> str:
        """The label of the element a place's `environment` or an object's `item` names, in the wanted half of a bilingual label."""
        cat, _, vid = str(ref or "").partition("/")
        return label_in((self.variant(cat, vid) or {}).get("label") or vid, lang) if vid else ""

    def axes(self, n: dict, lang: str) -> dict:
        """The people, places and objects a memory or a psyche node names, as short texts in the wanted language:
        {people, places, objects}. An entry shows its label, else its character's name / its element's label (an entry
        `{"identity": "self"}` is the character itself, not listed). A memory's `where` is one place, a node's a list; a
        1.1 memory link to an `accessory` variant is read as an object (RFC 0005 § 2.2)."""
        out = {"people": [], "places": [], "objects": []}
        add = lambda key, t: t and t not in out[key] and out[key].append(t)
        for w in n.get("who") or []:
            if isinstance(w, dict) and w.get("identity") != "self":
                add("people", text_in(w.get("label"), lang) or (self.identities().get(w.get("identity")) or {}).get("name") or str(w.get("identity") or ""))
        where = n.get("where")
        for p in (where if isinstance(where, list) else [where]):
            if isinstance(p, dict):
                add("places", text_in(p.get("label"), lang) or self._element_label(p.get("environment"), lang))
        objs = [o for o in n.get("what") or [] if isinstance(o, dict)]
        for ln in n.get("links") or []:
            cat, _, vid = str((ln or {}).get("to", "") if isinstance(ln, dict) else "").partition("/")
            if cat and cat not in ("memories", "psyche") and (self.variant(cat, vid) or {}).get("kind") == "accessory" and not any(o.get("item") == f"{cat}/{vid}" for o in objs):
                objs.append({"item": f"{cat}/{vid}"})
        for o in objs:
            add("objects", text_in(o.get("label"), lang) or self._element_label(o.get("item"), lang))
        return out

    def _axes_words(self, n: dict) -> list[str]:
        """What the people, places and objects of a node can be found by: every language of their labels, a character's
        names, an element's label (both halves) and id."""
        words = []
        where = n.get("where")
        entries = [w for w in n.get("who") or [] if isinstance(w, dict)] + [p for p in (where if isinstance(where, list) else [where]) if isinstance(p, dict)] \
            + [o for o in n.get("what") or [] if isinstance(o, dict)]
        for e in entries:
            if isinstance(e.get("label"), dict):
                words += [str(x) for x in e["label"].values()]
            if e.get("identity") and e["identity"] != "self":
                words += self._identity_words(str(e["identity"]))
            for key in ("environment", "item"):
                if e.get(key):
                    cat, _, vid = str(e[key]).partition("/")
                    words += [(self.variant(cat, vid) or {}).get("label") or "", vid]
        return [x for x in words if isinstance(x, str) and x]

    def memories(self, pid: str, include_personal: bool = False) -> list[dict]:
        """The preset's memories in timeline order (by age, else by date and the birth year the file implies, else
        after the node they link to, undated last): [{id, kind, label, node, lang, gist, when, media, explicit, people,
        places, objects}] — the last three as short texts in the gist's language (RFC 0002 who / where, RFC 0005 what)."""
        vids = [v for v in (self.slots(pid).get("memories") or []) if self.variant("memories", v)]
        nodes = {}
        for v in vids:
            rel = (self.variant("memories", v) or {}).get("memory")
            n = self.json_at(rel) if rel else None
            if isinstance(n, dict):
                nodes[v] = n
        vids = [v for v in vids if v in nodes]

        def year(d):
            m = re.match(r"^(-?\d{1,4})(?:-(\d{2})(?:-(\d{2}))?)?$", str(d or ""))
            return None if not m else int(m.group(1)) + (int(m.group(2) or 7) - 1) / 12 + (int(m.group(3) or 15) - 1) / 365

        def point(v):
            w = nodes[v].get("when") or {}
            p = (w.get("from") or w.get("to") or {}) if self.variant("memories", v).get("kind") == "period" else w
            return age_years(p.get("age")), year(p.get("date"))

        by = sorted(y - a for a, y in map(point, vids) if a is not None and y is not None)
        birth = by[len(by) // 2] if by else None
        key = {}
        for v in vids:
            a, y = point(v)
            if a is None and y is not None:
                a = y - birth if birth is not None else 10_000 + y
            if a is not None:
                key[v] = a
        for _ in vids:
            changed = False
            for v in vids:
                if v in key:
                    continue
                for ln in nodes[v].get("links") or []:
                    cat, _, tgt = str(ln.get("to", "")).partition("/")
                    if cat == "memories" and tgt in key:
                        key[v] = key[tgt] + 0.001; changed = True; break
            if not changed:
                break
        order = sorted((v for v in vids if v in key), key=lambda v: (key[v], vids.index(v))) + [v for v in vids if v not in key]
        want = self._wanted(pid)
        out = []
        for v in order:
            n = nodes[v]
            if n.get("visibility") == "personal" and not include_personal:
                continue
            g = n.get("gist") or {}
            lang = self._gist_lang(g, want)
            rv = self.variant("memories", v) or {}
            w = n.get("when") or {}
            lab = (w.get("label") or {}).get(lang) or next(iter((w.get("label") or {}).values()), "")
            if rv.get("kind") == "period":
                fa, ta = age_years((w.get("from") or {}).get("age")), age_years((w.get("to") or {}).get("age"))
                fig = "" if fa is None and ta is None else (f"age {fa:g}" if ta is None else f"until age {ta:g}" if fa is None else f"age {fa:g}–{ta:g}")
            else:
                a = age_years(w.get("age"))
                fig = (("~" if w.get("approx") else "") + f"age {a:g}") if a is not None else ""
                if w.get("date"):
                    fig = f"{fig}, {w['date']}" if fig else w["date"]
            out.append({"id": v, "kind": rv.get("kind"), "label": rv.get("label") or v, "node": n, "lang": lang,
                        "gist": g.get(lang, ""), "when": f"{lab} ({fig})" if lab and fig else (lab or fig or "undated"),
                        "media": self.memory_media(v), "explicit": self.memory_explicit(v), **self.axes(n, lang)})
        return out

    # ---- languages of the gists (RFC 0002 § 3 reading rule, shared by memories and psyche) ---------------------------

    def _wanted(self, pid: str, language: str = "") -> list[str]:
        """The languages to read a gist in, in order: the one asked (if any), the preset's personality language, the
        character's primary language, the file's languages."""
        pers = (self.resolved(self.slots(pid)).get("personality") or [{}])[0]
        langs = [language, pers.get("language"), self.identity(self.owner(pid)).get("language"), *(self.m.get("languages") or [])]
        return [str(x).strip() for x in langs if isinstance(x, str) and x.strip()]

    @staticmethod
    def _gist_lang(g: dict, want: list[str]) -> str:
        """The key of the language map to read: the first wanted language present, else one of the same base (fr-CA → fr),
        else the first entry."""
        low = {k.lower(): k for k in g}
        return next((low[w.lower()] for w in want if w.lower() in low), None) or \
               next((low[k] for w in want for k in low if k.split("-")[0] == w.lower().split("-")[0]), None) or next(iter(g), "")

    # ---- psyche (COF 1.2, RFC 0004) -------------------------------------------------------------------------------

    USER_WORD = {"en": "the user", "fr": "l'utilisateur"}

    def _identity_words(self, iid: str) -> list[str]:
        I = self.identities().get(iid) or {}
        out = [iid, I.get("name") or "", I.get("given_name") or "", I.get("nickname") or ""] + list(I.get("nicknames") or [])
        return [x for x in out if isinstance(x, str) and x]

    def _target_words(self, ref: str) -> list[str]:
        """What a link or an `about` target can be found by: a memory's label, tags, people, places and objects; a psyche node's
        label and tags; a character's names."""
        cat, _, vid = str(ref or "").partition("/")
        if cat == "identities":
            return self._identity_words(vid)
        rv = self.variant(cat, vid) or {}
        words = [rv.get("label") or "", vid]
        if cat == "memories" and rv.get("memory"):
            n = self.json_at(rv["memory"]) or {}
            words += [str(t) for t in (n.get("tags") or [])]
            for w in n.get("who") or []:
                if isinstance(w, dict):
                    words += list((w.get("label") or {}).values()) if isinstance(w.get("label"), dict) else []
                    if w.get("identity") and w["identity"] != "self":
                        words += self._identity_words(w["identity"])
            where = (n.get("where") or {}).get("label")
            words += list(where.values()) if isinstance(where, dict) else []
            words += [w for w in self._axes_words({"what": n.get("what")})]          # its objects (RFC 0005)
        elif cat == "psyche" and rv.get("node"):
            n = self.json_at(rv["node"]) or {}
            words += [str(t) for t in (n.get("tags") or [])]
        return [x for x in words if isinstance(x, str) and x]

    def psyche_subject(self, about, lang: str) -> str:
        """What a stance bears on, as a short text in the wanted language: the user, a character's name, a node's
        label or the free label (RFC 0004 § 3 bis)."""
        if not isinstance(about, dict):
            return ""
        if about.get("user"):
            return self.USER_WORD.get((lang or "en").lower().split("-")[0], self.USER_WORD["en"])
        if about.get("identity"):
            return (self.identities().get(about["identity"]) or {}).get("name") or str(about["identity"])
        if about.get("node"):
            cat, _, vid = str(about["node"]).partition("/")
            return label_in((self.variant(cat, vid) or {}).get("label") or vid, lang)
        return text_in(about.get("label"), lang)

    def psyche(self, pid: str, language: str = "", include_personal: bool = False) -> list[dict]:
        """The preset's psyche nodes (`slots.psyche`, inherited through `extends`) in slot order, each with its gist in the
        wanted language (the one asked, else the preset's, each node falling back to what it has), its weight and axes,
        the subject of a stance, the people, places and objects it is tied to (`who` / `where` / `what`, RFC 0005), and the
        words it can be focused by: [{id, kind, label, node, lang, gist, strength, stability, disclosure, origin, risk, lie,
        subject, keywords, people, places, objects}]. Unknown kinds and fields are kept as they are.
        A file without psyche (1.0, 1.1) gives an empty list. Personal nodes (a user's data) are left out unless asked."""
        vids = [v for v in (self.slots(pid).get("psyche") or []) if self.variant("psyche", v)]
        want = self._wanted(pid, language)
        out = []
        for v in vids:
            rv = self.variant("psyche", v) or {}
            n = self.json_at(rv["node"]) if isinstance(rv.get("node"), str) else None
            if not isinstance(n, dict) or not isinstance(n.get("gist"), dict) or not n["gist"]:
                continue
            if n.get("visibility") == "personal" and not include_personal:
                continue
            lang = self._gist_lang(n["gist"], want)
            strength = next((x for x in (n.get("strength"), rv.get("strength")) if isinstance(x, (int, float)) and not isinstance(x, bool)), 50)
            kind = str(rv.get("kind") or "")
            words = [str(rv.get("label") or ""), v] + [str(t) for t in (n.get("tags") or []) if t]
            about = n.get("about") if isinstance(n.get("about"), dict) else None
            if about:
                if about.get("user"):
                    words += list(self.USER_WORD.values()) + ["user"]
                if about.get("identity"):
                    words += self._identity_words(str(about["identity"]))
                if about.get("node"):
                    words += self._target_words(str(about["node"]))
                if isinstance(about.get("label"), dict):
                    words += [str(x) for x in about["label"].values()]
            for ln in n.get("links") or []:
                if isinstance(ln, dict) and ln.get("type") in ("because", "about"):
                    words += self._target_words(str(ln.get("to", "")))
                    if isinstance(ln.get("label"), dict):
                        words += [str(x) for x in ln["label"].values()]
            words += self._axes_words(n)                                          # the people, places and objects it is tied to (RFC 0005)
            out.append({"id": v, "kind": kind, "label": rv.get("label") or v, "node": n, "lang": lang,
                        "gist": " ".join(str(n["gist"].get(lang, "")).split()), "strength": int(strength),
                        "stability": n.get("stability") or "settled", "disclosure": n.get("disclosure") or "open",
                        "origin": n.get("origin") or "authored", "risk": n.get("risk") or "none",
                        "lie": kind == "belief" and bool(n.get("false_but_held")),
                        "subject": self.psyche_subject(about, lang) if kind == "stance" else "",
                        "keywords": [w for w in words if w], **self.axes(n, lang)})
        return out

    @staticmethod
    def psyche_select(nodes: list[dict], focus: str = "", drop_dangerous: bool = True, max_nodes: int = 0) -> tuple[list[dict], dict]:
        """RFC 0004 § 3 quinquies, selection at use time. `focus`: keep the nodes whose label, tags, subject, people, places
        and objects (RFC 0005: a name, a character's names, an element's label) or `because` / `about` targets (a memory's
        label, tags, people, places, objects; a character's names) match the words, plus every `core` node — "what she
        thinks of X". `drop_dangerous`: the `dangerous` nodes leave — the persona clause always drops them; the COF Psyche
        node keeps them unless asked (decision D3, 3 November 2026: a graph generates, it does not act). `max_nodes`: the strongest N (core first, then by strength, then slot
        order), 0 = all. A contradiction whose two sides are not both kept is dropped (§ 3 sexies). Returns the kept
        nodes in slot order and what was dropped."""
        stats = {"dangerous": 0, "focus": 0, "limit": 0, "contradictions": 0}
        keep = []
        for n in nodes:
            if drop_dangerous and n["risk"] == "dangerous":
                stats["dangerous"] += 1; continue
            keep.append(n)
        if focus and focus.strip():
            whole = fold(focus).strip()
            words = [w for w in re.split(r"[\s,;/]+", whole) if len(w) >= 3 and w not in FOCUS_STOP]
            def hit(n):
                if n["stability"] == "core":
                    return True
                hay = [fold(k) for k in n["keywords"]]
                return any(whole in h or any(w in h for w in words) for h in hay)
            before = len(keep)
            keep = [n for n in keep if hit(n)]
            stats["focus"] = before - len(keep)
        if max_nodes and max_nodes > 0 and len(keep) > max_nodes:
            rank = sorted(keep, key=lambda n: (n["stability"] != "core", -n["strength"], nodes.index(n)))[:max_nodes]
            stats["limit"] = len(keep) - len(rank)
            keep = [n for n in keep if n in rank]
        while True:
            ids = {n["id"] for n in keep}
            gone = [n for n in keep if n["kind"] == "contradiction"
                    and sum(1 for ln in (n["node"].get("links") or []) if isinstance(ln, dict) and ln.get("type") == "conflicts"
                            and str(ln.get("to", "")).partition("/")[2] in ids) < 2]
            if not gone:
                break
            stats["contradictions"] += len(gone)
            keep = [n for n in keep if n not in gone]
        return keep, stats

    def psyche_clause(self, pid: str) -> list[str]:
        """What the persona text says of the psyche when the preset holds nodes: one short clause from the strongest value
        and one from the strongest stance — open nodes only (no secret, no dangerous, no personal), the gist's first sentence."""
        kept, _ = self.psyche_select([n for n in self.psyche(pid) if n["disclosure"] != "secret"])
        out = []
        for kind, lead in (("value", "Core value: "), ("stance", "Stance")):
            best = max((n for n in kept if n["kind"] == kind), key=lambda n: n["strength"], default=None)
            if not best:
                continue
            clause = short_clause(best["gist"])
            if kind == "stance":
                subj = best["subject"]
                out.append(f"{lead} toward {subj}: {strip_toward(clause)}" if subj else f"{lead}: {clause}")
            else:
                out.append(lead + clause)
        return out


def strip_toward(text: str) -> str:
    """A stance gist often opens with its subject ('Toward the Martian: protective curiosity…', 'Envers les agences : …'):
    when the subject is named beside it, that opening is dropped so it is not said twice."""
    m = re.match(r"^(toward|towards|envers|vers|about|à propos de)\s+[^:]{1,60}:\s*", text, re.I)
    return text[m.end():] if m and text[m.end():] else text
