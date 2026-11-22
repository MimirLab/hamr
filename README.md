<p align="center">
  <img src="docs/art/hamr-storyforge.png" width="1100" alt="An illuminated storybook sends golden ink into the shapes of a raven, a wolf and a sound wave. A raven in headphones listens beside a brass microphone." />
</p>

<h1 align="center">hamr</h1>

<p align="center"><strong>Let your books change shape.</strong></p>

<p align="center">
  <a href="https://github.com/MimirLab/hamr/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/MimirLab/hamr?style=flat-square&amp;labelColor=172536&amp;color=c99550" /></a>
  <img alt="45 built-in voices" src="https://img.shields.io/badge/cast-45_voices-c99550?style=flat-square&amp;labelColor=172536" />
  <img alt="EPUB, Markdown and PDF" src="https://img.shields.io/badge/books-EPUB_%C2%B7_MD_%C2%B7_PDF-72b8ad?style=flat-square&amp;labelColor=172536" />
  <a href="LICENSE"><img alt="MIT application code" src="https://img.shields.io/badge/code-MIT-72b8ad?style=flat-square&amp;labelColor=172536" /></a>
</p>

<p align="center">
  <a href="#epub-to-audiobook-in-three-commands">Make a book</a> ·
  <a href="#what-hamr-can-do">What hamr can do</a> ·
  <a href="#expressive-narration">Expressive narration</a> ·
  <a href="#listen">Meet the cast</a> ·
  <a href="docs/USAGE.md">The field guide</a> ·
  <a href="RELEASE.md">Release notes</a> ·
  <a href="https://github.com/MimirLab/hamr/releases/latest">Downloads</a>
</p>

**hamr turns EPUB, Markdown, PDF and plain-text files into audiobooks with Breeze TTS2.** It runs on your computer and comes with 45 voices. Choose one, check the text, and make a chaptered M4B you can take to your audiobook player.

Give Nigel Thornberry a nature book or Carl Sagan an astronomy text. Cave Johnson will read the safety manual too, if you're feeling brave.

You can inspect the narration before rendering, resume after an interruption, and retake a bad passage without starting the whole book again. Each finished book comes with its source and a timed companion for following along or tracking down a mistake.

**New in 0.4.2.** Hamr follows the author's cues for tone, emphasis, pace, and sounds in ordinary listening mode. A whisper can survive an interruption by “she said,” a warning can build across sentences, and an authored laugh can get its own beat. That works with searchable and scanned PDFs as well as EPUB, Markdown, and text. [How expressive narration works](#expressive-narration) · [What changed in 0.4.2](RELEASE.md#hamr-042)

## EPUB to audiobook in three commands

Once hamr is installed, replace the example filename and run this from any directory.

```sh
# First run only. Press Enter to choose the recommended profile.
hamr setup

# Optional. Hear Nigel before making the book.
hamr voice sample nigel-thornberry --play

# Review the plan and enter y to start.
hamr open "$HOME/Downloads/Ravens in Winter.epub" --voice cast:nigel-thornberry
```

hamr renders the narration, checks the speech locally and prints the finished `book.m4b` path. The default is `~/Audiobooks/JOB-r1/`, alongside the original document and companion files. Pass `--output DIRECTORY` to use an existing folder or `--play` to listen as soon as the book is ready.

Here is a successful run with a separate output folder and some progress lines omitted.

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

Use the same command with a PDF, Markdown, or `.txt` file. Add `--yes` for unattended conversion.

## What hamr can do

