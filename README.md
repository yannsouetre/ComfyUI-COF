<p align="center"><img src="docs/img/cof-comfy.png" alt="Diagram: a .cof file goes into the COF Load Character node, which outputs the preset's primary image, its references, a prompt and the rights" width="100%"></p>

<h1 align="center">ComfyUI-COF</h1>

<p align="center"><b>Load a whole AI character in ComfyUI — with its rights.</b></p>

<p align="center">
<a href="https://github.com/yannsouetre/cof"><img src="docs/img/btn-format.png" alt="The COF format" width="250"></a>&nbsp;&nbsp;&nbsp;
<a href="https://yannsouetre.github.io/cof/viewer/"><img src="docs/img/btn-explore.png" alt="Explore a .cof" width="250"></a>&nbsp;&nbsp;&nbsp;
<a href="https://yannsouetre.github.io/cof/builder/"><img src="docs/img/btn-build.png" alt="Build one in your browser" width="250"></a>
</p>

A **COF (Character Open File)** `.cof` packs a whole AI character — reference views, outfits, places, LoRA, pre-encoded references (RefMods), voice, poses, motion clips, personality — together with its rights. These nodes turn a **preset** of that character (a language, an age, a style…) into images, a prompt, a LoRA, audio and files for your workflow. No extra Python package is needed.

## Nodes

**One node per category of the COF builder**, in the same order and with the same icons, after the loader. Each takes the loader's `cof` output.

| Node | Inputs | Outputs |
|---|---|---|
| 👤 **COF Load Character** (enough for most workflows) | `.cof` file, **preset** and **character** dropdowns (filled from the selected file), max size, allow intimate | `cof`, primary image, reference images, prompt, info |
| 📝 COF Personality | `cof` | the preset's personality as text (for a language-model node), language |
| 🖼️ COF Appearance | `cof`, **kind** (only the appearance types the preset has, with their counts: clothing — 4 images…), include offered, **pick** (the matching images by name), subject, angle | images, count, labels |
| 🧬 COF Identity Weights · Apply LoRA | model, clip, `cof`, strength (-1 = the weight recommended in the file) | model, clip, trigger words |
| 🧬 COF Identity Weights · Reference Latents (RefMods) | `cof`, models subfolder (default `refmods`) | paths of the exported `.safetensors`, base models |
| 🧊 COF 3D | `cof`, **pick** (the 3D files by name, or all) | paths (exported to `input/3d/`, where **Load 3D** lists them), labels, count |
| 👄 COF Voice | `cof`, **sample** (the voice samples, with their language) | audio, transcript, labels |
| 🏃 COF Postures | `cof`, **kind** (photo, photos, OpenPose, silhouette — with counts), include offered, **pick** | images, count, labels |
| 🎥 COF Motion | `cof`, **pick** (the motion clips by name, or all) | paths (exported to `input/cof/`), labels, count |
| 📁 COF Files | `cof`, **kind** (skill, memory, document…), **pick** | the text of the chosen files, their paths (exported to `input/cof/…/files/`, a skill keeps its folder layout), labels, count |

The builder's LoRA icon is a drawing; 🧬 stands for it here. **COF Files** reads and exports text files only (Markdown, text, JSON, YAML, CSV): a script inside a skill is listed, never extracted.

- **Presets**: the dropdown lists the presets of the selected file — and, in a file with several characters, those of the selected character. `(default)` is the character's default preset. After adding a `.cof` file, press **R** (refresh) in ComfyUI.
- **One image or several**: `primary_image` is one image, the preset's main one. `references` is a **batch** of every reference view (face, reference set, character sheet, body), for nodes that take several reference images (IP-Adapter, multi-reference editing models). To choose, take **COF Appearance**: its **pick** dropdown lists the images that match, by name (`image_001 — Bright studio facing the park (environment) · background.desktop`…), plus `mosaic_all` for all of them as one batch when there are 24 or fewer, or `mosaic_page_1`, `mosaic_page_2`… (24 at a time) when there are more.
- The dropdowns follow the **COF Load Character** node they are connected to: change the file, the preset or the character, and the lists update.

The **prompt** combines the trigger words of the preset's LoRA, the character's name and visual summary, the preset's age, the outfit, accessories and place applied by the preset, and its visual style.

## The character's rules are enforced

