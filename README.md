# ComfyUI-COF

Load **COF (Character Open File)** characters in ComfyUI. A `.cof` file packs a whole AI character — reference views, outfits, LoRA, pre-encoded references (RefMods), voice, personality — together with its rights. These nodes turn a preset of that character into images, a prompt, a LoRA and audio for your workflow.

Format and tools: <https://github.com/yannsouetre/cof> · explorer: <https://yannsouetre.github.io/cof/viewer/> · builder: <https://yannsouetre.github.io/cof/builder/>

## Nodes

| Node | Inputs | Outputs |
|---|---|---|
| **COF Load Character** (simple — enough for most workflows) | `.cof` file, preset (id or label, empty = default), character (for a cast), max size | `cof`, primary image, reference images (face, reference set, character sheet, body), prompt, info |
| COF Images (advanced) | `cof`, kind (clothing, accessory, environment, posture…), include offered, subject, angle | images, count |
| COF Apply LoRA (advanced) | model, clip, `cof`, strength (-1 = the weight recommended in the file) | model, clip, trigger words |
| COF Reference Latents / RefMods (advanced) | `cof`, models subfolder (default `refmods`) | paths of the exported `.safetensors`, base models |
| COF Voice (advanced) | `cof`, sample index | audio, transcript |

The **prompt** combines the trigger words of the preset's LoRA, the character's name and summary, the preset's age, the outfit, accessories and place applied by the preset, and its visual style.

## The character's rules are enforced

- **Adult content is never output by default.** Variants marked `nudity` (no genitals) need `allow_intimate` on and a preset of 18 or over; intimate variants also need the preset's effective `allowSexualUsage`. For a real person, both need their consent in the file.
- Using the **LoRA, references or voice of a real person** without a consent record in the file is **warned** (console and `info` output): you are responsible for that use.
- Only images, audio and `.safetensors` are read or extracted; nothing in the file is executed. A `.safetensors` with a broken header is refused.
- Read the **rights** in the `info` output, or open the file in the explorer: commercial use, redistribution, voice cloning and training are the author's call.

## Install

1. Copy this folder into `ComfyUI/custom_nodes/ComfyUI-COF`, or `git clone https://github.com/yannsouetre/ComfyUI-COF` there. No extra Python package is needed.
2. Put your `.cof` files in `ComfyUI/models/cof/` or `ComfyUI/input/cof/`. To try it, get `clara-mercier.cof` from the COF repository (`viewer/samples/`).
3. Restart ComfyUI. The nodes are in the **COF** category.
4. Drag `workflows/cof_preview_api.json` onto the canvas:
   - it shows Clara's primary image, her references, her offered outfits, the prompt and the info;
   - it needs no model.

   `workflows/cof_txt2img_api.json` adds a checkpoint, the file's LoRA (if any) and a sampler; choose your checkpoint first.

## Tests (outside ComfyUI)

```bash
cd tests && python -m pytest test_nodes.py --import-mode=importlib -c /dev/null
```

The tests stub `torch` and `comfy`. They need four sample files in `tests/samples/`:
- `clara-mercier.cof` and `lea-martien.cof`, from `viewer/samples/` of the COF repository;
- `mixed-adult-minor.cof`, which is example 09 packed;
- `refmod.cof`, a test file with a pre-encoded reference.

## Licence

MIT — Yann Souetre.
