# Using hamr

Local audiobooks from EPUB, Markdown and PDF, with a built-in character cast, source review and recoverable Breeze rendering. The current release is **hamr 0.2.2**. Read [release notes](../RELEASE.md) for measured checks and remaining qualifications.

Unsupported phonemes/languages and ambiguous source structure require review. QA preserves signs, precision, units and identifier values; publication approvals bind the comparison policy, plan and physical recordings.

Use `--notes after_paragraph|inline|companion` to select a linked-note reading policy. `--chapters '5,2-3,2'` selects source chapter occurrences2,3,5 in their original order; unselected source stays in the companion. `hamr repair JOB --chapters all` restores the full selection. Preflight groups related authored pronunciation leads without extending a correction or listening approval to other occurrences.

## Install and setup

Python 3.12+ and FFmpeg/ffprobe are required for rendering; mpv is used for playback. Import/planning needs no GPU or model. Dependencies are pinned in `uv.lock`. While a book is rendering, keep its installed environment fixed; use a separate installation for candidate checks and stop at a durable checkpoint before upgrading. For a fresh installation, including from a directory outside this checkout:

```sh
uv sync --frozen --extra test
uv build --wheel
./install.sh
hamr doctor
hamr setup
```

Start with [Which profile should I choose?](../README.md#which-profile-should-i-choose) for the hardware, memory, download and testing table. The application includes every F16/Q8/Q6/Q4 choice for both CPU and Vulkan, plus Q8 with CPU text encoding. Large weights are downloaded only after selection. Native CUDA/MLX and other research catalog entries have no setup adapter. The shipped adapter requires Linux x86_64, AVX2 and the Vulkan loader library; GPU profiles also require a working vendor Vulkan driver.

`hamr doctor` inventories the resident GPU/driver and available memory, then explains a suggested starting profile. Its built-in Vulkan probe creates no logical device and submits no GPU work. Optional `vulkaninfo` and `nvidia-smi` add diagnostics when installed. Unknown memory, shared integrated memory, multiple GPUs and failed probes are reported as uncertainty. Advice is based on planning estimates, not a speech benchmark or a promise that every allocation will fit. `hamr doctor --json` includes the observations, per-profile compatibility reasons, download sizes, testing status and recommendation; it does not activate a profile.

Interactive `hamr setup` offers the recommendation and all implemented choices. Select by number or ID, or press Enter to accept the suggestion. Scripts and noninteractive shells must specify a profile, for example:

```sh
hamr setup --profile vulkan-q8
hamr profiles --json
```

Setup downloads one catalog-pinned runtime/model, verifies size/SHA-256, retains model terms and performs real startup before activating settings. Other installed profiles and job pins survive a setup failure. Repeating the command resumes matching partial downloads; `--cache DIRECTORY` accepts matching, verified asset filenames already on disk. A memory error suggests smaller models/CPU offload; Vulkan startup errors identify the driver/loader check and startup log. CPU profiles need the loader library but not a working Vulkan GPU. **Only combinations listed in RELEASE.md have real rendering evidence**; the remaining implemented choices are experimental. Breeze model/output terms are separate from this application's MIT license: personal research/noncommercial use is the initial scope.

Backend profiles (`hamr setup --profile vulkan-q8`) select model precision and execution hardware. Saved listening profiles (`hamr profile technical --mode study`, then `hamr plan book.epub --profile technical`) store document-reading preferences. They have separate command dispatch; a missing saved profile reports its exact path.

Fresh installs store app state, config, cache and locks in `~/.local/share/hamr`, with the installed environment in `~/.local/share/hamr-app/venv`. Use `--home PATH` or `HAMR_HOME` for an isolated profile. Use a local filesystem supporting SQLite WAL. The CLI never downloads a model during ordinary planning, rendering or recovery. `setup` acquires model assets. Models and references are pinned to each job.

Upgrades reuse existing owned `~/.local/share/audiobook` state and the active `audiobook-app/venv` in place, preserving saved jobs, recordings and absolute model/reference paths. `hamr` is the primary command; `audiobook` and `python -m audiobook` remain compatibility entry points. `python -m hamr` is also supported. State selection is explicit `--home`, then `HAMR_HOME`, then `AUDIOBOOK_HOME`, then an existing hamr state, then an existing legacy state, otherwise a fresh hamr state. No automatic data move or re-render occurs.

## Everyday commands

```sh
hamr plan 'My book.epub' --json
# Correct a new reference transcript before saving it:
hamr voice design narrator --transcript-file reference.txt --description 'A calm clear English narrator'
# Or supply a clean 2–30 second recording and its exact transcript:
hamr voice clone my-voice --reference clean.wav --transcript-file exact.txt
mkdir -p ~/Audiobooks
hamr open 'My book.epub' --voice narrator --output ~/Audiobooks
# A saved narrator is selected by default; a clean EPUB takes one confirmation.
# Headless: no prompts, explicit approved defaults:
hamr open 'My book.epub' --yes --voice narrator --output ~/Audiobooks --json
# Queue without rendering, then let an owned detached worker finish it:
hamr queue 'Project report.md' --mode procedure --output ~/Audiobooks --json
hamr worker --once --detach
hamr status --json
hamr show JOB --content
hamr preview JOB --utterance u00003 --play
hamr resume JOB
hamr play JOB
```

Listen mode preserves claims/qualifiers, speaks tables with explicit headers, inserts linked notes beside their claim, and keeps exact code in the companion. Study mode also speaks reference sections. Procedure mode speaks literal blocks. Explicit category choices override mode defaults. These deterministic modes do not summarize or infer content. Exact-heading section policies are available; sophisticated note placement and bibliography recognition beyond labeled sections remain limited.

A preview is stored beside the job snapshot and never placed in ready output. The preview command returns exact source locations. JSON diagnostics use exit 2 for failures, 3 for needs-review, 130 for interruption; normal success is 0. Source/script export requires `show --content`; ordinary diagnostics omit transcripts.

## Built-in character cast

All 45 reviewed character voices are included in the program. Listening samples play the stitched source references offline before model setup. Select a character for new books or use a per-book `cast:` voice ID.

```sh
hamr voice cast
hamr voice sample dale-gribble --play
hamr voice sample nigel-thornberry --play
hamr voice select nigel-thornberry
hamr queue 'My book.epub' --voice cast:nigel-thornberry --output ~/Audiobooks
```

`voice select` sets the narrator for new books. The `cast:` voice ID selects a preset for one new book or a scoped repair. Queued jobs pin their reference audio and transcript in app state for recovery across package updates.

To add a character, put its ID, display name, description, and language in `src/audiobook/cast.json`. Add its 24 kHz mono PCM `reference.wav` and a `voice.json` with `transcript` and `reference_sha256` under `src/audiobook/cast_assets/CHARACTER/`. A reference can contain 2–30 seconds of speech. Set `"sample_kind": "reference"` to play that reference when browsing. Each character contributes only its final `reference.wav` and transcript/metadata file to the wheel. Browsing plays that same reference. Review candidates, rejected clips, generated auditions and source archives stay outside the installed program.

## Unattended speech and verification

The standalone pipeline alternates bounded synthesis sessions with offline CPU ASR, checkpoints committed PCM and observations, runs complete required QA, and assembles only when source and speech gates pass:

```sh
hamr run JOB --asr-python ~/.local/share/audiobook-tools/asr/bin/python \
  --asr-model ~/.local/share/audiobook-tools/asr-base-en-model \
  --second-model ~/.local/share/audiobook-tools/asr-model --json
```

ASR assets must already be installed. The default is one new take per engine session. `--max-new-takes 1` limits one invocation; repeat the same command to continue. `--prepare-clear` prepares accounted speech while unresolved source regions remain publication holds. The optional second observer can corroborate critical-content discrepancies for bounded seed retakes: at most two per exact text/source requirement and eight across the job by default. No acceptance is added automatically. Nonverbal artifacts and unsupported source recovery remain research work; a matching transcript cannot clear a recorded listening finding.

Add `--resident --batch-takes 1 --batch-rest-seconds 10` to retain one owned model across batches, CPU recognition and generation rests. The model loads lazily when speech is actually missing. A batch of one bounds generation to one whole narration block at a time even when the backend supports more parallel requests. Larger batches reuse the same model and follow the saved parallel limit. All text, voice, seed, cache identities and source/speech gates stay pinned. Cancellation, failure or pipeline completion closes the owned engine; there is no automatic restart of a failed resident server.

`status JOB --json` reports the pipeline phase and cumulative retake use. After interruption or reboot, invoke `run` again with the same observer options. There is no automatic boot launch.

Source locations, planned speech and committed takes remain inspectable through `show --content`, `excerpt` and offline `qa`. Source review and speech review are separate publication gates.

Rules9/10 anchor ordinary complete paragraphs independently, preserve complete sentences within bounded input, and separate an unfinished tail from preceding complete sentences. Known source gaps hold unfinished sentences. Single complete sentences up to160 words/1000 UTF-8 bytes are an experimental extension; a136-word/793-byte actual control has passed source/ASR/player checks; longer inputs retain explicit clause/forced boundaries. Saved plans retain their rules until explicitly upgraded. Verified document policies can defer a photograph caption until its interrupted paragraph finishes, with a spoken “Illustration caption” cue, or give a synopsis a standalone boundary.

## Recovery and repair

```sh
hamr status JOB --json
hamr resume JOB
# Output mount unavailable/collision/corruption: preserve prior output and select a new parent:
mkdir -p ~/Audiobooks-recovered
hamr resume JOB --output ~/Audiobooks-recovered
# Only a pronunciation changes; unchanged speech/conditioning keys reuse their PCM:
hamr repair JOB --pronounce '2.4.10=two point four point ten'
hamr resume JOB
# Resolve one exact uncertain occurrence with a verified source reading:
hamr repair JOB --block BLOCK_ID --text-file verified.txt
hamr resume JOB
```

Ctrl-C, HUP and TERM stop owned work and retain verified clips. A single managed writer prevents rendering/GC races. Abrupt parent death terminates the owned Breeze child on Linux. The detached worker survives terminal close via a new session and private log, and continues unrelated queued jobs when one fails or needs review. A worker must be started again after logout/reboot; no boot/login service is installed. `pause`/`cancel` mark idle jobs; signal the owned foreground process to interrupt active work.

Every source is copied by bytes before import; changing the original path does not change queued content. Repairs create a new output revision and preserve prior publications. The source occurrence ID is retained; no automatic rebasing across changed source bytes is claimed. The current state schema is 16. Supported older state is migrated transactionally, and newer schemas are rejected. Existing jobs retain their saved narration rules, sources, voice and model pins.

PCM clips and receipts are promoted together. M4B time derives from 64-bit final sample counts. Assembly pipes bounded chunks to FFmpeg and encodes AAC once at explicit 24 kHz. All ready files, companion and report are promoted in one same-filesystem directory rename with no-replace semantics, checksums and a manifest. Recovery reconciles promotion before/after the DB ready checkpoint. Process-crash tests do not prove universal power-loss durability.

Ready output is `~/Audiobooks/JOB-rREV/`: `book.m4b`, `source.html`, `companion.json`, `companion.vtt`, `report.json`, `manifest.json`. The HTML contains exact lookup text and source locators; source identity points to the retained immutable snapshot in app state. Segment times are precise; word-level forced alignment is not implemented. AAC decode padding differs from source PCM; verified container offsets use PCM chapter boundaries with a 2 ms tolerance.

## PDF and OCR

Native PDF glyph rectangles are inventoried separately from raster regions. The importer tests a detected central gutter and uses spanning elements to split bands. Any raster-backed page is OCR'd as a visual competing observation, including selectable/hidden text. Numeric disagreement blocks that page for review while clear pages render. The current whole-page OCR route needs further complex-layout validation. Semantic tags, robust furniture suppression, charts and structured math speech remain pending.

Tesseract may be on PATH or extracted in user space under `~/.local/share/audiobook-tools/ocr`, with `usr/bin/tesseract`, `usr/lib` and `usr/share/tessdata`. `AUDIOBOOK_OCR_ROOT` overrides that root. Missing OCR is reported as an exact unresolved page and does not silently omit it. OCR assets are acquired separately from the program.

## Cleanup and uninstall

```sh
hamr cleanup --json                 # dry run
hamr cleanup --apply --json         # unreferenced cache and scratch only
hamr cleanup --purge-job JOB --json # inspect intentional source/edit removal
hamr cleanup --purge-job JOB --apply
```

Active, paused and retained jobs pin their verified speech. Saved voices, models, source snapshots and published results survive ordinary cleanup. Explicit job purge removes that job's durable data, then future cleanup may remove its unreferenced clips. Cleanup refuses symlinks; it never follows them or deletes outside app state. Published output is intentionally never removed by cleanup.

To uninstall the app, remove its `~/.local/bin/hamr` and `~/.local/bin/audiobook` links and the environment they point into (`hamr-app/venv`, or `audiobook-app/venv` on an upgraded installation). App data, voice originals, models and published output remain until intentionally removed. Keep state backed up before upgrades. Reinstalling the wheel leaves state unchanged.

## Development and qualification

```sh
uv sync --python 3.12 --extra test
uv run pytest -q
uv build --wheel
```

The distribution is `hamr`; the existing `audiobook` Python package is retained for compatible integrations and saved qualification launchers. The `hamr` module provides the canonical module entry point. Models, job formats and reference identities are independent of the product name.

Signal fixtures verify recovery and packaging without certifying narration. ASR comparisons can identify missing words and quantities; a matching transcript cannot rule out honking, clipped delivery or other audible artifacts. Listener findings remain publication holds until the exact current take is checked. Record an issue with:

```sh
hamr review JOB --flag u00003 --reason 'Clipped ending at 01:03:52' --json
# After checking the current take against its source and completing required QA:
hamr review JOB --accept u00003 --reason 'Checked the complete replacement passage' --json
```

Release archives contain the engine, final character references, installer, locked dependencies, public documentation and the release-content checker. Each character contributes one `reference.wav` and one transcript/metadata file. Review candidates, generated auditions, rejected clips, book files, job state, research logs and Git history are excluded. They remain in the development workspace.

Build and inspect the exact release artifacts:

```sh
uv build
python3 tools/check_release.py
```

The checker verifies archive membership, all reference hashes, transcript metadata and known private-path/key patterns. Create a public repository from the checked source archive with fresh Git history. The existing development workspace/history is not a publication source. See [release notes](../RELEASE.md) for measured checks and remaining qualifications.
