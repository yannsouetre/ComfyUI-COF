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

| Node | Inputs | Outputs |
|---|---|---|
| **COF Load Character** (simple — enough for most workflows) | `.cof` file, **preset** and **character** dropdowns (filled from the selected file), max size | `cof`, primary image, reference images, prompt, info |
| COF Images (advanced) | `cof`, **kind** (only the kinds the preset has, with their counts: clothing — 4 images, posture — 3 images…), include offered, subject, angle, **pick** (a dropdown of the matching images by name) | images, count, labels |
| COF Voice (advanced) | `cof`, **sample** (a dropdown of the voice samples, with their language) | audio, transcript, labels |
| COF Persona Text (advanced) | `cof` | the preset's personality as text (for a language-model node), language |
| COF Motion & 3D Files (advanced) | `cof`, motion clips or 3D files, **pick** (a dropdown of the files by name, or all) | paths of the exported files, labels, count |
| COF Apply LoRA (advanced) | model, clip, `cof`, strength (-1 = the weight recommended in the file) | model, clip, trigger words |
| COF Reference Latents / RefMods (advanced) | `cof`, models subfolder (default `refmods`) | paths of the exported `.safetensors`, base models |

- **Presets**: the dropdown lists the presets of the selected file — and, in a file with several characters, those of the selected character. `(default)` is the character's default preset. After adding a `.cof` file, press **R** (refresh) in ComfyUI.
- **One image or several**: `primary_image` is one image, the preset's main one. `references` is a **batch** of every reference view (face, reference set, character sheet, body), for nodes that take several reference images (IP-Adapter, multi-reference editing models). To choose, take **COF Images**: its **pick** dropdown lists the images that match, by name (`image_001 — Bright studio facing the park (environment) · background.desktop`…), plus `mosaic_all` for all of them as one batch when there are 24 or fewer, or `mosaic_page_1`, `mosaic_page_2`… (24 at a time) when there are more.
- The dropdowns follow the **COF Load Character** node they are connected to: change the file, the preset or the character, and the lists update.
- **Motion clips** (BVH) are exported to `input/cof/`, **3D files** (VRM, GLB, OBJ…) to `input/3d/`, where the **Load 3D** node lists them.

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

## Tests (outside ComfyUI)

```bash
cd tests && python -m pytest test_nodes.py --import-mode=importlib -c /dev/null
```

The tests stub `torch` and `comfy`. They need four sample files in `tests/samples/`:
- `clara-mercier.cof` and `clara-martien.cof`, from `viewer/samples/` of the COF repository;
- `mixed-adult-minor.cof`, which is the COF repository's `tests/fixtures/mixed-age` packed;
- `refmod.cof`, a test file with a pre-encoded reference.

## Changes

- **0.4.0** — the kind list shows only the kinds the preset has, with counts; images, voice samples and files chosen by name in dropdowns (a mosaic holds 24 images at most; pages of 24 beyond); preview workflow with wider columns.
- **0.3.0** — laid-out preview workflow (`cof_preview.json`), illustrated README, registry metadata.
- **0.2.0** — preset and character dropdowns, image index and labels, voice labels, COF Persona Text, COF Motion & 3D Files.
- **0.1.0** — first version.

## Licence

MIT — Yann Souetre.
