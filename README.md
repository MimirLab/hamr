<h1 align="center">hamr</h1>

<p align="center"><strong>Let your books change shape.</strong></p>

<p align="center">Turn EPUBs, PDFs, Markdown and plain text into English audiobooks on your own computer.</p>

<p align="center">
  <a href="#listen">Meet the cast</a> ·
  <a href="#expressive-narration">Hear a scene two ways</a> ·
  <a href="#epub-to-audiobook-in-three-commands">Make a book</a> ·
  <a href="#what-hamr-can-do">Explore how it works</a>
</p>

<p align="center">
  <img src="docs/art/hamr-storyforge.png" width="840" alt="An illuminated book sends golden ink into the shapes of a raven, a wolf and a sound wave. A raven in headphones listens beside a brass microphone." />
</p>

## Listen

What are you reading? A field guide belongs with Nigel. A book about the universe might feel right with Sagan. For a safety manual, there's always Cave Johnson.

| Voice | Reach for it when… | Hear the introduction |
| --- | --- | --- |
| **Nigel Thornberry** | You'd like someone to get genuinely excited about a footprint. | [▶ Nigel](https://raw.githubusercontent.com/MimirLab/hamr/9ed6f5d933fcf0c781380d6b132f585e9989e609/docs/listen/nigel-thornberry.mp3) |
| **Carl Sagan** | The book has a way of making the room feel a little bigger. | [▶ Sagan](https://raw.githubusercontent.com/MimirLab/hamr/9ed6f5d933fcf0c781380d6b132f585e9989e609/docs/listen/carl-sagan.mp3) |
| **Cave Johnson** | The experiment probably goes better if nobody reads the liability waiver. | [▶ Cave](https://raw.githubusercontent.com/MimirLab/hamr/9ed6f5d933fcf0c781380d6b132f585e9989e609/docs/listen/cave-johnson.mp3) |
| **Dr. Orpheus** | There's a cellar door, and naturally you're going to open it. | [▶ Orpheus](https://raw.githubusercontent.com/MimirLab/hamr/9ed6f5d933fcf0c781380d6b132f585e9989e609/docs/listen/dr-orpheus.mp3) |
| **HAL 9000** | A voice this calm should be reassuring. | [▶ HAL](https://raw.githubusercontent.com/MimirLab/hamr/9ed6f5d933fcf0c781380d6b132f585e9989e609/docs/listen/hal-9000.mp3) |

**[Meet all 45 voices →](docs/CAST.md)** Each character introduces themselves. The introductions are balanced for browsing the cast, and the same clips play offline with `hamr voice sample CHARACTER --play`, even before model setup.

## Expressive narration

> “Keep your eyes on the lantern,” she whispered, “until the footsteps fade.”
>
> The latch began to turn.
>
> “Run for the bridge!” she shouted.

**[▶ Plain reading](https://raw.githubusercontent.com/MimirLab/hamr/9ed6f5d933fcf0c781380d6b132f585e9989e609/docs/demo/lantern-plain.mp3)** · **[▶ Expressive reading](https://raw.githubusercontent.com/MimirLab/hamr/9ed6f5d933fcf0c781380d6b132f585e9989e609/docs/demo/lantern-expressive.mp3)** · [Read the source](docs/demo/lantern.md)

Same passage, same Nigel. Listen to the two halves of the whispered line and then to the warning. In the expressive take, Hamr uses *she whispered* to direct the speech on both sides of the attribution, then brings the narrator back in gently for the latch.

This is how an ordinary book runs. Hamr can follow clear cues for tone, pace, emphasis, and authored sounds such as a sigh or a laugh. It uses the same rules for EPUB, Markdown, text, and PDFs, including scans after OCR. Use `--performance off` for a plain reading of one book, or `hamr settings --performance off` to make that your preference. [Delivery guide](docs/USAGE.md#expressive-narration)

An eager whisper can carry a written sigh and a moment of emphasis in one continuous reading. Audition variations on a saved passage with `hamr preview --compare`, then keep the chosen direction and settings with a targeted repair. [Compare and keep alternate takes](docs/USAGE.md#expressive-narration)

“She lowered her voice” calls for softer speech. “To a whisper” makes the direction more specific, and “he muttered” gives the line a low, private delivery. A pair of quotation marks can still carry a cue when OCR reads one as curly and the other as straight.

On Linux with CPU Q8, Vulkan Q8, or Vulkan F16, Hamr can direct two whispered lines in the same passage and let the narrator return for the words between them. It follows where Breeze actually speaks the quoted words and can adjust the direction when a quote starts or ends at a different time. It checks the full passage before using the take. Run `hamr setup` to install the updated runtime. [Continuous dialogue and engine support](docs/ENGINES.md#timed-whispers)

A whisper can also open the passage. Hamr can time it from an ordinary recording, then keep that recording if a directed take fails the word, timing, or level check.

## EPUB to audiobook in three commands

[Install on Linux](#install-on-linux) or [install on Windows](docs/ENGINES.md#what-hamr-can-use), then replace the example filename. The preview is optional.

```sh
hamr setup                               # First run. Enter accepts the suggested model.
hamr voice sample nigel-thornberry --play # Hear your narrator.
hamr open "$HOME/Downloads/My book.epub" --voice cast:nigel-thornberry
```

Review the reading plan and enter `y`. Hamr renders and checks the speech, then prints the job ID and the path to a chaptered `book.m4b` under `~/Audiobooks/JOB-r1/`. The folder includes the original document and a timed companion. The same command accepts PDF, Markdown, and text files.

Hamr balances the finished book’s playback level while preserving the differences between whispers, narration, and shouts. It counts quiet margins at ordinary joins so the pauses in the recording and assembly work together. [Audio mastering](docs/USAGE.md#audio-mastering)

Use `--output DIRECTORY` to choose a destination, `--play` to start listening when it finishes, or `--yes` to run unattended. If interrupted, run `hamr resume JOB` with the job ID it printed. [See how recovery works](#resume-and-repair)

<details>
<summary>See a finished run</summary>

```text
Ravens in Winter · 2 chapters · about 0.3 minutes
Recommended: listen. Narrator: cast:nigel-thornberry.
0 locations need review.
Start recommended? [y/N, t tables, c literals] y
e6ff67e457854bb6: Checking the full narration
e6ff67e457854bb6: Assembling the audiobook
e6ff67e457854bb6 · ready · Ravens in Winter
/tmp/hamr-demo/Audiobooks/e6ff67e457854bb6-r1/book.m4b
```

</details>

## What hamr can do

### Scans and archives

Open a folder of JPG, PNG or TIFF page images directly, or point Hamr at the ZIP the book arrived in. The page preview shows the order. Completed books carry a portable copy of their original pages and the companion points back to each one. [Scan folders, archives and other document formats](docs/INTAKE.md)

Hamr also opens NIMAS textbooks, text-bearing DAISY packages, JATS research articles, TEI editions, BITS books from NCBI Bookshelf, and DocBook manuals. It follows their authored order, reads linked notes where they belong, and keeps the original package files available in the companion. [Structured document readers](docs/INTAKE.md#source-readers)

AbiWord documents and templates can be opened directly, including compressed copies. Hamr reads authored chapter styles, linked notes and tables and keeps the original file with the audiobook. [AbiWord intake](docs/INTAKE.md#source-readers)

Rich-text documents keep authored chapter outlines, notes and readable text boxes. You can open RTF directly or use the same reader in a Scrivener project or TextBundle. [Rich-text intake](docs/INTAKE.md#source-readers)

Word documents can read editable text boxes in their authored story order. Their text, notes and equations go through the same narration preparation as the body. Hamr keeps the original Word file in the companion. [Word intake](docs/INTAKE.md#source-readers)

Merged and nested tables in Word, OpenDocument, HTML, EPUB and supported tagged PDFs can read with their authored headers and original cell order. Hamr can join a tagged table across pages, keeping one copy of each value and skipping repeated print headings. [Table intake](docs/INTAKE.md#source-readers)

Searchable PDFs and scanned pages can also read tables whose printed grid or repeated alignment establishes the cells and their order. Each cell points back to its place on the original page. [Table intake](docs/INTAKE.md#source-readers)

XPS and OpenXPS packages can open directly or inside ZIPs. Hamr follows declared pages and authored stories. On compatible Linux hosts, it can also read pages printed as images or glyph outlines through its built-in OCR. The companion can recover exact page files. [Document intake](docs/INTAKE.md#source-readers)

Open Jupyter notebooks to hear their Markdown, equations, and readable saved results in cell order. Code and graphics are available in the companion, including the exact embedded image files. [Notebook intake](docs/INTAKE.md#jupyter-notebooks)

Turn a PowerPoint deck into chapters with its slide text and presenter notes. Hamr reads merged tables, supported chart data, and text from image-only slides using its built-in OCR. The companion keeps source locations for the slides, notes, and embedded images. [PowerPoint intake](docs/INTAKE.md#powerpoint-presentations)

Open LibreOffice and OpenDocument presentations the same way. Slides, notes, tables, saved chart data, and eligible images use the shared reading pipeline. The built-in reader accepts `.odp`, `.otp`, and `.fodp`. [OpenDocument presentation intake](docs/INTAKE.md#opendocument-presentations)

Open Palm Markup Language books and `.pmlz` archives with the built-in reader. Chapters, footnotes, and sidebars enter the shared narration pipeline. The companion keeps the original text and local artwork. [Palm ebook intake](docs/INTAKE.md#palm-markup-language)

Read PalmDOC, Weasel Reader zTXT, and unencrypted eReader books directly from recognized `.pdb` or `.prc` files. Hamr preserves text across record boundaries and keeps embedded pictures and reader records available in the companion. [Palm Database books](docs/INTAKE.md#palm-database-books)

FictionBook 2 and 3 keep linked notes and verse in the authored reading. Open a FictionBook 3 archive directly to follow its chapter text and retain linked illustrations in the source companion. [FictionBook intake](docs/INTAKE.md#source-readers)

### Scanned and mixed PDFs (OCR)

An old scan or a page with a scanned inset can become part of the same book as searchable text. Hamr uses the PDF’s native text where it can and runs local English OCR where it needs the page image. It follows simple two-column layouts and can keep a figure crop, its recognized labels, and the author's caption together in the companion.

A stray “T” at the start of a quote can be re-read against two larger crops. When both crops agree on “I” and the surrounding words, Hamr can use that reading. Unresolved findings point back to the page, and completed OCR work survives an interruption. On Linux x86-64 and Windows x64, the package includes the English OCR runtime and model. Open the PDF as you would an EPUB. [PDF and OCR guide](docs/USAGE.md#pdf-and-ocr)

### Contextual reading and book structure

`$100` sounds like “one hundred dollars.” The zeroes in `Room 007` belong there. `1:30` can mean a clock time or a duration. Hamr uses nearby words and page structure to make those choices, carry table headers into values, and put linked notes near the passages that cite them. You can inspect the planned words with `hamr plan "My book.epub" --json` or trace a finished passage back to the original wording in the companion. [Context and reading options](docs/USAGE.md#tune-readings-that-depend-on-context)

### Offline speech-to-text (STT)

“Do open the hatch” is a different instruction from “Do **not** open the hatch.” Before making the M4B, Hamr listens back with local speech recognizers. It checks for dropped or repeated words, changed numbers and units, and missing negations. Two recognizers can corroborate a critical error before Hamr retries a take. An uncertain transcription of an authored laugh can be flagged for listening while the performance is kept.

Setup chooses the checker independently of the voice and speech model. Fluid INT8 runs on eligible Linux systems, and the lighter Whisper pair serves lower-memory machines and Windows x64. Both run on the CPU. `hamr transcribe JOB --utterance u00003 --json` inspects a saved take, and `hamr transcribe "sample.wav" --json` works on a local recording. Refresh an existing checker with `hamr setup --checks-only`. [Checker presets and review](docs/USAGE.md#speech-verifier-presets)

### Resume and repair

A reboot needn't cost you the whole book. `hamr resume JOB` verifies saved takes and picks up unfinished work. `hamr status JOB --json` shows the progress, the output path, and any source or speech finding that needs attention. A bad line can get another take without remaking the chapters around it.

```sh
hamr repair JOB --retake u00042
hamr resume JOB
```

You can also correct a pronunciation, revise a source passage, or rebuild the packaging. A repair publishes a new edition and preserves earlier ones. Play a finished edition with `hamr play JOB`. [Recovery guide](docs/USAGE.md#recovery-and-repair)

### Bring your own narrator

Save a cast member with `hamr voice select nigel-thornberry` or import a 2–30 second reference of your own with its exact transcript.

```sh
hamr voice clone my-narrator --reference clean.wav --transcript-file exact.txt
```

Hamr can safely try cleaning steady electrical hum during import once you run `hamr setup --wash-only`. It checks the cleaned version against the transcript, preserves the original, and falls back to the original if the check fails. Cast introductions are for listening. Breeze uses the separate reference and exact transcript to make new recordings. [Voice preparation and lighter CPU options](docs/USAGE.md#prepare-a-voice-reference)

## Install on Linux

You'll need Git, Python 3.12 or newer, uv, FFmpeg with ffprobe, and mpv for playback. The Breeze TTS2 runtime uses a Linux x86-64 CPU with AVX2 and the Vulkan loader. A GPU profile also needs a working Vulkan driver. [Install uv](https://docs.astral.sh/uv/getting-started/installation/)

```sh
git clone https://github.com/MimirLab/hamr.git
cd hamr
uv build --wheel
./install.sh
export PATH="$HOME/.local/bin:$PATH"
hamr --version
man hamr
```

The installed `hamr` command works from any directory. To work from the checkout instead, run `uv run hamr` there, for example `uv run hamr setup`. `git pull` updates the checkout. Reinstall the wheel to update the separate command on PATH. When Hamr prints a recovery command, run it in the same environment and keep the same `--home` if you set one.

Windows x64 with CPU Q8 has passed installation, checked M4B publication, and forced-exit recovery on a native runner. [Windows install and Vulkan status](docs/ENGINES.md#what-hamr-can-use) · [Back to the first book](#epub-to-audiobook-in-three-commands)

### Which profile should I choose?

Start with `hamr setup`. It recommends a model for the graphics device and available memory it sees, then downloads the model you choose. Press Enter to accept, or use `hamr setup --choose` to see all options. `hamr doctor` explains its recommendation without loading the model.

**Vulkan** lets Breeze use an AMD, NVIDIA, or Intel graphics device through its driver. **F16** stores the model weights at 16-bit precision. **Quantization** makes the model smaller so it can run with less storage and memory. Q8, Q6, and Q4 are progressively smaller. They can change delivery as well as speed.

F16 is a starting point for a discrete GPU with 12–16 GiB of graphics memory, or try Q8 with 8 GiB. On CPU, Q8 has passed short book and recovery tests. Q4 is the lowest-memory option if Q8 will not fit. Close GPU-heavy applications or step down a size if setup reports insufficient memory. If Vulkan is unavailable, check the loader and driver with `hamr doctor` and `vulkaninfo --summary`.

<details>
<summary>Compare profiles, download sizes, and hardware requirements</summary>

The sizes below are the engine and its weights. Checker setup adds approximately 605 MiB for the Whisper pair plus runtime dependencies, or about 801 MB of model files for Fluid INT8.

| Profile | Hardware route¹ | Approx. peak VRAM² | System RAM budget² | Fresh download³ | Quality / speed and testing |
| --- | --- | ---: | ---: | ---: | --- |
| `vulkan-f16` | AMD / NVIDIA / Intel Vulkan GPU | 7–9 GiB | 4–8 GiB | 5.87 GiB | Least weight compression. **Locally tested**, including long narration. |
| `vulkan-q8` | AMD / NVIDIA / Intel Vulkan GPU | 4–6 GiB | 3–6 GiB | 3.34 GiB | Smaller weights and lower memory use. **Tested on Radeon** with matched voice controls and chaptered books. |
| `vulkan-q6` | AMD / NVIDIA / Intel Vulkan GPU | 3.5–5 GiB | 3–6 GiB | 2.93 GiB | More compression. Available in setup, render trials pending. |
| `vulkan-q4` | AMD / NVIDIA / Intel Vulkan GPU | 3–4.5 GiB | 3–6 GiB | 2.43 GiB | Smallest GPU model. Available in setup, render trials pending. |
| `vulkan-q8-cpu-text-encoder` | Vulkan GPU + CPU text encoding | 4–6 GiB | 8–12 GiB | 3.39 GiB | Moves text encoding to CPU. Available in setup, memory savings and render trials pending. |
| `cpu-f16` | CPU | 0 | 10–16 GiB | 5.92 GiB | Least compression and largest RAM budget. Available in setup, render trials pending. |
| `cpu-q8` | CPU | 0 | 8–12 GiB | 3.34 GiB | **Tested on Linux and Windows** with short audiobook renders, speech checks and resume after forced exit. Long-book timing depends on the CPU. |
| `cpu-q6` | CPU | 0 | 7–11 GiB | 2.93 GiB | Intermediate RAM and precision. Available in setup, render trials pending. |
| `cpu-q4` | CPU | 0 | 6–10 GiB | 2.43 GiB | Smallest CPU model. Available in setup, render trials pending. |

¹ Estimates are for Linux x86-64 with AVX2. GPU profiles need a Vulkan driver. CPU profiles still need the Vulkan loader. The [Windows setup](docs/ENGINES.md#what-hamr-can-use) uses its own pinned runtime.

² Memory figures are estimates for planning. GPU ranges use the [upstream footprints](https://github.com/HoppouAI/Breeze-TTS-2.cpp/blob/4110a9fff4e47d4d1ba850d92d81f3ba86bf39c6/README.md#weights) with headroom; system RAM and CPU-offload peaks haven't been measured. Other applications, reference length and parallel generation affect what fits. Integrated graphics share system RAM.

³ Linux downloads include the runtime, selected weights and model terms, before cache reuse. Windows downloads about 52 MiB more for the three profiles with the updated Linux runtime. Leave at least another 1 GiB for extraction and headroom. One GiB is 1,024³ bytes.

F16 has rendered long narration on a Radeon RX 6800 XT with 16 GiB. Q8 has produced a continuous take over 45 seconds on the same card. See the [measured controls and tensor recipes](docs/ENGINES.md#what-the-quantization-labels-mean). `hamr profiles` lists native CUDA and MLX under research. Those paths still need setup adapters.

As a rough starting point: **12–16 GiB GPU → F16**, **8 GiB → Q8**, **6 GiB → Q6**, **4 GiB → Q4**. F16 should have at least 9 GiB available; Q4 on a 4 GiB card will be tight. Start small on integrated graphics. Without usable GPU support, try `cpu-q4` if you have enough system RAM. Doctor checks current memory, so an open game or another model can change its recommendation.

You can also check the hardware yourself:

```sh
hamr doctor
hamr profiles
lspci -nn | grep -Ei 'VGA|3D|Display'
free -h
vulkaninfo --summary              # Optional: supplied by vulkan-tools
nvidia-smi                        # Optional: NVIDIA hardware
```

If setup runs out of memory, close other GPU-heavy applications or step down from Q8 to Q6 to Q4. Q8 with CPU text encoding may help when system RAM is plentiful; CPU Q4 avoids GPU allocation. If Vulkan is unavailable, check the loader, vendor driver/ICD and device access with `hamr doctor` and `vulkaninfo --summary`. Repeating setup resumes matching partial downloads. A failed setup leaves your active settings and existing books in place.

To choose a specific engine, run `hamr setup --profile vulkan-f16`, replacing `vulkan-f16` as needed. Scripts can use `hamr setup --auto` on eligible hosts. [Engine evidence and benchmarks](docs/ENGINES.md)

</details>

## Why hamr?

*Hamr* is Old Norse for skin or shape. In *Ynglinga saga*, Odin leaves his body resting while he travels in another form. A book taking on a voice seemed a good use for the name. [Dictionary entry](https://www.germanic-lexicon-project.org/html/oi_cleasbyvigfusson/b0236.html) · [Saga, chapter 7](https://sacred-texts.com/book/heimskringla-or-the-chronicle-of-the-kings-of-norway/the-ynglinga-saga) · [About the artwork](docs/ARTWORK.md)

<p align="center">
  <img src="docs/art/hamr-listener.png" width="160" alt="A raven in copper headphones perches on a book." />
</p>

## Files and privacy

Setup downloads models. Planning, rendering, and speech verification happen locally. Books, job data, and personal reviews stay on your computer. Fresh installs use `~/.local/share/hamr` for local state. Set `HAMR_HOME` or pass `--home` to use another directory. Existing installations retain their owned legacy state, and `audiobook` remains an alias for `hamr`.

## Working on hamr

[Command guide](docs/USAGE.md) · [Linux manual](docs/man/hamr.1) · [Cast guide](docs/CAST.md) · [Release notes](RELEASE.md) · [Published releases](https://github.com/MimirLab/hamr/releases) · [MIT application code](LICENSE)

Bug reports are most useful with a source location or timestamp, the expected words, and what you heard. [Open an issue](https://github.com/MimirLab/hamr/issues/new)

Scripts and assistants can discover JSON commands with `hamr agent capabilities` and inspect a job with `hamr agent inspect JOB`. [Operator guide](src/audiobook/agent_guide.md)

```sh
uv sync --frozen --extra test
uv run pytest -q
uv build
python3 tools/check_release.py
```
