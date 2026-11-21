# Using hamr

## Make your first book

[Install hamr](../README.md#install-on-linux), then run these commands with a terminal. Replace the EPUB path with your file. PDF, Markdown and plain-text files work with the same command.

```sh
hamr setup
hamr voice sample nigel-thornberry --play
hamr open "$HOME/Downloads/Ravens in Winter.epub" --voice cast:nigel-thornberry
```

Accept the suggested profile during setup and enter `y` after checking the book plan. The second command is an optional listening sample. Once the narration passes its checks, hamr prints the job ID and M4B path. The folder under `~/Audiobooks` also contains the original source, a timed companion and a report. Use `hamr play JOB` to listen or `hamr resume JOB` to continue an interrupted book.

## Install and setup

The [installation commands](../README.md#install-on-linux) build and install the app for your account. Run `hamr --version` and `man hamr` to check discovery. The installer uses locked dependencies. A source checkout can also run `uv run hamr` from the project directory. The installer accepts `./install.sh APP_DIRECTORY COMMAND_DIRECTORY` for a separate installation. Set `HAMR_MAN_DIR` to choose its manual hierarchy, then use `man -M "$HAMR_MAN_DIR" hamr` if needed.

Use [the profile guide](../README.md#which-profile-should-i-choose) if you need a different model. Setup downloads the selected engine and two offline speech recognition models and checks that they load. Setup keeps verified partial downloads for retry. `--cache DIRECTORY` accepts verified engine files by filename and checker files in `base.en/` and `small.en/`. Run `hamr setup --checks-only` after upgrading an installation that already has a working engine. Use `--engine-only` when configuring speech generation for a separate manual QA workflow.

```sh
hamr doctor
hamr setup --profile vulkan-q8
hamr profiles --json
```

Backend profiles choose model precision and execution hardware. A saved listening profile chooses document reading rules. For example, save `hamr profile technical --mode study`, then apply it with `hamr plan book.epub --profile technical`.

Fresh installs use `~/.local/share/hamr` for jobs, models and cache. Prepend `--home PATH` before the command or set `HAMR_HOME` to use an isolated state directory on a local filesystem. Upgrades retain owned legacy state and pinned paths. Keep the installed environment fixed during rendering and upgrade at a stopped checkpoint.

To upgrade an existing checkout, run the following from that checkout.

```sh
git pull --ff-only
uv build --wheel
./install.sh
hamr --version
hamr setup --checks-only
```

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

Use `--notes after_paragraph|inline|companion` to control linked notes. `--chapters '5,2-3,2'` selects chapters 2, 3 and 5 in source order. Use `hamr repair JOB --chapters all` to restore the complete selection.

### Check how a document will be read

Run `hamr plan DOCUMENT --json` before speech setup to inspect the source decisions, planned utterances and review locations. The plan keeps the extracted `source` and final `spoken` text separately. New plans use the `faithful-14` rules.

- Markdown note definitions can contain several paragraphs or list items. A code span containing a note marker stays literal. Missing, duplicate and nested note targets need review when selected for speech.
- Plain `.txt` files accept UTF-8 or BOM-marked UTF-16/UTF-32. Blank lines separate paragraphs. The source locations retain exact decoded lines and line endings. Convert files from other encodings before opening them.
- EPUB can read the text of a book that uses the standard font-obfuscation method on declared fonts. Its inventory records the affected resources. Embedded media that needs an accessible reading appears in source review. [EPUB 3.3 font obfuscation](https://www.w3.org/TR/epub-33/#sec-obfuscation)
- PDF text objects declared invisible or clip-only can trigger visual OCR, even on pages without raster images. The plan retains conflicting observations for review. Private-use and unmapped characters in a selected reading also need review. Complex layouts and vector-only labels still need separate source checks.
- Supported explicit readings include `Room 007`, `Departure 08:05`, `0.080`, `mL` and `°C` or `°F` after a number. A bare `$` is read as a symbol without assigning a country. Numeric dates without a locale cue retain their written field order.

In a decision’s `normalization.edits`, the input and output ranges use half-open Unicode code-point offsets. Read input offsets against `source` when `input_basis` is `block_source`. When earlier processing changed the text, use the included `normalization.input`. Output offsets point into `spoken`. These mappings cover the new typed readings. Other transformations have their own provenance, such as note links, PDF locations and abbreviation evidence.

For an existing job, use `hamr repair JOB --upgrade-rules` to opt into the new spoken rules. Add `--reimport` to pick up importer fixes from the original snapshot. Examine the new review state with `hamr show JOB --content` before resuming.

### Tune readings that depend on context

Inspect `decisions[].context_events` in `hamr plan DOCUMENT --json`. Each event records its source span, possible readings, evidence and selected candidate. Defaults keep uncertain dates in their printed field order and continue the book. Choose `"mode": "strict"` in the settings file to hold unresolved choices for review. Existing source and speech checks apply in either mode.

Ordinary narration reads `$12.50` as “twelve dollars and fifty cents” and `3/4 cup` as “three quarters of a cup”. Set `"conventions": {"narration_style": "technical"}` when the spoken form should preserve printed currency symbols and decimal places. Either style keeps the exact source and separate reading evidence in the companion.

Start with explicit conventions. This file selects day/month dates where the context identifies a date, and negative parentheses for labeled financial values.

```json
{
  "conventions": {"date_order": "DM", "accounting_parentheses": true}
}
```

```sh
hamr plan report.txt --context context.json --json > plan.json
hamr profile technical --context context.json
hamr open report.txt --profile technical --voice cast:nigel-thornberry
hamr repair JOB --context context.json --expect-revision REVISION
```

The effective settings are in `.policy.context` in the plan. Edit a cue group's literal phrases to try a new context. A term in `cues.date_topic` can support a date when an adjacent word such as “on” governs the value. The `lexicon` maps lowercase dotted tokens to cue-based readings. The `initialisms` map selects `word` or `letters` for uppercase tokens. Evidence weights and `minimum_margin` are heuristic ranking settings. Check a change against a similar phrase that needs a different reading.

A document policy bound to the file's SHA-256 can set `context.conventions` for the book, `context.chapters` for exact chapter names or occurrence IDs, and `context.tables` for exact table block IDs. Find those IDs in `hamr plan SOURCE --json`. Hamr can also recognize a date-order declaration within the same quotation and apply it to that date. A date can be recognized while month and day stay unresolved. The plan exposes that distinction, scoped evidence, and the selected reading.

For technical books, set `number_locale` in the applicable conventions when you know the notation. Supported values are `en-US`, `en-GB`, `de-DE`, and `fr-FR`. Without a setting, unambiguous measurements can establish a decimal separator for other measurements in the same source section. The plan records the exact passages that support the reading. Printed thin spaces and nonbreaking spaces can join groups of digits inside a number.

Use a scoped `notation` setting for sources with plain-text symbols whose meaning is already known. Supported values include `ASCII_subscript`, `ASCII_power`, `propositional_implication`, and `chess`. A table convention `ditto_marker: "double_quote"` lets a bare quote inherit the verified value immediately above it. `emoji_speech: "localized_name"` names supported single pictographs. `expand_contractions` controls spoken expansion in recognized grammatical constructions.

An optional top-level `native_names` list records particular names whose reading is being checked. The narrator receives the full printed name. A supplied pronunciation alias takes precedence.

The contextual reader gives whole forms such as rates, ranges, dimensions, and measurements priority over their component numbers. It still draws on the established EPUB handling for authored links, literal text, and publisher pronunciation. For words such as “read” or “record”, a recorded contextual interpretation may rely on Breeze for pronunciation. Hearing and acoustic evaluation are required to verify the resulting stress or vowel.

An EPUB expression split by inline markup can have multiple `source_leaves` in the plan. Each part records its parsed XHTML text node and half-open Unicode code point range. In a simple table, a unique authored `headers` reference supplies the column label and its source ID. A ditto cell follows the preceding value within the same authored column, and a chain retains the original value cell as well as its immediate antecedent.

In an instruction to copy `"$222"` exactly, Hamr reads the dollar sign and digits as characters. The same amount in ordinary prose reads as money. Recognized vocal markers such as `[laughs]` and `(clears throat)` are read as text. Their original spelling and punctuation remain in the companion.

Normal narration reads `$100` as “one hundred dollars,” `£100` as “one hundred pounds,” and `JPY 100` as “one hundred yen.” The plan keeps the currency code when it is known. Technical narration can speak the full currency label.

English narration passes through mixed-language text such as scientific names and short quotations. EPUB language tags remain in the source and help match a publisher’s pronunciation lexicon. Speech QA still checks the rendered audio, and an unresolved publisher pronunciation instruction can be repaired for its exact occurrence.

HAMR reads a complete EPUB ruby annotation written in kana for its ideographic base once. In other ruby markup, it reads the base and any explicitly authored definition. The companion retains the original markup. The default narration and speech checks are qualified for English.

A duration label such as `Elapsed time (mm:ss)` supplies the units for `03:20`. Normal narration says the duration in minutes and seconds once. When a source omits the unit order, normal narration says “three twenty.” The plan keeps both interpretations, and technical narration says the printed components and colon. Set `duration_fields` in a context convention to apply a known order to a document, chapter, or table.

Hamr keeps “et al.” in ordinary narration. Set `expand_citations: true` in the context conventions to read it as “and others.” In explicit command examples, Hamr can voice the dollar sign in a shell variable and the double dash in a long option. The plan and companion keep the command as written.

In a measurement context, `1.234(5) m` is read as a length with an uncertainty of five in the final digits. The plan retains the unit, coefficient precision, and exact uncertainty digits.

For matched ruled PDF tables, the original cell coordinates and glyph ranges appear in the plan. A scientific table with `Quantity`, `Symbol`, `Numerical value`, and `Unit` headers can supply exact grouped digits, raised exponents, and compound units. An authored declaration can supply the meaning of parenthesized uncertainty digits. A mathematical mark or ambiguous row appears with its own source location for inspection.

The PDF reading can include positioned formulas and independent equations under a common heading. When a PDF's font encoding and text layer disagree on a letter, a document policy can supply `pdf_glyph_aliases`. Map the unresolved row's block ID to a list of verified entries. Each entry has `column` and `offset` as zero-based indices into an original cell's text, `source` as the exact original letter, `reading` as the verified mathematical letter, a `reason`, and `verified: true`. The policy uses the original file's `source_sha256`. The companion keeps the original PDF and its extracted letter, and the audit records the derived reading.

Hamr exports ambiguous candidate requests in `.contextual_resolution.review_requests`. Pass a local JSON array of replies with `--context-review FILE`, or explicitly opt into a local service with `--context-review-endpoint http://127.0.0.1:PORT/review`. The service receives a POST containing a versioned `{"schema_version":1,"requests":[...]}` batch of unresolved candidates and nearby source snippets. It returns the same reply array as the file interface. A selection includes the request's `source_digest` and `occurrence_id`, `action` set to `select`, a listed `candidate_id`, listed `evidence_ids`, and an empty `requested_context` array. An endpoint can ask for `neighbors` or `chapter` using `action: "request_context"` and a null candidate. Hamr sends at most one follow-up, with an `additional_context` field containing available excerpts from the same source scope. The same candidate and evidence IDs apply. Invalid, stale, or unavailable replies leave the automatic reading in place. Settings and accepted replies are saved with the job. Resuming uses those choices without contacting the endpoint again.

A preview is stored beside the job snapshot and never placed in ready output. The preview command returns exact source locations. JSON diagnostics use exit 2 for failures, 3 for needs-review, 130 for interruption; normal success is 0. Source/script export requires `show --content`; ordinary diagnostics omit transcripts.

## Built-in character cast

All 45 reviewed character voices are included in the program. Listening samples play the same approved introductions as the cast guide, offline before model setup. Select a character for new books or use a per-book `cast:` voice ID.

```sh
hamr voice cast
hamr voice sample dale-gribble --play
hamr voice sample nigel-thornberry --play
hamr voice select nigel-thornberry
hamr queue 'My book.epub' --voice cast:nigel-thornberry --output ~/Audiobooks
```

`voice select` sets the narrator for new books. The `cast:` voice ID selects a preset for one new book or a scoped repair. Queued jobs pin their reference audio and transcript in app state for recovery across package updates.

To add a character, put its ID, display name, description, and language in `src/audiobook/cast.json`. Add its 24 kHz mono PCM `reference.wav` and a `voice.json` with `transcript` and `reference_sha256` under `src/audiobook/cast_assets/CHARACTER/`. A reference can contain 2–30 seconds of speech. Add the approved introduction under `docs/listen/CHARACTER.mp3` and its exact text, duration and SHA-256 to `docs/listen/manifest.json`. The wheel bundles that same audio for offline previews. The cast catalogue uses `"preview_kind": "introduction"`; voice conditioning continues to use the original reference and transcript. Review candidates, rejected clips and source archives stay outside the installed program.

## Unattended speech and verification

The ordinary book workflow already renders, checks speech and assembles the book. Use `hamr run JOB` when you want control over batch sizes, pauses or the choice of checker. For example, to use a separate offline speech recognition environment:

```sh
hamr run JOB --asr-python ~/.local/share/audiobook-tools/asr/bin/python \
  --asr-model ~/.local/share/audiobook-tools/asr-base-en-model \
  --second-model ~/.local/share/audiobook-tools/asr-model --json
```

The explicit Python and model paths must refer to an installed local checker. The low-level `run` default is one new take per engine session. `--max-new-takes 1` limits one invocation; repeat the same command to continue. `--prepare-clear` prepares accounted speech while unresolved source regions remain publication holds. The optional second observer can corroborate critical-content discrepancies for bounded seed retakes: at most two per exact text/source requirement and eight across the job by default. No acceptance is added automatically. Nonverbal artifacts and unsupported source recovery remain research work; a matching transcript cannot clear a recorded listening finding.

Add `--resident --batch-takes 1 --batch-rest-seconds 10` to retain one owned model across batches, CPU recognition and generation rests. The model loads lazily when speech is actually missing. A batch of one bounds generation to one whole narration block at a time even when the backend supports more parallel requests. Larger batches reuse the same model and follow the saved parallel limit. All text, voice, seed, cache identities and source/speech gates stay pinned. Cancellation, failure or pipeline completion closes the owned engine; there is no automatic restart of a failed resident server.

`status JOB --json` reports the pipeline phase and cumulative retake use. After interruption or reboot, invoke `run` again with the same observer options. There is no automatic boot launch.

Source locations, planned speech and committed takes remain inspectable through `show --content`, `excerpt` and offline `qa`. Source review and speech review are separate publication gates.

Rules9/10 anchor ordinary complete paragraphs independently, preserve complete sentences within bounded input, and separate an unfinished tail from preceding complete sentences. Known source gaps hold unfinished sentences. Single complete sentences up to160 words/1000 UTF-8 bytes are an experimental extension; a136-word/793-byte actual control has passed source/ASR/player checks; longer inputs retain explicit clause/forced boundaries. Saved plans retain their rules until explicitly upgraded. Verified document policies can defer a photograph caption until its interrupted paragraph finishes, with a spoken “Illustration caption” cue, or give a synopsis a standalone boundary.

## Recovery and repair

When publication is on hold, `hamr status JOB` shows the planned M4B path and the next step. **Speech check pending** means saved narration still needs the automatic check. Run `hamr resume JOB` to check it and finish the book. If the checker is missing, run `hamr setup --checks-only` first. **Listening review needed** means Hamr flagged specific recordings. The status includes a command to export the first flagged passage, and `hamr status JOB --json` lists the reasons and report paths. **Source review needed** means a passage in the original document must be resolved before publication.

For a standalone report, run `hamr qa JOB --all --require-review` with the checker installed by setup. The report records the check, and `hamr resume JOB` writes the book once all holds are cleared. Use the custom Python and model options only when supplying your own local checker.

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

To uninstall the app, remove the owned `hamr.1` and `audiobook.1` links under `~/.local/share/man/man1` (or the custom manual directory), its `~/.local/bin/hamr` and `~/.local/bin/audiobook` links and the environment they point into (`hamr-app/venv`, or `audiobook-app/venv` on an upgraded installation). Your app data, models and published books live separately from these installed files. Keep state backed up before upgrades. Reinstalling the wheel leaves state unchanged.

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

Release archives include the program, voice references, approved listening samples, Linux manual and required installation inputs. The [release notes](../RELEASE.md) record what has been qualified.

```sh
uv build
python3 tools/check_release.py
```

The checker validates archive membership and asset identities. Keep private book and job material in app state.

For shell completion, load the output of `hamr completion bash`, `hamr completion zsh` or `hamr completion fish` using your shell's usual completion setup. For a one-session Bash example, use `source <(hamr completion bash)`.