| When you want to… | Where to start |
| --- | --- |
| **Hear a book read naturally** | Contextual reading is on by default. Hamr uses nearby words and document structure to read abbreviations, amounts, dates and times. The companion preserves the original wording. Use `hamr plan DOCUMENT --json` to inspect the reading before synthesis. [Reading and pronunciation controls](docs/USAGE.md#tune-readings-that-depend-on-context) |
| **Give dialogue some acting** | Expressive delivery is on in EPUB, PDF, Markdown, and plain text. Hamr can follow an author's direction, from a sigh or laugh to a whispered line broken by “she said.” [Expressive narration and how to use it](#expressive-narration) |
| **Listen to a scanned PDF** | With Tesseract installed, `hamr setup --ocr-only` adds offline English OCR. Hamr can read simple two-column scans and recognize scanned regions beside selectable text. [PDF and OCR guide](docs/USAGE.md#pdf-and-ocr) |
| **Pick up where a book stopped** | `hamr resume JOB` checks and reuses saved takes. A retake changes one passage, and `hamr status JOB` shows whether a book is ready or needs review. [Recovery and repair](#resume-and-repair) |
| **Check the words before export** | The ordinary book command runs local speech checks before publishing. Use `hamr transcribe CLIP.wav` to inspect a disputed recording with the installed verifier. [Speech checking](docs/USAGE.md#speech-verifier-presets) |

The [release notes](RELEASE.md) record completed render, playback and interruption tests and the scope of each result.

## Expressive narration

Expressive delivery is on by default in EPUB, searchable and scanned PDF, Markdown, and plain text. Hamr can carry a whisper through an attribution, let urgency build or a voice fade, and stress a word the author singles out. A line can also pick up the tone of the prose around it when the evidence points to the same speaker. Scanned pages use the local English OCR setup.

An authored sigh or chuckle can be performed as part of the line. The speech checker checks the words before export. The exact sound and delivery can vary with the voice and engine. If you prefer steady narration, save that preference for new books.

```sh
hamr settings --performance off --mode study
```

Use `hamr settings` to see your choices. Supply `--performance off` on a single book to read it plainly. [Delivery controls and supported cues](docs/USAGE.md#expressive-narration) · [Changes in the source checkout](RELEASE.md#upcoming-changes)

## Listen

These are short introductions made with hamr. All 45 are included in the program and play offline with `hamr voice sample CHARACTER --play`.

| Narrator | Try them with | Introduction |
| --- | --- | --- |
| **Nigel Thornberry** | Wildlife, travel and natural history | [Listen](https://raw.githubusercontent.com/MimirLab/hamr/main/docs/listen/nigel-thornberry.mp3) |
| **Carl Sagan** | Astronomy and science | [Listen](https://raw.githubusercontent.com/MimirLab/hamr/main/docs/listen/carl-sagan.mp3) |
| **Cave Johnson** | Science with rather too much confidence | [Listen](https://raw.githubusercontent.com/MimirLab/hamr/main/docs/listen/cave-johnson.mp3) |
| **Garak** | Memoirs, politics and unreliable narrators | [Listen](https://raw.githubusercontent.com/MimirLab/hamr/main/docs/listen/garak.mp3) |
| **The Stanley Parable Narrator** | Fiction that keeps an eye on the reader | [Listen](https://raw.githubusercontent.com/MimirLab/hamr/main/docs/listen/the-stanley-parable-narrator.mp3) |
| **Dale Gribble** | Government documents. He has questions. | [Listen](https://raw.githubusercontent.com/MimirLab/hamr/main/docs/listen/dale-gribble.mp3) |

**[Hear all 45 voices →](docs/CAST.md)**

```sh
hamr voice cast
hamr voice sample nigel-thornberry --play
hamr voice select nigel-thornberry
```

The introductions are playback previews. Breeze clones from each voice's original clean recording and exact transcript. It never uses the generated introduction as a cloning reference.

To use your own voice, supply a clean 2–30 second recording and its exact transcript:

```sh
hamr voice clone my-narrator --reference clean.wav --transcript-file exact.txt
```

## Install on Linux

You'll need Git, Python 3.12 or newer, [uv](https://docs.astral.sh/uv/getting-started/installation/), FFmpeg with ffprobe, and mpv for playback. The speech engine runs on Linux x86_64 with an AVX2 CPU and the Vulkan loader. A GPU profile also needs a working Vulkan driver.

```sh
git clone https://github.com/MimirLab/hamr.git
cd hamr
uv build --wheel
./install.sh
export PATH="$HOME/.local/bin:$PATH"
hamr --version
man hamr
```

The installer checks the wheel version and installs the command and Linux manual for your account. If you prefer to run directly from a source checkout, stay in that directory and use `uv run hamr` in place of `hamr`. For example, `uv run hamr setup` runs setup through the checkout’s environment and `uv run hamr open "/path/to/My book.epub" --voice cast:nigel-thornberry` makes a book. Use the installed command for work outside the checkout.

If a checkout reports a missing speech checker, run the recovery command it prints, or run `uv run hamr setup --checks-only` from that checkout with the same `--home` option, if used. Checker setup chooses a verifier automatically and requires no speech engine profile. `git pull` updates the checkout. Reinstall the wheel to update the separate `hamr` command on PATH.

Linux has the broadest test coverage. Windows x64 with CPU Q8 has passed installation, checked M4B publication and forced-exit recovery on a native runner. See the [Windows installation and engine guide](docs/ENGINES.md#what-hamr-can-use) for that route and the status of Windows Vulkan.

### Which profile should I choose?

Start with **`hamr doctor`**. It reads GPU and memory information from the driver, then recommends a starting profile and explains the choice. It doesn't load a speech model. **`hamr setup`** shows one choice in plain language. Press Enter to start, or use `hamr setup --choose` for the full list. Scripts can use `hamr setup --auto` when there is a well-established fit.

With a compatible graphics device and enough free memory, setup offers the full model. If the graphics device needs more room, setup offers a smaller one. On processor-only systems it offers the short-tested Q8 model with at least 12 GiB of available system memory, or the smaller Q4 where memory is tighter. Processor-only audiobook rendering can take much longer than playback.

**Vulkan** lets the speech engine use your GPU through its graphics driver. It supports AMD, NVIDIA and Intel hardware with a suitable driver. **F16** stores the model's weights at 16-bit precision. **Quantization** uses fewer bits: Q8, Q6 and Q4 need progressively less storage and memory, but may change the voice's detail and delivery. A smaller model can be faster, though that depends on the hardware.

Setup downloads the selected engine and a separate CPU speech checker. The Whisper pair adds about 605 MiB plus runtime dependencies. Eligible hosts can also use Fluid INT8, which adds about 801 MB of model files. Setup verifies the pinned files and checks that the models load.

| Profile | Hardware route¹ | Approx. peak VRAM² | System RAM budget² | Fresh download³ | Quality / speed and testing |
| --- | --- | ---: | ---: | ---: | --- |
| `vulkan-f16` | AMD / NVIDIA / Intel Vulkan GPU | 7–9 GiB | 4–8 GiB | 5.92 GiB | Least weight compression. **Locally tested**, including long narration. |
| `vulkan-q8` | AMD / NVIDIA / Intel Vulkan GPU | 4–6 GiB | 3–6 GiB | 3.39 GiB | Smaller weights and lower memory use. **Tested on Radeon** with matched voice controls and chaptered books. |
| `vulkan-q6` | AMD / NVIDIA / Intel Vulkan GPU | 3.5–5 GiB | 3–6 GiB | 2.93 GiB | More compression. Available in setup, render trials pending. |
| `vulkan-q4` | AMD / NVIDIA / Intel Vulkan GPU | 3–4.5 GiB | 3–6 GiB | 2.43 GiB | Smallest GPU model. Available in setup, render trials pending. |
| `vulkan-q8-cpu-text-encoder` | Vulkan GPU + CPU text encoding | 4–6 GiB | 8–12 GiB | 3.39 GiB | Moves text encoding to CPU. Available in setup, memory savings and render trials pending. |
| `cpu-f16` | CPU | 0 | 10–16 GiB | 5.92 GiB | Least compression and largest RAM budget. Available in setup, render trials pending. |
| `cpu-q8` | CPU | 0 | 8–12 GiB | 3.39 GiB | **Tested on Linux and Windows** with short audiobook renders, speech checks and resume after forced exit. Long-book timing depends on the CPU. |
| `cpu-q6` | CPU | 0 | 7–11 GiB | 2.93 GiB | Intermediate RAM and precision. Available in setup, render trials pending. |
| `cpu-q4` | CPU | 0 | 6–10 GiB | 2.43 GiB | Smallest CPU model. Available in setup, render trials pending. |

¹ Estimates are for Linux x86-64 with AVX2. GPU profiles need a Vulkan driver. CPU profiles still need the Vulkan loader. The [Windows setup](docs/ENGINES.md#what-hamr-can-use) uses its own pinned runtime.

² Memory figures are estimates for planning. GPU ranges use the [upstream footprints](https://github.com/HoppouAI/Breeze-TTS-2.cpp/blob/4110a9fff4e47d4d1ba850d92d81f3ba86bf39c6/README.md#weights) with headroom; system RAM and CPU-offload peaks haven't been measured. Other applications, reference length and parallel generation affect what fits. Integrated graphics share system RAM.

³ Downloads include the runtime, selected weights and model terms, before cache reuse. Leave at least another 1 GiB for extraction and headroom. One GiB is 1,024³ bytes.

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

### Select a profile explicitly

```sh
hamr setup
# Or select a profile directly:
hamr setup --profile vulkan-f16
```

Setup checks that the model starts before activating it. In scripts, supply `--profile` or use `--auto` on eligible hardware. More detail is in the [setup guide](docs/USAGE.md#install-and-setup).

### Optional checks before rendering

```sh
hamr plan 'My book.epub' --json
hamr voice select nigel-thornberry
hamr status
hamr play JOB
```

`voice select` saves a default for new books. A per-book `--voice` takes priority. PDFs with scans or difficult layouts may need source review. See the [usage guide](docs/USAGE.md) for OCR, reading policies, review and unattended queues.

## Resume and repair

If rendering stops or the computer reboots, check the job and resume it:

```sh
hamr status JOB --json
hamr resume JOB
```

hamr verifies saved takes before reusing them and retries unfinished work. To retake a particular passage:

```sh
hamr repair JOB --retake u00042
hamr resume JOB
```

You can also correct a pronunciation, repair a source passage or rebuild the packaging. Earlier publications are retained. The [recovery guide](docs/USAGE.md#recovery-and-repair) explains the options; [unattended rendering](docs/USAGE.md#unattended-speech-and-verification) covers `hamr run` and offline ASR checks.

## Why hamr?

*Hamr* is Old Norse for skin or shape. In *Ynglinga saga*, Odin leaves his body resting while he travels in another form. A book taking on a voice seemed a good use for the name.

[Dictionary entry](https://www.germanic-lexicon-project.org/html/oi_cleasbyvigfusson/b0236.html) · [Ynglinga saga, chapter 7](https://sacred-texts.com/book/heimskringla-or-the-chronicle-of-the-kings-of-norway/the-ynglinga-saga) · [About the artwork](docs/ARTWORK.md)

## Files and privacy

Books, job data and personal reviews stay in local app state. Model downloads happen during setup; planning, rendering and recovery use local files and models.

Fresh installs use `~/.local/share/hamr`. Set `HAMR_HOME` or pass `--home` to use another directory. Existing installations retain their owned legacy state and pinned paths. `audiobook` remains a compatibility alias for `hamr`.

## Working on hamr

Bug reports are most useful with a source location or timestamp, the expected words and what you heard. The [release notes](RELEASE.md) list completed checks and work that still needs qualification.

```sh
uv sync --frozen --extra test
uv run pytest -q
uv build
python3 tools/check_release.py
```

[Open an issue](https://github.com/MimirLab/hamr/issues/new) · [Command guide](docs/USAGE.md) · [Linux manual](docs/man/hamr.1) · [Cast guide](docs/CAST.md)

<p align="center">
  <img src="docs/art/hamr-listener.png" width="215" alt="A raven in copper headphones perches on a book." />
</p>
