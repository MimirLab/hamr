# Upcoming changes

### Expressive narration

- Dialogue can take its direction from a fuller attribution such as “the doctor replied firmly” or “she said, her voice barely audible.” Hamr uses the reporting clause to tie the delivery to the right speaker.
- An authored sigh or cough between complete sentences can be performed inside one continuous quotation.
- An author-marked pronoun can carry the stress in a spoken line. EPUB stylesheet italics and Markdown emphasis can supply that cue. The plan records the marked word and its source location.

These changes run in ordinary listening mode. See the [expressive narration guide](docs/USAGE.md#expressive-narration) for examples, settings, and how to refresh an older EPUB.

### Scanned PDF source checks

Hamr can recover an opening “I” that OCR read as “T” in a quotation. Two enlarged readings of the source line must agree on the correction and reproduce the surrounding words. The source inventory keeps both readings. To apply the check to an existing job, run `hamr repair JOB --reimport`, then `hamr resume JOB`.

# hamr 0.4.2

### Expressive narration for every supported input format

Hamr 0.4.1 enabled several directions in normal listening but skipped expressive PDF delivery. Other supported directions still required a separate flag. In 0.4.2 the ordinary `hamr open` path applies dialogue direction, authored sounds, and heading cadence to EPUB, searchable and scanned PDF, Markdown, and plain text. Scanned PDF uses the existing offline English OCR setup. A PDF cue can span adjacent source lines and keeps its original locations in the companion.

- **Tone and pace.** Reporting phrases and nearby speaker evidence can guide whispering, measured delivery, urgency, irony, reassurance, and other authored tones.
- **Changes within a passage.** Hamr can preserve delivery through an attribution, build or fade across a quotation, and emphasize a phrase the author identifies.
- **Authored sounds and transitions.** Explicit sounds, heading cadence, and gentle entrances are available in the ordinary workflow. [Complete cue and command guide](docs/USAGE.md#expressive-narration)

Save `hamr settings --performance off --mode study` for plain technical reading. Existing jobs keep their saved takes and instructions. To apply the current directions to an existing job, run `hamr repair JOB --performance auto`, then `hamr resume JOB`. [Commands and available directions](docs/USAGE.md#expressive-narration)

Contextual English readings and offline speech checking also run in the normal workflow. The status and published report now show the result of the offline speech check instead of leaving a completed check marked pending. Use the [front page](README.md#what-hamr-can-do) for the feature and command overview, the [verifier presets](docs/USAGE.md#speech-verifier-presets) for available speech checkers, and the [engine guide](docs/ENGINES.md) for hardware-specific results.

## hamr 0.4.1

### Expressive narration by default

New books in listening mode use clear cues in the prose to shape delivery. Hamr can whisper through an intervening attribution, carry a supported tone into the same speaker's next turn, build urgency, mark an author's chosen emphasis, and enter gently after a whisper. The complete passage stays together. The normal speech check looks for missing or changed words before publication.

Nigel's authored opening chuckle is also available automatically on Linux Vulkan F16. Save plain or technical delivery for new books with `hamr settings --performance off --mode study`. The setting can be overridden for an individual book, and existing jobs keep their saved delivery until revised. [How to use expressive narration](docs/USAGE.md#expressive-narration)

## hamr 0.4.0

### Expressive narration

Hamr uses cues in the prose to guide dialogue and quiet scenes. Automatic delivery supports authored leading sighs for Nigel Thornberry and Cave Johnson on Linux CPU Q8. On Linux Vulkan Q8, it supports trailing sighs and directly attributed shouts for both voices. Nigel can also perform an authored opening chuckle and read a quiet scene with restrained anticipation.

Additional delivery controls cover whispers across an attribution, emphasis on an author-specified phrase, growing urgency, and other authored directions. Matched listening and word checks cover these cases on selected passages. The ordinary book workflow has passed real chaptered publication tests with automatic delivery.

Speech checking keeps the take when a directed reading needs review. It reports an ambiguous vocalization at an authored cue separately from missing or changed words. [Delivery controls and commands](docs/USAGE.md#expressive-narration)

### Reading and recovery fixes

EPUB tables now read the first data row, use authored column labels, and carry image descriptions, equations and linked notes into the reading. Existing jobs can opt in with `hamr repair JOB --reimport --upgrade-rules`.

Speech checks now keep quantities with their units and negation with its place in the sentence. Setup selects the offline verifier by hardware. Fluid INT8 becomes the primary whole-book verifier on eligible Linux x86-64 hosts. The lighter Whisper pair remains available from `--asr-profile whisper`. `hamr transcribe` uses the active verifier to investigate saved speech or local audio. Automatic retakes of ordinary narration require matching evidence from two distinct models and keep their budget through interruption. Reimport preserves a selected retake when it can identify the same passage. [Verifier presets](docs/USAGE.md#speech-verifier-presets)

Setup verifies extracted runtime bytes against the pinned archive and accounts for downloads already on disk. Combined setup runs the requested checks before activating the speech profile. The queue records blocked jobs with a recovery action. New audiobook publications preserve literal text in WebVTT and decode the complete M4B before making it available.

If the offline speech checker is missing, Hamr prints a recovery command for the current checkout or installation and the same state directory. `setup --checks-only` can upgrade the verifier in an existing installation without an engine profile.

### Scanned and mixed PDFs

Hamr can now OCR a scan inset without replacing the surrounding native text. It reads simple two-column scans in column order, keeps the source positions of captions and recognized labels, and reuses completed OCR observations after an interruption. For English scanned PDFs, run `hamr setup --ocr-only` with Tesseract installed. Dense historical layouts still need broader qualification. [PDF and OCR guide](docs/USAGE.md#pdf-and-ocr)

`hamr status JOB` shows where the M4B will be written and whether a saved book is awaiting an automatic check or a particular source or listening review. Ordinary `hamr render` runs through speech checking and publication, as does `hamr resume`. If a recording needs listening, the status gives a command to export the first flagged passage. [Recovery and repair](docs/USAGE.md#recovery-and-repair)

An installed Linux build turned a two-page scan into a checked, playable 36.94-second M4B. The render reused the first OCR page after a controlled interruption, and the finished book verified after cleanup. Separate layout checks covered mixed pages and simple two-column reading order. The Linux suite for this update passed 771 tests with one Windows-only test skipped.

### Speech engine setup

`hamr setup` starts with one plain-language recommendation. `--choose` opens the advanced list. `--auto` accepts an eligible recommendation for scripted installs. The engine guide records the evidence and exact model recipes behind the choices. [Engine comparison and Windows setup](docs/ENGINES.md)

Hamr includes a Windows x64 installer and the pinned Hoppou Windows runtime. A native hosted runner installed the wheel, downloaded and checked CPU Q8, then rendered, checked and verified a chaptered M4B. Windows and Linux CPU Q8 recovered from forced exits with the original committed audio hashes. Both Windows M4Bs still verified after cleanup. Native Windows GPU rendering remains unqualified. CUDA and Apple candidates have their own qualification plans in the engine guide. [Windows trial](https://github.com/MimirLab/hamr/actions/runs/44863152453)

The Radeon comparison covers F16, stock Q8 and Q8 with original depth and codec tensors. Each produced a continuous take longer than 45 seconds. The engine guide records the measured time and word checks. Model placement and audible quality are separate checks.

### Natural English readings and document import

Document normalization introduced `faithful-13`. Markdown imports whole footnote definitions and retains reference locations. EPUB checks the protection method and the declared resource type before admitting font-only obfuscation. Its source inventory includes opaque embedded media. Plain text has a strict Unicode decoder with source line locations. [Document planning guide](docs/USAGE.md#check-how-a-document-will-be-read)

The planner preserves leading identifier zeros and decimal precision in supported readings. It records exact offsets in the input used for each typed rewrite. Known PDF nonpainting text objects can trigger an independent OCR observation. Conflicting text, unmapped glyphs and inaccessible content can hold a job before speech generation.

A two-paragraph Markdown note survived real synthesis and publication. A plain-text control retained its unaffected PCM through a targeted clock-reading repair and published after offline QA. Both M4Bs decoded with mpv. A hidden PDF text disagreement and a private-use glyph in plain text each held publication at source review. DOCX, HTML, general PDF table recognition and other adapters still need their own source and reading-order qualification.

Contextual reading runs in the default English narration path. Hamr uses nearby phrasing, table columns, explicit definitions and selected conventions. It can read $100 as “one hundred dollars,” distinguish an elapsed time from a clock reading, and preserve a command option in the companion while saying it aloud. A settings file can change cue lists, lexicon readings or weights without changing code. The plan records its candidates and their evidence. Ordinary ambiguity keeps its source wording. Strict mode and validated local reviewer replies are optional. The installed planner was exercised on 240 EPUB, Markdown and PDF cases. Selected natural readings also passed local speech checks and M4B playback. [Context settings and review](docs/USAGE.md#tune-readings-that-depend-on-context)

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

Q6 and Q4 on Vulkan, Q8 with CPU text encoding, and CPU F16, Q6 and Q4 are selectable in setup. Render trials for these six profiles are pending. CPU Q8 passed short-book publication and recovery on Linux and native Windows. Its long-book timing and memory characterization are still open. The RAM ranges are planning estimates. CUDA and MLX are research entries that still need setup adapters. The [profile guide](README.md#which-profile-should-i-choose) distinguishes these states.

To test a profile, run a control in an isolated state directory. The following example uses Vulkan Q6.

```sh
trial=$(mktemp -d)
mkdir -p "$trial/output"
printf '%s\n' 'The fox waits beneath the old oak tree. Do not open the gate. Fifty-seven birds return at dawn, and four remain beside the river.' > "$trial/control.md"
hamr --home "$trial/state" doctor --json > "$trial/doctor.json"
hamr --home "$trial/state" setup --profile vulkan-q6 --json > "$trial/setup.json"
hamr --home "$trial/state" open "$trial/control.md" --voice cast:nigel-thornberry --output "$trial/output" --yes --json
```

Compare the whole spoken control to the exact planned text and an F16 control, including negation, numbers, voice detail, boundaries and nonverbal artifacts. Record elapsed generation time and sample peak process RSS, system RAM and GPU memory through model startup and speech; desktop usage and sampling interval belong with those measurements. Then qualify longer narration with at least two cast references, offline ASR and full listening. Inspect any held publication through the field guide's source/speech review workflow rather than bypassing it. Repeat on AMD, NVIDIA and Intel hardware before claiming broad compatibility or publishing benchmark rankings.

Synthetic controls, decoder checks and transcript comparisons cannot certify expressive delivery or exclude every audible defect. Full-book listening and broader difficult-document qualification continue separately. No hardware-stability claim follows from this release. Missing narration remains a publication hold; the engine does not silently skip failed takes.
