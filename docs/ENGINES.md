# Speech engines and model quality

Run `hamr setup` for one suggested model. Press Enter to install it. Setup checks startup and runs a short silent take when it needs audio to verify the actual device. Use `hamr setup --choose` or `hamr profiles` to compare the choices. `hamr setup --auto` is for scripts that want a recommendation backed by local rendering evidence and a credible hardware fit.

The fit estimate uses the graphics driver and current available memory. Startup and a short silent generation check verify the device that actually produced audio. The full model is the reference when it fits. Sound, memory during load, book throughput and coverage of voice modes all matter when choosing a smaller model.

## What hamr can use

| Host | Runtime and model | Evidence |
| --- | --- | --- |
| Linux x86_64 with Vulkan | Hoppou 0.1.7, one of F16, Q8, Q6 or Q4, plus Q8 with CPU text encoding | F16 long narration and matched F16/Q8 controls on RX 6800 XT. Q8 also passed chaptered publication. Render trials are pending for Q6, Q4 and CPU text encoding. |
| Linux x86_64 on CPU | The same pinned Hoppou formats via its CPU route | Q8 short control passed real render, forced interruption, recovery, offline speech checking and playback. Long-book turnaround and other precisions need trials. |
| Windows x86_64 with AVX2 | The upstream Hoppou Windows ZIP, same matched model files | **CPU Q8 tested.** Setup, checked publication and forced-exit recovery passed on native Windows. The Vulkan preview still needs a physical Windows GPU trial. [Hosted run](https://github.com/MimirLab/hamr/actions/runs/44863152453) |
| Apple Silicon | Swift/MLX BF16 and affine INT8 are first candidates | Research. Needs an owned Apple adapter and target-device trials. |
| NVIDIA with a working Vulkan driver | The Hoppou route above | Native CUDA remains a separate comparison. |
| AMD Strix Halo | Vulkan as available through Hoppou | Speakrail ROCm remains a separate comparison. |

Linux needs the Vulkan loader even when using the CPU profile. Windows uses the loader installed by the graphics driver. For Windows use PowerShell with Git, uv, Python 3.12, FFmpeg and ffprobe available. mpv is used for playback.

```powershell
git clone https://github.com/MimirLab/hamr.git
cd hamr
uv build --wheel
.\install.ps1 -PersistPath
hamr doctor
hamr setup
hamr open "$HOME\Downloads\My book.epub" --voice cast:nigel-thornberry
```

For isolated trials, put `--home DIRECTORY` before each hamr command. Windows defaults to `%LOCALAPPDATA%\hamr`. The Windows Q8 CPU control published a verified 2.43-second M4B. In a second render, hamr was forced to exit immediately after saving a take. `resume` preserved the take's checksum, checked the speech and published a second M4B. The recovered book decoded with FFmpeg, and both publications still verified after cleanup. [Native recovery run](https://github.com/MimirLab/hamr/actions/runs/44863152453)

The Vulkan runtime loaded under Wine and failed at its first queue submission. To qualify Windows graphics on a physical PC, run this in PowerShell after installing Hamr. Use `vulkan-q8` if `hamr doctor` recommends a smaller model.

```powershell
$trial = Join-Path $env:TEMP ('hamr-vulkan-' + [guid]::NewGuid())
$output = Join-Path $trial 'books'
$state = Join-Path $trial 'state'
New-Item -ItemType Directory -Path $output -Force | Out-Null
$control = Join-Path $trial 'Control.md'
Set-Content -LiteralPath $control -Value 'Fifty-seven birds returned at dawn. Do not open the gate.'
hamr --home $state doctor
hamr --home $state setup --profile vulkan-f16 --json
$book = hamr --home $state open $control --output $output --voice cast:nigel-thornberry --yes --json | ConvertFrom-Json
hamr --home $state agent inspect $book.job --verify
hamr --home $state play $book.job
```

Record `health.actual_device` from setup and `publication_verified` from inspection. Then repeat with a multi-chapter source. Interrupt during rendering, run `hamr --home $state resume JOB`, and listen across the recovered boundary. Inspect the chapter times and keep the test states isolated from your regular books.

The Ryzen 7 5800X Linux Q8 CPU control produced 3.04 seconds of speech. The first `open` call took 38 seconds through startup, reference processing and durable audio commit. A forced exit after that commit left the PCM checksum intact. `resume` reused the take, passed offline speech checking and published a 3.39-second M4B that decoded in mpv.

## What the quantization labels mean

These are tensor counts from the exact pinned files, including the active Qwen audio codec. F16 keeps 578 tensors in F32. Q8 also compresses eligible depth and codec matrices. In Q6 and Q4, some eligible matrices remain Q8. Hamr ties the precision report to each model hash, and `hamr profiles --json` exposes the stage counts.

| Pinned file | Whole file | Depth decoder | Embedded codec |
| --- | --- | --- | --- |
| F16 | 583 F16, 578 F32 | 85 F16, 26 F32 | 116 F16, 282 F32 |
| Q8_0 | 583 Q8_0, 578 F32 | 85 Q8_0, 26 F32 | 116 Q8_0, 282 F32 |
| Q6_K | 450 Q6_K, 133 Q8_0, 578 F32 | 85 Q6_K, 26 F32 | 116 Q6_K, 282 F32 |
| Q4_K | 450 Q4_K, 133 Q8_0, 578 F32 | 85 Q4_K, 26 F32 | 116 Q4_K, 282 F32 |

See the [pinned GGUF files](https://huggingface.co/HoppouAI/Breeze-TTS-2.cpp/tree/e4fae6493ffb8bbeacc402d1b36f18fc8d5), [the quantizer](https://github.com/HoppouAI/Breeze-TTS-2.cpp/blob/4110a9fff4e47d4d1ba850d92d81f3ba86bf39c6/apps/quantize/main.cpp) and [the converter](https://github.com/HoppouAI/Breeze-TTS-2.cpp/blob/4110a9fff4e47d4d1ba850d92d81f3ba86bf39c6/scripts/convert_hf_to_gguf.py). Use `python tools/inspect_gguf.py FILE_OR_PINNED_URL` to reproduce the inventory. It reads a bounded header without loading weights. Aggressive depth variants need their own long-form checks after upstream reported audible degradation over time. [Hoppou release notes](https://github.com/HoppouAI/Breeze-TTS-2.cpp/releases/tag/v0.1.7)

## Candidate work by hardware

| Candidate | What distinguishes it | Next check |
| --- | --- | --- |
| Official CUDA BF16 | Official PyTorch eager and fast paths with distinct codec modes. | Pin the CUDA environment and run the reference clone and design suite on NVIDIA. [Source](https://github.com/breezeblue-ai/breeze-tts/tree/daa6cb2efe5471f09369229f322d20bb956cb3c3) |
| drbaph with remominor | Hybrid INT8 backbone and text encoder with preserved BF16 depth. Text encoder only is a separate recipe. | Check exact load peak, clone quality and long rendering against official BF16 and Vulkan Q8 on 6–8 GB NVIDIA cards. [Loader](https://github.com/remominor/breeze-tts-api/tree/22ab8c6586b8b9891d52c5bb724888e9669dbe64) · [Weights](https://huggingface.co/drbaph/Breeze-TTS-2-comfyui) |
| alimpfard, HQQ, and other CUDA challengers | alimpfard's prequantized loader and host embeddings target 4 GB cards. HQQ packs selected decoder linears. RACErace is on-load INT8. wejoncy includes activation quantization. | Measure load and generation separately. Validate packer and codec compatibility, model terms and real PCM. [alimpfard](https://github.com/alimpfard/breeze-tts-2/tree/26d42f53dbad35074f8acf41f29c122bb4235c48) · [HQQ](https://huggingface.co/mesmertech/Breeze-TTS-2-int4-hqq-g64) · [RACErace](https://github.com/RACErace/breeze-tts-8g) · [wejoncy](https://github.com/wejoncy/breeze_tts_cpp) |
| Speakrail | Calibrated GPTQ overlay still needs the original checkpoint. Author tests on Radeon 8060S do not qualify RDNA2. | Test the complete model/overlay load with the exact Strix Halo device and compare INT8 versus a selective INT4 recipe. [Runtime](https://github.com/speakrail/speakrail) · [Overlay](https://huggingface.co/WarrenHal/breeze-tts-2-gptq-int4) |
| Apple Swift and community MLX | Swift affine INT8 group 64 and community MXFP8 group 32 are separate numerical formats. Gust chiefly changes depth. | Start with Swift BF16/INT8 and check clone/design, memory and locality on Apple Silicon. [Swift](https://github.com/soniqo/speech-swift) · [Community weights](https://huggingface.co/mlx-community/Breeze-TTS-2-mlx) · [Gust](https://github.com/Shoofio/gust-breeze-tts-2-mlx-server) |
| Sirocco and Vireo | Sirocco preserves sensitive components, with gated small downloads. Vireo uses pruning as well as compression. | Compare held-out long-form listening. Test Sirocco's authorized acquisition and report Vireo separately. [Sirocco](https://github.com/vanch007/mlx-breeze-tts2) · [Vireo](https://huggingface.co/mchen04/Vireo-TTS-3B-MLX-mixed4bit) |
| Portable C++ and Rust | audio.cpp, CrispASR/cstr and WarmBreeze have their own models and codec contracts. mecha is a clone-oriented challenger. | Use the exact matched runtime and codec. Qualify stopped generation and each voice mode before claiming parity. [audio.cpp](https://github.com/0xShug0/audio.cpp) · [CrispASR weights](https://huggingface.co/cstr/breeze-tts-2-GGUF) · [WarmBreeze](https://huggingface.co/marafx2007/WarmBreeze-TTS-2-gguf) · [mecha](https://huggingface.co/mecha-factory/mecha-speech-gguf) |
| Blackwell NVFP4 and AX650 | Nomos documents quality failures in experimental selective NVFP4 paths. AX650 is board-specific with limited voice modes. | Revisit when upstream clears a Breeze-specific quality or voice-mode gap. [Nomos](https://github.com/lightofbaldr/nomos-nvfp4) · [AX650](https://huggingface.co/yunyu1258/breeze-tts-2-ax650) |

These research rows describe upstream artifacts and the test that would advance each candidate. Use `hamr profiles --json` for the catalog profile and package identifiers. Their source revisions, exact files and checksums are in the [embedded catalog](../src/audiobook/catalog.json). Hamr downloads only from an implemented profile you select.

## Compare two builds

Use isolated setup directories for the reference and challenger, then run the comparison from the checkout.

```sh
hamr --home f16-control setup --profile vulkan-f16 --engine-only
hamr --home q8-control setup --profile vulkan-q8 --engine-only
uv run python -m tools.compare_breeze --output comparison f16-control/settings.json q8-control/settings.json
```

Open `comparison/listen.html` before looking at the answer key. The clips share text, original clone references, and seeds. Compare omissions and insertions, voice likeness, expression, clarity and drift near the end. The receipts record runtime/model hashes, the reference identity, the actual device, request time and duration. The memory report samples process RSS and available GPU counters through startup and generation. AMD sysfs readings include other applications.

Use the application's passage planning, offline checks and M4B assembly as a second test. Compare long continuous speech and the assembled book. Test clone, design and any advertised direction separately. Run multiple seeds on both builds so normal variation informs the listening threshold. A transcript match can support word fidelity. The listener still judges speaker identity, delivery and artifacts.


### A protected Q8 candidate

`tools/mix_hoppou.py` checks both pinned inputs by SHA-256 and makes a local model. It copies the depth decoder and codec directly from F16 and the other tensors from Q8. The 3,951,984,292-byte result has 382 Q8_0, 201 F16 and 578 F32 tensors, including all 85 F16 depth matrices and 116 F16 codec matrices. It is a comparison candidate. The shipped profiles still use their original weights.

```sh
uv run python -m tools.mix_hoppou   f16-control/assets/vulkan-f16/breeze-tts-2-f16.gguf   q8-control/assets/vulkan-q8/breeze-tts-2-q8_0.gguf   protected-q8.gguf
```

To compare it, copy the Q8 setup identity into a separate settings file. Set `profile` to `vulkan-q8-protected-depth-codec`, set `model` to the absolute output path, and set `model_sha256` from `protected-q8.recipe.json`. Pass the new settings file as the third input to `tools.compare_breeze`. This is an operator procedure for a local evaluation. It requires no changes to the built-in model catalog.

### Radeon RX 6800 XT control results

| Build | Continuous take | Request wall time | Transcript edits across 248 planned words, base.en / small.en |
| --- | ---: | ---: | ---: |
| Pinned F16 | 49.76 s | 33.66 s | 1 / 0 |
| Stock Q8 | 53.84 s | 23.43 s | 0 / 1 |
| Q8 with F16 depth and codec | 47.76 s | 28.35 s | 1 / 1 |

Both recognizers recovered the tested quantities and negations for all three builds. The takes differ in duration. Blind listening still needs to establish whether the protected recipe improves timbre and delivery.

Each build also completed a short voice-design take without a reference. These cold engine starts began with 0.62–0.64 GiB of graphics memory in use. Peaks are total graphics memory on this machine, including the desktop, sampled every 0.2 seconds. Long passages and longer references can use more.

| Build | Engine startup | Peak graphics memory during load | Peak during short design take |
| --- | ---: | ---: | ---: |
| Pinned F16 | 21.80 s | 6.46 GiB | 6.87 GiB |
| Stock Q8 | 8.49 s | 3.95 GiB | 4.36 GiB |
| Q8 with F16 depth and codec | 7.17 s | 4.30 GiB | 4.71 GiB |

The protected recipe also passed hamr’s normal render, text check and M4B assembly for a three-passage narration control. The resulting M4B runs 53.53 seconds and decoded in mpv.