- **Adult content is never output by default.** Variants marked `nudity` (no genitals) need `allow_intimate` on and a preset of 18 or over; intimate variants also need the preset's effective `allowSexualUsage`. For a real person, both need their consent in the file.
- Using the **LoRA, references or voice of a real person** without a consent record in the file is **warned** (console and `info` output): you are responsible for that use.
- Only images, audio and `.safetensors` are read or extracted; nothing in the file is executed. A `.safetensors` with a broken header is refused.
- Read the **rights** in the `info` output, or open the file in the explorer: commercial use, redistribution, voice cloning and training are the author's call.

## Install

1. Copy this folder into `ComfyUI/custom_nodes/ComfyUI-COF`, or `git clone https://github.com/yannsouetre/ComfyUI-COF` there. No extra Python package is needed.
2. Put your `.cof` files in `ComfyUI/models/cof/` or `ComfyUI/input/cof/`. To try it, get `clara-mercier.cof` from the COF repository (`viewer/samples/`).
3. Restart ComfyUI. The nodes are in the **COF** category.
4. Drag `workflows/cof_preview.json` onto the canvas (laid out: images at the top; `cof_preview_api.json` is the same workflow in API format):
   - it shows Clara's primary image, her references, her offered outfits, one environment, her poses, her voice, her personality text, her motion clips, the prompt and the info;
   - it needs no model.

   `workflows/cof_txt2img_api.json` adds a checkpoint, the file's LoRA (if any) and a sampler; choose your checkpoint first.

## Using the preview workflow

The workflow opens with a **Read me** note, under the loader, that sums this up. It holds one node of each category (two for Appearance: the outfits, a place), laid out in the builder's order; the identity weights need a model and are in `cof_txt2img_api.json`.

- **The nodes are a fixed template.** They do not appear or disappear with the file's content. Only the **dropdown lists** follow the loaded file, preset and character.
- **Another type** (face, body, hair, accessory…): change **kind** in the node. It lists only the types the preset has, with their number of images or files. A node keeps its type, shown "— 0 images" when this preset has none.
- **Add a node**:
  1. Double-click an empty spot of the canvas, type `COF`, choose the node.
  2. Drag from the `cof` output of **COF Load Character** to the `cof` input of the new node.
  3. Connect a preview (**Preview Image** for images, **Preview Any** for text).
  4. Run.
- **`pick`**: `mosaic_all` shows every matching image (24 at most; beyond 24 the list offers `mosaic_page_1`, `mosaic_page_2`…); `image_001`… shows one image; `all` or `item_001`… for files.
- **include offered** (on by default): also the variants offered with the preset — a spare outfit, an accessory, a pose — available but not applied by default.
- **Missing content**: a preset without voice, 3D, clips or some type of image gives one second of silence, a blank image or an empty list, and says so in the labels. The run goes on.
- **One variant = a consistent set of one thing**: one place for an environment (several framings: desktop and mobile, angles), one garment, one hairstyle… A preset can have several places, each in its own variant. Only a reference set (loose photos) mixes things.

## Tests (outside ComfyUI)

```bash
cd tests && python -m pytest test_nodes.py --import-mode=importlib -c /dev/null
```

The tests stub `torch` and `comfy`. They need four sample files in `tests/samples/`:
- `clara-mercier.cof` and `clara-martien.cof`, from `viewer/samples/` of the COF repository;
- `mixed-adult-minor.cof`, which is the COF repository's `tests/fixtures/mixed-age` packed;
- `refmod.cof`, a test file with a pre-encoded reference.

## Changes

- **0.5.0** — one node per category of the COF builder, with its icon: COF Personality, Appearance, Identity Weights (Apply LoRA, Reference Latents), 3D, Voice, Postures, Motion, Files (new: skills, memories, documents — text files only). COF Images becomes COF Appearance (appearance only; postures have their own node), COF Persona Text becomes COF Personality, COF Motion & 3D Files is split in two. "include offered" is on by default. The preview workflow holds one node per category, in the builder's order, with a Read me note.
- **0.4.0** — the kind list shows only the kinds the preset has, with counts (the node keeps its own kind, even at 0); a preset without a voice gives one second of silence instead of stopping the run; image labels show size and orientation; the prompt leaves out the summary for a preset of another age; the preview workflow opens with a Read me note; images, voice samples and files chosen by name in dropdowns (a mosaic holds 24 images at most; pages of 24 beyond); preview workflow with wider columns.
- **0.3.0** — laid-out preview workflow (`cof_preview.json`), illustrated README, registry metadata.
- **0.2.0** — preset and character dropdowns, image index and labels, voice labels, COF Persona Text, COF Motion & 3D Files.
- **0.1.0** — first version.

## Licence

MIT — Yann Souetre.
