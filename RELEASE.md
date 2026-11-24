# hamr 0.4.9.dev33

- A tagged PDF can use the shared table reader when its tags identify the visible text. Hamr can read merged and nested cells, continue one table across pages, and retain repeated print headings in the companion. Conflicting tags preserve the visible reading or identify the table for source review.
- Writer/Web OpenDocument packages, including `.oth` files and copies inside ZIPs, open as documents. Their embedded previews stay with the original.

# hamr 0.4.9.dev32

- Word, OpenDocument, HTML and EPUB read merged and nested table cells through one shared model. Grouped headers, cell notes, nested reading order and original locations stay attached to the facts they describe. [Document intake](docs/INTAKE.md#source-readers)

# hamr 0.4.9.dev31

- Word DOCX reads grouped, nested and linked editable text boxes, selecting the authored compatibility branch once. Ordinary table cells can read their linked notes. Hamr preserves the Word source locations through ZIP selection, narration and companion export. [Document intake](docs/INTAKE.md#source-readers)

# hamr 0.4.9.dev30

- RTF reads authored chapter outlines, footnotes, endnotes, text boxes, and font-specific encodings. The same reader works in TextBundle and Scrivener. Notes keep their anchors in the companion, and tables with merged or nested cells show the exact passage to verify. [Document intake](docs/INTAKE.md#source-readers)

# hamr 0.4.9.dev29

- Open AbiWord documents, templates and compressed copies directly or inside an archive. The resident reader follows chapter styles, notes, table coordinates, text boxes and line breaks. Page headers and footers stay in the companion, and unsaved field values appear at their source locations. [Document intake](docs/INTAKE.md#source-readers)
- Speech verification compares explicit amendment labels in square brackets or parentheses by value, so a spoken “Amendment three” can verify a printed “Amendment III.”

# hamr 0.4.9.dev28

- Open FictionBook 3 files, ZIPs, or extracted books. Hamr follows the package’s declared text and illustration links and keeps the original files in the companion. FictionBook 2 uses the same section, note, and verse preparation. Long stanzas split at their authored line breaks when a single request is too large. [Document intake](docs/INTAKE.md#source-readers)
- A heading that exceeds the M4B chapter-track limit gets a shortened navigation label. Its full wording stays in the source companion. Long unbroken words also pass through preparation without repeated email scans.

# hamr 0.4.9.dev27

- Open DocBook 4 and 5 manuals, chapters, and reference entries as XML files, source folders, or ZIPs. Hamr follows local XML includes and reads authored headings, footnotes, figure descriptions, and simple CALS tables. The source companion retains the chapter and illustration files. [Document intake](docs/INTAKE.md#source-readers)

# hamr 0.4.9.dev26

- Open BITS books, individual book-part XML exports, and NCBI Bookshelf source archives. Hamr reads the declared chapter order, resolves local XML includes, and keeps chapter originals and accompanying renditions in the source companion. [Document intake](docs/INTAKE.md#source-readers)
- Structured documents accept tables whose row and column spans explicitly say one cell. Actual merged or nested tables still get a source-specific review location.

# hamr 0.4.9.dev25

- Open TEI editions as `.tei` or `.xml` files, source folders or ZIPs. Hamr reads the authored main lemma or normalized form. Competing witness readings stay accessible in the source companion. Notes, illustrations and the original package use the same reading and recovery tools as JATS. [Document intake](docs/INTAKE.md#source-readers)

# hamr 0.4.9.dev24

- JATS articles read their title, primary abstract, body, and linked footnotes in order. Figures use authored descriptions, and tables with a text rendition read its cells once. Open a `.jats` or `.xml` file, the containing folder, or a source ZIP. The companion preserves the original article and declared local assets. [Document intake](docs/INTAKE.md#source-readers)
- A verified block replacement becomes the reading for that occurrence. A missing note or formula in the original passage can stay visible in the source companion while the supplied reading proceeds.

# hamr 0.4.9.dev23

- Open text-bearing DAISY 2.02 from a source folder, ZIP, NCC document or component SMIL. Hamr reads synchronized HTML in the authored order, applies the standard footnote settings, and keeps original source files available in the companion. Missing text cues identify the exact SMIL member and line. [Document intake](docs/INTAKE.md)

# hamr 0.4.9.dev22

- Read text-bearing DAISY 3 through the package OPF and SMIL sequence. The companion can recover exact text, synchronization and existing media files. Repeated passages in the authored order are preserved.
- Speech verification recognizes equivalent ISBN and web address formatting, paired year readings in matching contexts, and spelling variants such as Dust Bowl, MathML and DTBook. The report keeps the original ASR transcript and continues to compare actual numeric values and identifier digits.

# hamr 0.4.9.dev21

- Open a NIMAS textbook ZIP, source folder or `.opf` and follow its declared DTBook reading order. Recover the exact XML, package descriptor and illustrations from the finished companion.
- Read DTBook headings, linked notes, image descriptions and MathML through the same preparation as other structured documents. Printed page labels and marked contents stay available for reference in the companion.
- Other recent intake additions include TextBundle, mdBook, GitBook, Sphinx, Scrivener and standalone reStructuredText includes. The companion identifies original files and archive members. [Document intake](docs/INTAKE.md)

# hamr 0.4.9.dev8

- Prepare reading order, printed page boundaries, and prose wraps before applying narration rules. The same readable passage can carry its dialogue delivery through text, Word, PDFs, and scans. Preserve authored line breaks and keep timing and source evidence tied to the original text.

# hamr 0.4.9.dev7

- Read verified PAGE XML transcriptions with either modern coordinate attributes or the earlier point elements. Preserve multiline region text when reading archival sidecars.

# hamr 0.4.9.dev6

- Keep standalone scan page names and repair IDs stable from the reading plan to the saved job.

# hamr 0.4.9.dev5

- Open loose page images as one book and follow natural filename order or an authored manifest. Hamr captures the original pages, resumes in-progress intake, and publishes a portable original with exact member and crop references.
- Open raw ZIP and TAR archives when their selected contents have a readable route. ZIPs can carry their own page order, crops and OCR sidecars. The [intake guide](docs/INTAKE.md) lists the resident readers and their supported variants.
- Linux x86-64 and Windows x64 include the English OCR model and compact Kindle worker. Linux also carries the DjVu worker. The larger converter for LRF and unencrypted LIT is installed into Hamr on first use, with a download size and progress display.

# hamr 0.4.9.dev4

- A description after a quotation can guide the words it describes. When a narrator reports that a speaker's voice has softened, Hamr can read the preceding quotation more gently and return to the usual voice for the attribution. Tagged PDF paragraphs keep the lead-in together when it belongs to the same paragraph.
- On compatible timed-guidance runtimes, Hamr checks quote placement and wording in the short searchable PDF case. It can lower the strength, adjust the exit on the resulting take, and reuse saved attempts after an interruption. If the directed take stays uncertain, the job continues with ordinary narration. [Expressive narration guide](docs/USAGE.md#expressive-narration)

# hamr 0.4.9.dev3

- The timing verifier recognizes a small set of equivalent transcript word breaks within a source phrase, such as “all right” and “alright.” The text and its guide boundaries stay intact.

# hamr 0.4.9.dev2

- Timed whispers now check the narrator's reporting words when they come out weak. Hamr compares the phrase with the same words in an ordinary take, and can choose another reading when both speech checkers confirm a substantial local drop. The comparison appears in the finished book's `quote_timing` report. [Expressive narration guide](docs/USAGE.md#expressive-narration)
- When a character cuts off a word and another character immediately echoes those letters as a name, Hamr can use that echo to clarify the unfinished sound. The source and companion retain the printed fragment.

# hamr 0.4.8

- An explicitly whispered quotation can start a passage. Hamr measures it against the ordinary narration that follows, checks the words and delivery boundaries, and keeps the passage in one speech request.
- For a single opening quotation, Hamr measures timing from its ordinary take first. It can reuse that exact audio if directed attempts fail. An interrupted run can pick up the saved take. Existing jobs retain their pinned timing method.
- `hamr agent inspect JOB --content` reports the timed narration used in a published book. [Expressive narration guide](docs/USAGE.md#expressive-narration)

# hamr 0.4.7

- A whispered quote can follow the words Breeze actually speaks. Hamr can adjust the timing of an inline cue and check the resulting passage again. The narrator has its own direction between quoted lines. Resume reuses verified intermediate takes after an interruption.
- Attached phrases such as “in an undertone,” “in a whisper,” and “under her breath” can guide the dialogue they describe. The ordinary expressive workflow was exercised with text, Markdown, EPUB, searchable PDF and scanned PDF. Scanned pages can corroborate a disputed pronoun against the printed line before committing a reading.
- Run `hamr setup` to activate the new Linux runtime on Vulkan F16, Vulkan Q8 and CPU Q8. Each used the portable executable to publish a checked audiobook and play it to the end. The Windows runtime remains the upstream build. [Engine support](docs/ENGINES.md#timed-whispers)
- Render reports count new and reused clips across the entire command, including normal resume. The CLI and finished book report show the same elapsed time and take counts.

# hamr 0.4.6

- Hamr can time an explicit whisper inside a passage and follow a continuing speaker through a second quotation. Both quotes and the intervening narration stay in one speech request. Each directed take is independently checked for words and timing. Resume can pick up saved trials, and the book report includes the decision.
- The updated Linux runtime has passed setup, render, forced interruption, recovery, and decoded playback on CPU Q8, Vulkan Q8, and Vulkan F16. Run `hamr setup` to install it for one of these profiles. See [engine support](docs/ENGINES.md#timed-whispers) for platform coverage and the corresponding source.
- If two installed checkers point to the same substituted word in a held ordinary passage, Hamr can try the passage again automatically. A fresh take must pass the usual checks, and the disputed original is retained with its repair record. Neighboring recordings are reused.

# hamr 0.4.5

Hamr respects a sentence boundary after a finished quotation. In `“Stay here.” The trees whispered.` the second sentence stays ordinary narration. In `“Stay here,” she whispered.` the reporting clause still directs the spoken line. This applies to new books across text, Markdown, EPUB, and searchable and scanned PDF. Existing jobs retain their saved reading.

# hamr 0.4.4

- Dialogue attributed “in a hushed tone” or introduced by “she lowered her voice” can use a softer speaking voice. Explicit whisper cues direct a whisper. The reporting clause or immediate setup must belong to the same turn.
- Expressive narration can recognize a single quotation with a straight mark at one end and a curly mark at the other, a combination produced by some scanned PDF pages. The extracted characters and source locations are preserved.
- “He muttered” can give the quoted words a low, private delivery. Hamr asks Breeze to keep the words clear and the narration around the line in its usual voice.
- Setup can resume a transfer that ends early after receiving part of a model. If the transfer ends early again, Hamr reports how much it saved so setup can resume from there.

# hamr 0.4.3

### Audio mastering

New books balance their overall volume automatically. The CPU mastering pass uses one gain for the entire book, preserving the levels of whispers and shouts relative to the surrounding narration. Hamr checks the decoded M4B before publication, including its audio sample count independently of the chapter markers. Hamr can correct an isolated level jump against steady neutral narration, credit quiet margins at ordinary joins, and smooth an isolated discontinuity at the edge of a take. It records each edit in the book report and derives companion timing from the finished sequence. Existing checked books can be remastered without resynthesizing speech.

All 45 cast introductions have also been balanced for more consistent listening in the guide and in `hamr voice sample`. [Mastering and remastering commands](docs/USAGE.md#audio-mastering)

### Voice reference preparation

Custom voice import can recover a reference affected by steady electrical hum before Breeze sees it. Install the local CPU preparation worker with `hamr setup --wash-only`. Hamr retains the original media and records what it selected. The lighter FastEnhancer profile is available for explicitly requested preparation comparisons. [Preparation and hardware choices](docs/USAGE.md#prepare-a-voice-reference)

Carl Sagan and HAL 9000 build on the references chosen in blind comparisons. Sagan adds a second excerpt from the Library of Congress recording. HAL adds a third film excerpt. Dr. Orpheus uses the same two turns with their background noise and music reduced. Existing books retain the reference they were made with. [Carl Sagan](docs/CAST.md#carl-sagan) · [HAL 9000](docs/CAST.md#hal-9000) · [Dr. Orpheus](docs/CAST.md#dr-orpheus)

The three voices have new introductions that let you hear them change register within a short scene. HAL shifts from courtesy to unease, Sagan from invitation to wonder, and Dr. Orpheus from theatrical command to sincerity. Play them with `hamr voice sample CHARACTER --play`.

Blind listening across all 45 cast voices identified 22 preferred cleaned references. Each of those voices has a freshly recorded introduction made from its selected reference. [Cast guide](docs/CAST.md)

### Expressive narration

- When a plain paragraph needs multiple speech requests, Hamr can give a short closing sentence some preceding context. It keeps complete sentences together and uses the same request limits.
- Direct attributions can convey a caught breath, an eager or shy whisper, or a delivery introduced by a colon.
- An attribution inside a quoted sentence can direct both spoken fragments, even with narrator sentences around the turn. The whole passage stays in one request.
- Dialogue can take its direction from a fuller attribution such as “the doctor replied firmly” or “she said, her voice barely audible.” Hamr uses the reporting clause to tie the delivery to the right speaker.
- If the same turn continues in a second complete quotation in the paragraph, Hamr sends both quotes and the intervening attribution together. This lets a whisper or another attributed delivery carry into the second line without adding an audio join.
- Adjacent quotations across two aligned native PDF lines can stay in one speech request. If a paragraph tag covers both, Hamr can continue the delivery across the wrap. For an untagged wrap, Breeze handles the passage in its usual reading voice.
- An authored sigh or cough between complete sentences can stay in the same request as the quotation's delivery and emphasis. The companion shows the spoken words beside the original marker.
- An author-marked pronoun can carry the stress in a spoken line. EPUB stylesheet italics and Markdown emphasis can supply that cue. The plan records the marked word and its source location.
- `hamr preview --compare` auditions named seeds, directions, and sampling settings on the same saved passage. It saves the alternate WAVs with exact request receipts and leaves the book intact.
- A source-bound document policy can keep the auditioned direction, guidance, and sampling for one exact passage. Repair reuses the neighboring takes.

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
