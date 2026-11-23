# Using hamr

## Make your first book

[Install hamr](../README.md#install-on-linux), then run these commands with a terminal. Replace the EPUB path with your file. PDF, Markdown and plain-text files work with the same command.

```sh
hamr setup
hamr voice sample nigel-thornberry --play
hamr open "$HOME/Downloads/Ravens in Winter.epub" --voice cast:nigel-thornberry
```

Accept the suggested profile during setup and enter `y` after checking the book plan. The second command is an optional listening sample. Once the narration passes its checks, hamr prints the job ID and M4B path. The folder under `~/Audiobooks` also contains the original source, a timed companion and a report. Use `hamr play JOB` to listen or `hamr resume JOB` to continue an interrupted book.

## What runs automatically

| Feature | Where to see it |
| --- | --- |
| **Tone and performance** | Ordinary listening follows speaker and scene cues, including whispers, restraint, urgency, distress, and authored sounds. Works in EPUB, PDF, Markdown, and text. [Expressive narration](#expressive-narration) |
| **Contextual readings** | Nearby words and document structure guide the reading of dates, abbreviations, amounts, and similar text. `hamr plan DOCUMENT --json` shows the selection and its evidence. [Contextual readings](#tune-readings-that-depend-on-context) |
| **Scanned and mixed PDF** | With the English OCR assets installed, `hamr open` reads scanned pages and supported scan regions. `hamr doctor` reports OCR readiness. [PDF and OCR](#pdf-and-ocr) |
| **Offline speech checks** | `hamr open` checks speech before publishing. `hamr status JOB --json` reports which verifier ran and whether the check passed. [Speech verifier presets](#speech-verifier-presets) |

## Install and setup

The [installation commands](../README.md#install-on-linux) build and install the app for your account. Run `hamr --version` and `man hamr` to check discovery. The installer uses locked dependencies. A source checkout can also run `uv run hamr` from the project directory. The installer accepts `./install.sh APP_DIRECTORY COMMAND_DIRECTORY` for a separate installation. Set `HAMR_MAN_DIR` to choose its manual hierarchy, then use `man -M "$HAMR_MAN_DIR" hamr` if needed.

Use [the profile guide](../README.md#which-profile-should-i-choose) if you need a different speech engine. Setup selects the offline speech verifier separately. It keeps verified partial downloads for retry. `--cache DIRECTORY` accepts verified engine files by filename and checker files in `base.en/`, `small.en/`, and `fluid-int8/`. Run `hamr setup --checks-only` after upgrading an installation that already has a working engine. Use `--engine-only` when configuring speech generation for a separate manual QA workflow.

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

If you use the checkout directly, `git pull --ff-only` followed by `uv run hamr setup --checks-only` updates the checkout and installs its speech checker. Repeat your original book command afterward. Keep the same `--home` before the subcommand if you use custom state. The recovery command Hamr prints includes the exact state directory and keeps checkout commands in their checkout.

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

Run `hamr plan DOCUMENT --json` before speech setup to inspect the source decisions, planned utterances and review locations. The plan keeps the extracted `source` and final `spoken` text separately. New plans use the `faithful-17` rules. When a plain paragraph needs two requests, Hamr can move a complete sentence across the split so the final sentence keeps some preceding context.

- Markdown note definitions can contain several paragraphs or list items. A code span containing a note marker stays literal. Missing, duplicate and nested note targets need review when selected for speech.
- Plain `.txt` files accept UTF-8 or BOM-marked UTF-16/UTF-32. Blank lines separate paragraphs. The source locations retain exact decoded lines and line endings. Convert files from other encodings before opening them.
- EPUB can read the text of a book that uses the standard font-obfuscation method on declared fonts. Its inventory records the affected resources. Embedded media that needs an accessible reading appears in source review. [EPUB 3.3 font obfuscation](https://www.w3.org/TR/epub-33/#sec-obfuscation)
- PDF text objects declared invisible or clip-only can trigger visual OCR, even on pages without raster images. The plan retains conflicting observations for review. Private-use and unmapped characters in a selected reading also need review. Complex layouts and vector-only labels still need separate source checks.
- Supported explicit readings include `Room 007`, `Departure 08:05`, `0.080`, `mL` and `°C` or `°F` after a number. A bare `$` is read as a symbol without assigning a country. Numeric dates without a locale cue retain their written field order.

In a decision’s `normalization.edits`, the input and output ranges use half-open Unicode code-point offsets. Read input offsets against `source` when `input_basis` is `block_source`. When earlier processing changed the text, use the included `normalization.input`. Output offsets point into `spoken`. These mappings cover the new typed readings. Other transformations have their own provenance, such as note links, PDF locations and abbreviation evidence.

For an existing job, use `hamr repair JOB --upgrade-rules` to opt into the new spoken rules. Add `--reimport` to pick up importer fixes from the original snapshot. Examine the new review state with `hamr show JOB --content` before resuming.

<a name="audition-dialogue-delivery"></a>

### Expressive narration

| What Hamr can direct | What triggers it |
| --- | --- |
| Speaking softly, muttering, whispering, shouting, or using deliberate pacing | An attributed reporting phrase such as “she answered quietly,” “he muttered,” or “Ada said slowly.” Nearby descriptions can also establish how the speaker delivered the line. |
| Surprise, reassurance, irony, amusement, affection, or distress | Clear attribution or local speaker evidence. An identifiable speaker change or conflicting evidence limits carryover. |
| Growing urgency, fading delivery, or emphasis on a specific phrase | An authored change over multiple sentences, or a unique quoted phrase the author explicitly singles out. |
| Sighs, chuckles, sobs and other nonverbal sounds | An explicit marker associated with a dialogue turn. |
| Heading cadence and technical articulation | Heading structure, or a complete table or equation in study or procedure mode. |

An attribution inside a quoted sentence can direct both parts of the same turn. In “We will,” she whispered, “stay until dawn,” the quoted words share the whisper and the words around and between them stay in the narrator voice. Hamr can send the entire paragraph in one request when the surrounding sentences and the quoted turn fit.

Hamr can give Breeze a delivery instruction for clearly attributed dialogue and for prose with a clear local cue. “She lowered her voice” can soften the same speaker’s next line, as can “he said, in a hushed tone.” An explicit phrase such as “she lowered her voice to a whisper” asks for a whisper. Two details can support a direction together, as when a character leans closer and their words become barely audible. Competing directions leave the reading plain. Additional delivery controls recognize reporting verbs before the speaker as well, as in “whispered Alice.” A quiet scene requires corroborating details, such as a silent room and a character holding their breath. The plan records the source spans and delivery cues behind a decision. Hamr requires an explicit authored marker to perform a sound.

Authored sounds include sighs, laughter, chuckles, gasps, coughs, throat clearing, groans, sniffles, lip smacks, hiccups, sneezes, and sobs. Hamr attaches a sound to an identifiable dialogue turn. A written laugh at the start of an eligible line calls for a short chuckle. Clearly attributed dialogue can also carry delivery such as whispering, urgency, restraint, irony, or distress. These cues apply in listening mode to EPUB, searchable and scanned PDF, Markdown, and plain text. Scanned pages first need local English OCR. Hamr keeps each affected utterance intact and preserves the original wording in the companion.

**Expressive delivery** is on in normal listening mode. On the built-in Breeze profiles, Hamr can direct clearly attributed dialogue, let a named speaker carry a supported tone into the next turn, and shape a gentle entrance after a whisper. It applies the instruction to the complete passage, keeping the narrator's attribution and the words on both sides of an interruption. It reads conflicting and uncertain cues plainly. PDF line breaks can split a cue across source fragments. The plan records the original PDF blocks and spans for those cues.

With a compatible frame-guidance backend, Hamr can also direct an explicitly whispered quotation inside a longer passage. When a second quote continues the same turn, a backend with multiple guidance windows can keep the reporting words between the quotes in the narrator's voice. Hamr measures the first reading with the installed offline speech checkers and verifies the timing on the directed take. If timing adjustments cycle and one quotation ends just past its scheduled interval, Hamr can try one short taper that fits before the reporting words. If two checkers agree on the words but disagree slightly at the first measured exit, Hamr can measure one alternate reading before trying direction. The resulting take must pass the independent timing checks. The exact source spelling of a known abbreviation can help a timing checker align the passage. Speech QA receives the original transcript, including any added words. Uncertain timing uses the ordinary reading. The entire passage stays in one request for each take. Completed trials survive interruption, and the finished book's `report.json` records the decision under `quote_timing`.

The timing check can recognize spellings the book compiler already expanded, such as “Dr.” and “Doctor” at the same place in the passage. When the two timing checks disagree, an installed Fluid checker can corroborate the complete wording and the location of the disputed quote. The Whisper profile uses the existing pair of lower-memory checks.

“Wait for the lantern,” she whispered. “The latch is still closed.” When the same turn continues in a second complete quotation within the paragraph, Hamr can keep both lines in one Breeze request. The narrator still reads the attribution between them. The plan marks both quotation spans under `continued_quotation`.

On a searchable PDF these quotations can sit on separate printed lines. Hamr can keep adjacent aligned lines together for speech. A shared authored paragraph tag lets the delivery carry across both quotations. With only line geometry, Breeze reads the combined passage in its usual voice. The companion retains both source locations. Use `hamr repair JOB --reimport --performance auto` to check an older PDF for paragraph tags.

Phrases such as “she answered quietly” and “he said slowly” can shape the quoted line even when a short sentence of narration shares the paragraph. A description that clearly refers back to the speaker's voice can also guide the line just spoken. Hamr asks Breeze to read the surrounding narration in its usual voice.

Reporting phrases also distinguish firm emphasis and calm delivery. “Ada replied firmly” and “said Ada firmly” describe the same delivery. “Answered quickly” can refer to the timing of a reply, so Hamr leaves its pace to Breeze. The plan shows which words supported each direction.

The same rules recognize locally attached speaker descriptions, such as “whispered the night watchman,” “the doctor replied firmly,” and “her brother answered quietly.” Those descriptions identify the turn being directed.

Voice descriptions attached to the reporting clause can also direct one quotation. “She said, her voice barely audible” asks for softer speech when the possessive refers to the same speaker. An introduction such as “She said, in a whisper:” directs the quotation that follows. Hamr records the reporting verb, voice description and quoted span together.

A written sigh or cough can also fall between two complete sentences in one attributed quotation. For example, in “The last box is empty. [sigh] At least we found the key,” she said, Hamr sends one continuous speech request and asks Breeze to place the sigh at the marked sentence break.

If the same quotation is whispered, Hamr can keep the whisper and the authored sound in that request. It can also carry one unambiguous author-marked stress. The plan records the attribution, the marked sound, and the emphasized word separately. The sound marker tells Breeze where to act and is omitted from the expected spoken words.

An attribution can specify both delivery and emotion. “She whispered eagerly” asks for a whisper with a little anticipation, and “she whispered shyly” asks for a hesitant warmth. A direct lead-in such as `Mara whispered: “Wait by the door.”` scopes the direction to the quotation. If the attribution interrupts one continuous turn, both quoted parts share the delivery and the attribution uses the usual narrator voice.

Authored sounds are available in the ordinary workflow. Listening evidence includes leading sighs for Nigel Thornberry and Cave Johnson on Linux CPU Q8, trailing sighs on Linux Vulkan Q8, and Nigel's opening chuckle on Vulkan Q8 and F16. The plan records the direction that ran. Delivery varies with the voice, engine and passage.

Save plain delivery for new books with `hamr settings --performance off`. Add `--mode study` or `--mode procedure` for a technical reading policy. Use `hamr settings --mode listen --performance auto` to restore expressive listening. A per-book flag or an explicitly selected listening profile can override the saved preference. Existing jobs keep their delivery until you choose a revision. Use `hamr repair JOB --performance auto` to apply the current expressive rules to an existing job.

Hamr can give a heading a measured cadence and use clear articulation for a complete table or equation in study or procedure mode. Cave Johnson’s tearful direction asks Breeze to retain his usual register and timbre.

When the author names a phrase to stress, Hamr requires a single matching occurrence inside the quotation. Rising and fading delivery across two or more sentences follow an explicit attribution. Inferred distress requires local speaker evidence. Use the ordinary command to make the audiobook.

An author-marked personal pronoun can guide the stress in a complete spoken turn. For example, “Did *you* see who left?” asks Breeze to stress who saw it. Hamr uses semantic emphasis or an authored italic style in EPUB and emphasis markup in Markdown. The plan ties the marked word to its original source span. Use `hamr repair JOB --reimport --performance auto` to refresh stylesheet evidence on an older EPUB job.

```sh
hamr open 'Scene.pdf' --voice cast:nigel-thornberry
```

To inspect the choices or preview a single passage first, use `plan` and `queue`.

```sh
hamr plan 'Scene.pdf' --json
hamr queue 'Scene.pdf' --voice cast:nigel-thornberry --json
hamr preview JOB --utterance u00003 --play
hamr resume JOB
```

The plan lists a candidate's source block, intended words and Breeze request. Use the actual utterance ID shown by `hamr show JOB --content`. To change editions on a saved job, run `hamr repair JOB --performance off` or select another performance mode. A source-bound document policy can suppress specific occurrences with `"performance": {"disabled_blocks": ["BLOCK_ID"]}`.

Set `"performance": {"contextual_dialogue": "explicit"}` in the document policy to require a decisive cue for inferred dialogue. The default `corroborated` also takes two independent, compatible cues. Use `off` for dialogue with explicit attributions only. Direct attributions and authored sounds still follow the selected `--performance` mode.

For a book's own reporting phrases, add exact lowercase phrases to its source-bound policy. This example gives an even, measured delivery to quotes attributed with “replied carefully.” Run `hamr open BOOK --document-policy POLICY.json` as usual. `hamr agent capabilities` lists the available dialogue recipe names under `expressive_narration.dialogue_recipes`.

```json
{
  "source_sha256": "REPLACE_WITH_SOURCE_SHA256",
  "performance": {"attribution_cues": {"replied carefully": "measured"}}
}
```

To compare delivery before changing a book, save named settings in `takes.json` and preview one of its saved passages.

```json
{
  "Saved reading": {},
  "Plain control": {"instruction": null, "markers": false},
  "Quieter reading": {"instruction": "Read the passage softly, with clear consonants."},
  "Another seed": {"seed": 43}
}
```

```sh
hamr preview JOB --utterance u00003 --compare takes.json --play
```

All variants start from the same bound passage, voice and engine. An empty object uses the saved take's settings. `instruction` replaces Breeze's text direction. Use `null` to clear it and `"markers": false` to remove optional vocal cues from the synthesis text while keeping the planned words. You can also override a signed 32-bit `seed`, `cfg_scale`, or a sampling setting from the list below. Each variant inherits any settings it omits. The command returns the WAV paths and a local manifest with the original source locations, the exact requests and audio hashes. Use `--json` to read the result in a script. Rerun the same command after an interruption to reuse completed takes that pass a hash check. Comparisons leave the job, render cache, and published book intact.

To keep an auditioned instruction on one saved passage, copy the source block ID and expected spoken text from `hamr show JOB --content`. Add the chosen instruction to the source-bound policy.

```json
{
  "source_sha256": "SOURCE_FILE_SHA256",
  "performance": {
    "passage_instructions": {
      "SOURCE_BLOCK_ID": {
        "text": "The river was quiet.",
        "instruction": "Read this line softly with a clear, natural ending."
      }
    }
  }
}
```

Run `hamr repair JOB --document-policy POLICY.json`, then `hamr resume JOB`. The source block and exact text must identify one rendered passage. The instruction is sent separately from those words. Other passages keep their existing directions. Plain mode leaves passage instructions inactive.

To keep the audition's guidance or sampling for that passage, add a `"tuning"` object beside `"text"` and `"instruction"`, such as `"tuning": {"cfg_scale": 2, "top_k": 48}`. Its settings override those from the recipe for that passage. Omitted settings still come from the recipe or engine.

With a configured frame-guidance backend, a passage instruction or a named preview take can include `"frame_guidance": {"start_frame": 58, "end_frame": 75, "scale": 1.5, "release_frames": 2}`. Frames are zero-indexed, the end is exclusive, and each frame represents 80 ms on protocol 1. The release interpolates back to `base_scale`, which defaults to zero. Use a schedule timed to the passage's own spoken audio. Hamr checks what the backend confirms and records how many frames were actually generated. Use `"frame_guidance": null` in a preview variant to hear the passage with ordinary guidance.

For a backend that advertises `frame_guidance_windows`, `frame_guidance` can contain a `windows` array and an optional shared `base_scale`. Use the start, end, scale and release fields in each window. Provide the intervals in time order, with room for each release to finish before the next window. Hamr verifies completion of a fixed interval and its release. The final window may use `"end_frame": "speech"` with zero release when the instruction should stay active through the last spoken words.

On a runtime that advertises `narration_conditioning`, add `"narration_instruction": "Read in the narrator's natural, fully voiced audiobook voice."` beside the passage instruction and its frame schedule. Use a clone voice and zero `base_scale`. Breeze uses the narration instruction at zero guidance and increases the influence of the passage instruction inside each scheduled interval. A named preview can replace the narration instruction or set it to `null`. Compare the whole passage, including the words on each side of the interval, since changing the delivery can move the timing.

To apply recipe-specific guidance or sampling to a book, put the settings in its source-bound policy and use `--document-policy POLICY.json`. Omitted controls use the pinned engine defaults. Custom guidance above 1 requires serial synthesis. Hamr keeps these fields in the exact request receipt and reuses recordings whose requests are unchanged.

```json
{
  "source_sha256": "SOURCE_FILE_SHA256",
  "performance": {
    "tuning": {
      "contrastive_emphasis": {"cfg_scale": 1, "temperature": 0.7, "top_k": 48}
    }
  }
}
```

Set `source_sha256` to the file hash from `hamr plan`. Guidance accepts 1, 2, 2.5, or 3. Sampling ranges are 0.1–2 for `temperature`, 1–256 for `top_k`, 0.05–1 for `top_p`, and 0.8–1.4 for `repetition_penalty`. The controls change how Breeze generates a take. They can affect word fidelity and performance, so listen to matched takes before applying a tuned policy to a whole book.

Hamr keeps a directed take when speech recognition flags it. A possible wording error can hold publication for listening review. When the surrounding words match, a short interjection or recognized cue label at the position of an authored sound is recorded as uncertain. A recognizer can transcribe an actual chuckle as “mm-hmm.” The raw transcript stays in the QA report.

Compare two unprompted readings of the same committed take with `hamr transcribe JOB --utterance u00003 --json`. The report gives the PCM hash, native transcripts, word disagreements, and any intervals of exact digital silence lasting at least a quarter second. You can also run `hamr transcribe clip.wav --json` on a local excerpt up to 125 seconds. Neither command changes the take, job, or publication review. Use the actual audio to decide questions of performance, pronunciation, or sound quality.

### Speech verifier presets

`hamr setup` and `hamr setup --checks-only` select the verifier automatically. `hamr doctor` shows the active verifier, recommendation, and the reason for it. Set `--asr-profile` on either setup command to make an explicit choice.

| Preset | Hardware and download | How Hamr uses it |
| --- | --- | --- |
| `auto` | Checks available system RAM, platform, and remaining download space | Chooses the strongest qualified fit and checks its installed runtime before activation. |
| `fluid-int8` | Qualified on Linux x86-64 CPU. Requires 2.75 GiB available RAM for whole takes. The model adds about 801 MB to the baseline downloads. | Reads every take for publication. Whisper can corroborate a critical fault before an ordinary take is retried. |
| `whisper` | Lower-memory CPU preset and the baseline for Windows x64. Model files total about 634 MB. | Uses the faster-whisper pair. Choose it explicitly when limiting setup size or memory. |

The verifier uses system RAM and runs in a separate process. When memory is tight during synthesis, Hamr defers the heavier check until the synthesis process releases its resources. Each recognizer exits before the next starts. Fluid keeps the raw words, including fillers and repeats, and reports its segment timing as unavailable. Hamr keeps directed takes for review when a recognizer flags them. [Fluid Speech Pro ONNX model and license](https://huggingface.co/FluidVoice-Labs/fluid-speech-pro-onnx)

When a wording check holds ordinary narration and both checkers identify a replacement at the same source word, Hamr can retake that passage automatically. The replacement goes through the normal publication checks, and the disputed original stays available for comparison.

Fluid INT8 and Whisper are the installed verifier choices. The local comparison also ran Nemotron Streaming Q8 and Fluid FP32. Nemotron lost a softly spoken quote that Fluid recovered, and Fluid FP32 matched INT8's results on the controls with higher memory use. Those comparison models have no Hamr setup preset. For a finished book, `hamr status JOB --json` reports the active observer and `metrics.semantic_qa` for the saved job.

### Tune readings that depend on context

Contextual reading is part of normal English narration. It uses surrounding wording and the document’s structure to resolve a reading. Use the settings below when a book has its own terminology or conventions.

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

### Prepare a voice reference

Choose 2–30 seconds of one speaker with whole words at both ends. Write the exact transcript, including repetitions and intended sounds as appropriate to the recording. Hamr saves the original bytes and the final 24 kHz mono reference once. Subsequent books reuse the reference.

```sh
hamr setup --wash-only
hamr voice clone my-narrator --reference recording.wav --transcript-file exact.txt
```

Setup downloads the selected worker and model. Automatic preparation uses DPDFNet HR for detected steady electrical hum when that profile fits and the local speech checkers can verify the result. Clear recordings and inconclusive results use the original reading. The import result gives the decision and path to the preparation receipt. Use `--wash off` for a direct import, `--wash PROFILE` to request an installed treatment explicitly, or `--channel 1` to choose a clean source channel before conversion. Use `hamr setup --checks-only` to add the offline speech checkers if they are missing.

| Linux x86-64 CPU profile | Model download | Measured peak worker RAM | Use |
| --- | ---: | ---: | --- |
| `dpdfnet-hr` | 10.5 MB | about 181 MiB | Steady-hum auto route and explicit preparation. |
| `fastenhancer-tiny` | 151 KB | about 102 MiB | Lighter explicit comparison when the recording needs cleanup. |

Worker dependencies are an additional download. Hamr runs the local speech checkers after the treatment and releases the preparation worker before Breeze starts. `hamr doctor` explains which profiles fit. Keep the original or choose a better passage when music, overlapping voices, or damaged words make the speaker uncertain.

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

The default planner keeps complete paragraphs and sentences together within its input budget. It separates an unfinished tail from preceding complete sentences and holds unfinished sentences at known source gaps. A single complete sentence can use up to 160 words and 1,000 UTF-8 bytes. A 136-word control passed speech and playback checks. Longer inputs use recorded clause or forced boundaries. Saved plans retain their rules until explicitly upgraded. Verified document policies can defer a photograph caption until its interrupted paragraph finishes, with a spoken “Illustration caption” cue, or give a synopsis a standalone boundary.

## Audio mastering

New books use natural mastering automatically. After speech verification, Hamr measures the book and applies gain on the CPU with FFmpeg. Intentional changes in delivery keep their relative levels. The original takes stay available for recovery and revision.

For an isolated level jump, Hamr compares the take to steady neutral narration on both sides within the same chapter and scene. When the active speech and peak measurements agree, it can correct up to 3 dB. It counts quiet margins already present at ordinary narration joins toward the planned pause, up to 120 milliseconds or half the inserted pause. A fade of eight samples can smooth an isolated discontinuity on the very edge of a take. Dialogue, expressive cues, authored sounds, and scene and chapter boundaries are excluded from these joins.

The target is −19 LUFS for the mono audiobook. Hamr limits book-wide boost to 6 dB and checks the decoded M4B against a −2 dBTP ceiling. On a peak overshoot it lowers gain and encodes again from the saved recordings. Peaks or very short material can keep a book below target. `report.json` records level findings, corrections, and the decoded measurements. When a join is adjusted, the timed companion records the planned and actual inserted pauses. Chapter and subtitle times follow the finished sample sequence. Every export checks the decoded audio length against that sample sequence, independently of the chapter markers. If the check fails, Hamr retains the verified speech and prints the exact resume command.

Use `--mastering off` on `open` or `queue` to keep the assembly's original playback level. Saved jobs retain the mastering recipe they were created with. An explicit remaster uses the current recipe. To master an existing book from its checked takes:

```sh
hamr repair JOB --mastering natural
hamr resume JOB --no-synthesis
```

Hamr makes a new edition and keeps the previous one playable. When a saved take is missing or needs speech checking, `hamr resume JOB` uses the ordinary recovery workflow. Use `hamr repair JOB --mastering off` to make an edition at the original playback level. A metadata-only revision copies the existing audio packets.

Cast introductions are balanced separately for browsing. They are the clips you hear from the cast guide and `hamr voice sample CHARACTER --play`.

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

For a foreground queue pass, `hamr worker --once --json` lists the jobs processed and their outcomes. Exit code 2 means a job failed. Exit code 3 means a job needs review and none failed. Jobs blocked before rendering retain a reason and a recovery command in `hamr status JOB`. After fixing the cause, run `hamr resume JOB`.

Every source is copied by bytes before import; changing the original path does not change queued content. Repairs create a new output revision and preserve prior publications. The source occurrence ID is retained; no automatic rebasing across changed source bytes is claimed. The current state schema is 16. Supported older state is migrated transactionally, and newer schemas are rejected. Existing jobs retain their saved narration rules, sources, voice and model pins.

PCM clips and receipts are promoted together. M4B time derives from 64-bit final sample counts. Assembly pipes bounded chunks to FFmpeg and encodes AAC once at explicit 24 kHz. All ready files, companion and report are promoted in one same-filesystem directory rename with no-replace semantics, checksums and a manifest. Recovery reconciles promotion before/after the DB ready checkpoint. Process-crash tests do not prove universal power-loss durability.

Ready output is `~/Audiobooks/JOB-rREV/`: `book.m4b`, `source.html`, `companion.json`, `companion.vtt`, `report.json`, `manifest.json`. The HTML contains exact lookup text and source locators; source identity points to the retained immutable snapshot in app state. Segment times are precise; word-level forced alignment is not implemented. AAC decode padding differs from source PCM; verified container offsets use PCM chapter boundaries with a 2 ms tolerance.

## PDF and OCR

English OCR is available in the ordinary book workflow after `hamr setup --ocr-only` with Tesseract installed. An installed Linux build resumed after an OCR interruption and published a verified two-page scan as a chaptered M4B. [Qualification and scope](../RELEASE.md#scanned-and-mixed-pdfs)

Searchable PDFs with reliable visible text go straight to planning. In a mixed PDF, Hamr retains native text and reads scan insets in their own crops when their boundaries are clear. It keeps competing visible and hidden text observations, checks reliable column geometry, and reports unresolved regions by page. On a uniquely captioned figure, the companion keeps the crop, its recognized labels, and its authored caption together. Captions follow the normal reading policy.

When page OCR reads “T” at the start of a quoted line, Hamr can recheck the line at two larger sizes. It accepts “I” when both reads agree and recover the same surrounding words. The source inventory records the original reading alongside the correction. Use `hamr repair JOB --reimport` to apply current OCR checks to a saved job.

When Tesseract is installed, guided setup offers English scanned-PDF support. Run `hamr setup --ocr-only` to add the pinned English model to an existing installation, or `hamr setup --ocr-profile auto` with your ordinary setup options. The model and notice total about 4.1 MB, with Tesseract supplied by your system. `hamr doctor` reports whether OCR is ready. The model runs on the CPU and recognition stays local. Tesseract can be on PATH or in a user-space tree selected by `AUDIOBOOK_OCR_ROOT`.

If interrupted before a job ID appears, repeat `hamr open` with the same PDF to reuse completed OCR checkpoints. Once you have a job ID, use `hamr resume JOB`. For an existing job held on a page whose OCR engine was missing, enable OCR and use `hamr repair JOB --reimport` before resuming. Large or unresolved regions remain source findings, with their observations available locally for review.

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
