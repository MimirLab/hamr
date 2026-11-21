# hamr 0.4 development

`hamr setup` starts with one plain-language recommendation. `--choose` opens the advanced list. `--auto` accepts an eligible recommendation for scripted installs. The engine guide records the evidence and exact model recipes behind the choices. [Engine comparison and Windows preview](docs/ENGINES.md)

The development preview includes a Windows x64 installer and the pinned Hoppou Windows runtime. A native hosted runner installed the wheel, downloaded and checked CPU Q8, then rendered, checked and verified a chaptered M4B. Windows and Linux CPU Q8 recovered from forced exits with the original committed audio hashes. Both Windows M4Bs still verified after cleanup. Native Windows GPU rendering remains unqualified. CUDA and Apple candidates have their own qualification plans in the engine guide. [Windows trial](https://github.com/MimirLab/hamr/actions/runs/44863152453)

Three matching Radeon controls are available for blind listening: F16, stock Q8 and Q8 with original depth and codec tensors. Each has a real continuous take exceeding 45 seconds. The engine guide records the measured time and word checks. Model placement and audible quality are separate checks.

## hamr 0.3.0

[Install hamr and make a first book](README.md#epub-to-audiobook-in-three-commands). Run `man hamr` from the terminal for command help.

Setup now prepares offline speech checking alongside the chosen speech model. It shows the size and speed of asset transfers and checks that the models load. If your current installation already has a working engine, run `hamr setup --checks-only` after upgrading.

`hamr open DOCUMENT --voice cast:nigel-thornberry` renders, checks and assembles an M4B in one workflow. Resume continues that workflow after an interruption. Source and speech findings still hold publication. Progress and review state are also available through the JSON CLI.

## Checks

- Installed the wheel in a fresh user directory, used the direct command outside the source checkout, and verified section-1 lookup for `hamr` and the `audiobook` alias.
- Downloaded and verified both pinned recognition models, loaded them on CPU and started the F16 Vulkan engine from checked assets.
- Published a real two-chapter EPUB control after speech checks, then verified the chapters and decoded the M4B in mpv. A separate control with a speech disagreement stayed in review.
- Interrupted a Markdown render after a committed take and resumed it to publication. The retained take kept its original PCM checksum.
- Retook one EPUB passage, checked the new speech and published a second revision. Cleanup preserved both ready books.
- The source archive and wheel checks verify the public asset boundaries. The installer keeps existing saved jobs, voices and model pins.

The [profile guide](README.md#which-profile-should-i-choose) lists supported profiles and memory estimates. F16 long narration and Q8 short controls have prior rendering evidence on an RX 6800 XT. Cast introductions are the 45 approved offline playback previews. Breeze conditions on the separate original recordings and exact transcripts.

## Remaining qualification

Q6, Q4, CPU text encoding, and the F16, Q6 and Q4 processor-only choices are **implemented but experimental**. CPU Q8 has short-control evidence and needs long-book timing, memory and listening checks. RAM ranges in the guide are planning estimates. CUDA/MLX and other research entries need setup adapters. The [profile guide](README.md#which-profile-should-i-choose) distinguishes these states.

To qualify an experimental profile without changing active book settings, select its ID in an isolated state. This example uses Q6; repeat with each experimental ID on hardware with sufficient memory:

```sh
trial=$(mktemp -d)
printf '%s\n' 'The fox waits beneath the old oak tree. Do not open the gate. Fifty-seven birds return at dawn, and four remain beside the river.' > "$trial/control.md"
hamr --home "$trial/state" doctor --json > "$trial/doctor.json"
hamr --home "$trial/state" setup --profile vulkan-q6 --json > "$trial/setup.json"
hamr --home "$trial/state" queue "$trial/control.md" --voice cast:nigel-thornberry --output "$trial/output" --json
# Replace JOB with the job ID printed by queue:
hamr --home "$trial/state" show JOB --content
hamr --home "$trial/state" preview JOB --utterance u00001 --play
hamr --home "$trial/state" resume JOB
hamr --home "$trial/state" status JOB --json
```

Compare the whole spoken control to the exact planned text and an F16 control, including negation, numbers, voice detail, boundaries and nonverbal artifacts. Record elapsed generation time and sample peak process RSS, system RAM and GPU memory through model startup and speech; desktop usage and sampling interval belong with those measurements. Then qualify longer narration with at least two cast references, offline ASR and full listening. Inspect any held publication through the field guide's source/speech review workflow rather than bypassing it. Repeat on AMD, NVIDIA and Intel hardware before claiming broad compatibility or publishing benchmark rankings.

Synthetic controls, decoder checks and transcript comparisons cannot certify expressive delivery or exclude every audible defect. Full-book listening and broader difficult-document qualification continue separately. No hardware-stability claim follows from this release. Missing narration remains a publication hold; the engine does not silently skip failed takes.
