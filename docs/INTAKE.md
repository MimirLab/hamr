# From documents and scans to audiobooks

```sh
hamr open ~/Scans/MyBook --voice cast:nigel-thornberry --output ~/Audiobooks
hamr open ~/Downloads/novel.zip --voice cast:nigel-thornberry --output ~/Audiobooks
```

A folder or ZIP of loose page images is one book. Hamr uses an enclosed page manifest when present and otherwise sorts filenames naturally, so `page2.jpg` comes before `page10.jpg`. The plan lists the page sequence. An archive containing one recognized document opens that document. If several publications are available, Hamr prints their entry IDs. Repeat with `--entry ID` to select one. The selected contents determine what Hamr can read.

## Source readers

| Source | How Hamr reads it |
| --- | --- |
| PDF, EPUB, Markdown and plain text | The ordinary Hamr readers preserve the original source, planned narration and companion. Searchable PDFs retain native structure. For a known legacy text encoding, pass `--encoding windows-1252` or the actual encoding of the file. |
| Loose scans, individual images, TIFF and CBZ | The shared raster reader handles supported JPEG, PNG and TIFF page images, expands frames, and preserves crops and ordering. English OCR and its runtime are in the Linux x86-64 and Windows x64 packages. |
| Supplied OCR and ordered archives | Local hOCR, ALTO, PAGE XML, TSV and selected ABBYY XML sidecars can supply text for a scan. ABBYY XML uses one text page with original unrotated coordinates. A physical METS map or a local IIIF Presentation 3 sequence can name the pages. Binding and dimensions are checked before reuse. |
| HTML, OpenDocument and RTF | Resident readers preserve supported paragraphs, headings, visible revisions, notes and simple tables. Writer/Web OpenDocument files use the same reader, including `.oth` packages. Selected complex objects that require a verified reading appear in source review. |
| Word documents (DOCX) | Read editable DrawingML and VML text boxes in the selected Word compatibility branch. Hamr reads each story after its anchor paragraph and follows linked frames in their authored order. Visible revisions, notes, equations and simple tables use the shared preparation step. |
| TextBundle folders and TextPack archives | Hamr opens the descriptor's text payload and preserves its companion files. Version 1 uses Markdown. Version 2 can declare Markdown, plain text, HTML, or RTF. |
| mdBook folders and archives | Hamr follows the chapter links in the source directory's `SUMMARY.md` and reads included local material through the Markdown reader. A draft link stays in the companion. |
| GitBook folders and archives | Hamr follows the configured home page and summary, including section and nested page order. Each local space in a site can be opened as its own book. |
| reStructuredText and Sphinx projects | Hamr reads paragraphs, notes, equations and local includes in `.rst` documents. Open a Sphinx source folder or archive to follow its declared page order. A built single-file HTML export also works. |
| Scrivener projects and backups | Open the `.scriv` folder, its root `.scrivx` file, or the backup ZIP. Hamr reads the manuscript’s RTF documents in Draft binder order, using the binder labels for chapter navigation. |
| DTBook and NIMAS | Open DTBook XML, a NIMAS source folder or `.opf`, or the original ZIP. Hamr follows the package's declared reading order. Printed page numbers and the marked table of contents stay in the companion. |
| Text-bearing DAISY 3 | Open a DAISY 3 folder, `.opf` or ZIP. Hamr reads the DTBook passages in declared SMIL order. Keep the existing audio and synchronization files with the original package. |
| Text-bearing DAISY 2.02 | Open a DAISY 2.02 folder, `ncc.html` or ZIP. Hamr follows the synchronized HTML text and resolves authored footnotes through the usual note settings. Open a component SMIL to read just that component. |
| JATS research articles | Open a `.jats` or `.xml` article, a folder with the article and its figures, or a ZIP. Hamr reads the primary abstract, article text, linked notes, and authored figure descriptions. |
| TEI editions | Open the `.tei` or `.xml` edition, its folder, or a ZIP. Hamr reads the main text and keeps witness variants alongside the original. |
| BITS and NCBI Bookshelf | Open a BITS book or chapter as `.bits`, `.nxml`, or `.xml`. For NCBI Bookshelf, open the source archive or extracted folder to follow its table of contents. |
| DocBook manuals | Open a `.docbook`, `.dbk`, or `.xml` file, a source folder, or an archive. Hamr follows local chapter includes and reads the authored text, notes, descriptions, and simple tables. |
| Palm Database books | Open recognized PalmDOC, Weasel Reader zTXT, and unencrypted eReader books in `.pdb`, `.prc`, or an archive. [Palm Database intake](#palm-database-books) |
| Rocket eBooks | Open unprotected Rocket eBook version 2 (`.rb`) directly or inside an archive. [Rocket eBook intake](#rocket-ebooks) |
| Palm Markup Language | Open `.pml` files, `.pmlz` books, or a ZIP containing loose PML. The built-in reader preserves chapter order, footnotes, and sidebars. [Palm ebook intake](#palm-markup-language) |
| FictionBook | Open `.fb2` files and ZIPs, or open a FictionBook 3 file, ZIP, or extracted publication. Hamr follows linked notes and preserves verse lines and original assets. |
| AbiWord | Open `.abw`, `.awt`, gzip-compressed `.zabw` or `.abw.gz`, and bzip2-compressed `.bzabw` or `.abw.bz2`. The reader follows authored chapter styles and keeps linked notes, simple tables, text boxes, and illustration locations. |
| Jupyter notebooks | Open an `.ipynb` file or a ZIP containing one. The built-in reader follows saved cells and outputs in order. [Notebook intake](#jupyter-notebooks) |
| PowerPoint | Open a `.pptx` file, its extracted package, or a ZIP containing it. The built-in reader follows the saved slide order. [PowerPoint intake](#powerpoint-presentations) |
| OpenDocument presentations | Open an `.odp`, `.otp`, or `.fodp` file. Packaged presentations also open from extracted folders and ZIPs. [OpenDocument presentation intake](#opendocument-presentations) |
| Publishing XML and other structured sources | Resident readers accept selected DTBook, DocBook, JATS, TEI and BITS documents, saved mail and MHTML, CSV, XLSX worksheet contents, OPML, SVG text and subtitles. Content and reading order depend on the source structures present. |
| Archive containers | ZIP and TAR are built in. 7z and RAR use a compatible local libarchive. Encrypted or unresolved members appear as an intake error. |
| Unencrypted classic Kindle and DjVu | The Linux x86-64 and Windows x64 packages include a compact Kindle reader. Supported uncompressed Kindle Print Replica editions (AZW4) go through the PDF reader. The Linux DjVu reader uses available page text and runs raster OCR for pages without it. |
| Compiled HTML Help (`.chm`) | The resident Linux x86-64 reader follows the authored HTML table of contents. A linked topic outside the contents stays accessible in the companion. The plan reports unlisted topics and unreadable entries. |
| Selected old office formats | A compatible installed LibreOffice Writer can produce an ODT reading in an isolated Linux worker. Hamr keeps the exact original. |
| LRF and unencrypted LIT ebooks | A compatible local Calibre can be reused. Hamr can acquire a pinned Linux x86-64 helper on first use. The archive is 230 MiB and the installed helper is about 906 MiB. |

Authored equations in EPUB, HTML, Word and OpenDocument share a reading for powers, subscripts, fractions and roots. Hamr preserves each equation in the original and marks ambiguous layouts for source review. Inline equations, ordinary table cells and linked notes can use the same reading.

Open an `.xps` or `.oxps` file, its extracted package, or a ZIP containing it. Hamr follows the declared pages and authored stories. It preserves the original glyph runs, including words split across print runs. Named story cells use the shared table reader. Recovery can return an exact FixedPage member. On x86-64 Linux with glibc 2.36 or newer and Bubblewrap, the bundled renderer sends pages with no extractable text through the built-in OCR and compares readings at multiple resolutions. Source review names doubtful words, pages the renderer cannot read, and Unicode gaps on pages with mixed glyphs. [OpenXPS specification](https://ecma-international.org/publications-and-standards/standards/ecma-388/)

In Word, each editable text box keeps its exact XML location. Unresolved links between boxes and drawings without a readable text story or description may need source review.

Word, OpenDocument, HTML, EPUB and supported tagged PDFs use the same reading for merged and nested table cells. Hamr reads a merged value once, follows nested tables between the surrounding passages, and uses the headers that apply to each value. The companion retains original cell locations and distinguishes a covered position, a missing position, and an authored empty cell. Conflicting geometry or missing authored header targets appear in source review.

In a tagged PDF, Hamr matches table tags to visible text, follows the same table across pages, and keeps verified repeated print headers in the companion. Each reconstructed cell retains its original page and glyph ranges. When ownership cannot be established, the plan reports that it used the visible reading.

In a searchable PDF or scan, visible intersecting rules can establish cell boundaries and merged positions. Repeated alignment can also establish simple borderless tables with distinguishable headings and consistent number or date columns. Hamr reads these cells in order and keeps the native glyph or OCR locations.

When grouped labels or incomplete boundaries leave competing cell assignments, source review points to the affected table and retains its visible rows. Other ambiguous layouts can still use the ordinary page reading. Use the plan to check the reading where the page alone gives little structural evidence.

Structured XML such as JATS can supply the same readings through one authored MathML rendition. Hamr keeps the other renditions in the original article.

When a searchable PDF establishes an equation, raised script or scientific table cell from its printed layout, Hamr carries that reading into the same preparation step. The companion retains the exact glyph range and cell location. Source review identifies geometry that leaves the reading unresolved.

`hamr agent capabilities` reports registered routes and host availability. The recognized file type and the exact variant it can read are different. Hamr reports an unreadable variant or missing helper with the saved source intact.

For a CHM with a known legacy encoding, pass `--encoding windows-1252` or the actual source encoding. The resident CHM decoder runs locally with Bubblewrap. Its tested path uses a readable HTML contents file inside the CHM. Binary-only contents and popup controls without a local HTML target still need qualification.

## Numbered passages

Hamr uses authored ordered-list structure and consecutive printed paragraph labels to keep each number with its passage. A Roman chapter label in a heading sequence stays with the title. The source companion preserves the original numbering, and measurements and dates still use their own readings.

## Palm Database books

```sh
hamr open path/to/book.pdb
```

Hamr checks the book signature before selecting a reader. PalmDOC reads the whole body in saved record order and preserves its original text. Unencrypted eReader books with the 132-byte record header use the built-in Palm Markup Language reader for chapters, footnotes, and sidebars. The companion can recover the exact embedded picture or data record. The default encoding is Windows-1252. Supply `--encoding` for a file saved with a different known encoding.

Weasel Reader zTXT uses the same text preparation as PalmDOC and TXT. The built-in reader handles random-access records and books stored as one continuous compressed stream. Reader bookmarks and annotations are available in the source companion. Playback starts at the beginning and reads the book body.

Open a loose book or a supported member of a ZIP or TAR archive. If an eReader file uses a protected or older layout, supply a readable PML or EPUB export.

## Rocket eBooks

```sh
hamr open path/to/book.rb
```

Hamr starts at the declared body and follows a complete authored menu or unambiguous continuation links when available. It uses the saved directory order for the remaining cases. The source companion keeps the original book and can recover its embedded HTML, images, and index records. Supply `--encoding` if you know the book uses a specific older text encoding.

Legacy HTML contents lists with verified local links stay in the companion. Hamr can use a title from an illustration index when its link identifies the exact image. Titles for inline pictures stay with the source so the narration can read the surrounding sentence continuously. Images without an authored description appear at their source location for review. For a protected edition, open a readable export.

## Palm Markup Language

```sh
hamr open path/to/book.pmlz
```

Hamr reads authored chapter headings, linked footnotes and sidebars from `.pml` and `.pmlz`. The default source encoding is Windows-1252. For a file saved in another known encoding, supply `--encoding`.

A `.pmlz` edition can contain multiple text parts in one folder. Parts follow filename order. ZIPs containing separate loose `.pml` books prompt for a book. A loose `.pml` can use its local artwork folder. The companion preserves the original text ranges, note anchors and referenced artwork. Unlabelled illustrations can be reviewed at their original location.

## PowerPoint presentations

```bash
hamr open Lecture.pptx --voice cast:carl-sagan --output ~/Audiobooks
```

Hamr uses the saved slide and shape order, including text in nested groups. Slide titles become chapter headings. Presenter notes follow the slide text, and an identical passage on the same slide is voiced once. Set the existing notes policy to `companion` to keep presenter notes out of narration. Hidden slides and their notes stay in the original package.

Merged tables use their authored cell spans and header settings. Supported categorical charts read saved series labels and numeric values. Hamr reads authored image descriptions and sends a slide made from one eligible raster image through shared OCR. Unresolved charts and graphics appear in source review at their slide and package location.

Use a location ID from `source.html` with `hamr source BOOK_FOLDER LOCATION_ID --output FILE` to recover the exact slide, notes, chart, or embedded image part.

## OpenDocument presentations

```bash
hamr open Lecture.odp --voice cast:carl-sagan --output ~/Audiobooks
```

Open a presentation, template, or flat XML export through the same command. Hamr reads slide titles, grouped text, and presenter notes in the saved order. It uses one saved representation per frame and respects hidden slides, including visibility set through a page style. The existing notes policy can send presenter notes to the companion.

Writer documents and presentations share their table and paragraph preparation. Supported charts read the saved category and series ranges from the embedded table. Authored descriptions and text recovered from an eligible image-only slide use the same image preparation as PowerPoint.

Find exact package parts, or an embedded image inside flat XML, by using its location ID from `source.html`. Graphics and chart layouts that need another reading appear in source review at the affected slide.

## Jupyter notebooks

Open a saved Jupyter v3 or v4 notebook with `hamr open Lab.ipynb --voice cast:carl-sagan --output ~/Audiobooks`. The same command accepts a ZIP containing the notebook. Hamr reads the cells in saved order, including notes, tables, and supported equations. For display outputs it chooses the first usable Markdown, HTML, TeX equation, or plain-text reading. It reads saved standard output and keeps code, diagnostic messages, and graphics in the companion. A plot with authored alt text can be described using those words. The reader uses saved results and runs without Jupyter or a kernel.

Source review identifies the worksheet, cell, and output for a damaged entry or unresolved equation. To recover the exact bytes of an embedded PNG, JPEG, GIF, WebP, or SVG, use its location ID from `source.html`.

```bash
hamr source ~/Audiobooks/BOOK_FOLDER LOCATION_ID --output figure.png
```

Use the extension shown by the resource's media type. A cell location recovers the original notebook.

## Page order, recovery and originals

Use a `pages.hamr.json` file in a scan folder, or open the manifest directly, to set exact order. For example:

```json
{
  "schema": "hamr.page-sequence.v1",
  "title": "My Book",
  "pages": [
    {"path": "front/cover.jpg", "role": "cover"},
    {"path": "spread.tif", "frame": 0, "crop": [0, 0, 0.5, 1], "label": "1"},
    {"path": "spread.tif", "frame": 0, "crop": [0.5, 0, 1, 1], "label": "2"}
  ]
}
```

Frames start at zero. Crops are fractions of the EXIF-oriented image in left, top, right, bottom order. A page can include a `transcription` object with its relative `path` and optional `format`. For a local IIIF bundle, relative image references resolve within the package. Use the `hamr:localResources` object to map absolute identifiers to local relative files.

Use `hamr plan PATH --offline --json` to check order, coverage and planned speech without downloads. `hamr resume JOB` continues from the captured source after interruption. `hamr repair JOB --reimport` reruns intake against the same original. A source issue names the page or member whose content needs review.

Finished books include the original file or a portable `original.hamr.zip` with source hashes and original image bytes. Find the location ID in `source.html`. `companion.json` records the page, package path, frame or crop where available. These commands recover the exact original file or enclosing archive member, including after moving the book folder.

```sh
hamr source BOOK_DIRECTORY LOCATION_ID --output page.jpg
hamr source PDF_BOOK_DIRECTORY LOCATION_ID --output original.pdf
```

You can also use the passage ID from the companion link. If an older saved job has no source location index, rebuild its companion with `hamr repair JOB --repackage` followed by `hamr resume JOB --no-synthesis`.

A TextBundle folder is saved as a portable TextPack. Open `source.html` and follow the illustration or attachment label for its location ID. Recover individual package files with the same `hamr source` command.

Open an mdBook project folder or its source archive directly. Hamr uses `book.toml` to find a custom source directory and looks in `src/` by default. It preserves chapter order, nested chapter labels, unlisted source files and local includes. If an include points outside the project folder, open or archive the parent directory containing the project and its referenced files. For content produced by custom preprocessors, build with mdBook and open the generated `print.html`.

Open a GitBook space folder or its source archive. Hamr reads `.gitbook.yaml` for the content directory, home page and summary. It reads the local pages in summary order and uses natural folder order when a configured space has no summary. Open a folder or archive with `gitbook-docs.yaml` to choose among the local spaces. External links stay in the source companion. If the summary points to generated content absent from the source, such as a registered API reference, the plan reports its location. The local reader is part of the normal installation. [GitBook content configuration](https://gitbook.com/docs/docs-as-code/git-sync/content-configuration)

Open a standalone reStructuredText file or its archive. Hamr reads local `include` directives in order, including ranges selected by line or marker. For a loose file, keep its includes in the same folder or below it. Use an archive of the common parent when includes refer to sibling folders. Hamr saves the loose document and the files it used together. The finished companion can recover each complete included file even if only a few of its lines were read. For a known legacy encoding, pass `--encoding windows-1252` or the actual source encoding. An include can declare its own encoding. [Docutils include directive](https://docutils.sourceforge.io/docs/ref/rst/directives.html#include)

For a Sphinx project, open the folder containing `conf.py` and the source pages, or an archive of that folder. Hamr reads the literal project settings and follows `toctree` where it appears in the narrative. Relative and project-root includes resolve from the saved files. Missing pages and directives that require a build appear in the plan with source locations. For projects that produce pages with Python extensions or another markup parser, open the author-built `singlehtml` export. The reStructuredText reader installs with Hamr. [Sphinx table of contents](https://www.sphinx-doc.org/en/master/usage/restructuredtext/directives.html#directive-toctree)

For Scrivener, keep the root `.scrivx` binder beside the project’s `Files` directory. The reader handles the UUID-based layout and the older numeric-ID layout. It follows each item’s **Include in Compile** checkbox within Draft or Manuscript. Top-level items in that branch supply chapter boundaries. Paired style controls stay in the source record while the narration reads their text. Separate document notes, synopses, research and snapshots are kept with the portable original in the finished folder. Share the M4B by itself when you want to share only the narration. For an edition with custom compile transformations, compile an EPUB or PDF and open that edition in Hamr. [Scrivener compile guide](https://www.literatureandlatte.com/docs/Scrivener_Manual-Mac.pdf)

Open a NIMAS ZIP as one book. Its package file selects the DTBook text and connects its illustrations and title-page PDF to the companion. You can also open the extracted folder, the `.opf` file, or the DTBook XML by itself. Linked notes, authored image descriptions and supported MathML use the same speech preparation as other structured documents. The reader runs locally with the normal Hamr installation. [NIMAS technical specification](https://aem.cast.org/nimas-nimac/nimas-technical-specification)

A text-bearing DAISY 3 package pairs DTBook text with its SMIL sequence. Open the ZIP, the `.opf` file or the source folder. Hamr preserves authored repeats and keeps synchronized page markers in the companion. When a cue points to missing text, the plan identifies its SMIL member and line. Use the text-bearing edition for synthesis and a DAISY player for an audio-only edition. [DAISY 3 specification](https://www.daisy.org/z3986/2005/Z3986-2005.html)

For a DAISY 2.02 book, open the ZIP, source folder or `ncc.html`. Hamr follows the component SMIL sequence and uses the authored HTML for narration. Navigation, page labels and existing media remain available in the companion. Open a component SMIL directly to read that section. Use a full-text edition for synthesis and a DAISY player for an audio-only or partially transcribed edition. [DAISY 2.02 specification](https://daisy.org/activities/standards/daisy/daisy-2/daisy-format-2-02-specification/)

Open a JATS article with `hamr open paper.xml`. Hamr uses the main article and primary abstract, and reads notes at their exact references. Translated abstracts and alternate articles remain in the companion. Declared local graphics and supplementary files travel with the saved article. The plan identifies graphics that still need a description by XML location. [JATS Tag Library](https://jats.nlm.nih.gov/archiving/tag-library/1.4/)

Open a TEI edition with `hamr open edition.tei`. The narration uses an explicit lemma or a uniquely tagged correction, regularization, or expansion. It keeps the base text when a separate apparatus links to it. The companion records witness readings with their original locations. If a passage has competing inline readings and no selected text, supply the verified reading for that block. [TEI critical apparatus](https://tei-c.org/release/doc/tei-p5-doc/en/html/TC.html)

Open a BITS book or chapter with `hamr open chapter.nxml`. Keep referenced XML and graphics with the selected file, or open a ZIP of the publication. For an NCBI Bookshelf source archive, open the archive directly with `hamr open book.tar.gz`. The reader follows its chapter table of contents and keeps accompanying PDFs in the source companion. Local XML XIncludes can select a whole file or a unique element ID. A missing chapter or unsupported include points to the exact source location in the reading plan. [NLM BITS Tag Library](https://jats.nlm.nih.gov/extensions/bits/tag-library/2.2/) · [NCBI Bookshelf source archives](https://www.ncbi.nlm.nih.gov/sites/books/about/ftp/)

Open a DocBook manual with `hamr open manual.xml`. Keep the XML files and images referenced by the book in the same source tree, or open its ZIP directly. Hamr follows local XML includes, reads author-written descriptions, and keeps book metadata with the source. Complex tables, missing figure descriptions, and unresolved publisher variables appear in the plan at their XML locations. For a source project that generates text through a publisher-specific build, open its prepared DocBook, HTML, or EPUB edition. [The DocBook Definitive Guide](https://tdg.docbook.org/)

Use `hamr open book.fb3` for FictionBook 3. A ZIP containing the same publication and its extracted folder open the same way. Hamr follows the package’s text and image relationships, reads the authored notes and verse, and keeps the exact original files with the audiobook. A missing illustration description appears at its source location. FictionBook 2 takes the same note and verse path from a loose `.fb2` or an archive member. [FictionBook 3 specification and examples](https://github.com/gribuser/FB3)

Use `hamr open manuscript.abw` for AbiWord, or open an archive containing the document. Hamr reads the main text and keeps recurring page headers and footers in the source companion. A dynamic field without a stored value, an unlabelled illustration, or a table requiring human interpretation is located in the plan. For a tracked manuscript, accept the intended revisions in the editor and save the reading copy. [AbiWord source format](https://github.com/AbiWord/abiword/blob/6940426ea7fd3810f1aa5477fa93c2d4278dea7d/docs/AbiWord_DocumentFormat.abw)

Use `hamr open manuscript.rtf` to read a rich-text document. Hamr follows explicit and inherited outline levels, places footnotes and endnotes with their references, and reads text boxes at their source position. It applies the original document and font encodings. In Scrivener, the binder order and labels control chapter navigation. The plan identifies missing note text and tables with merged or nested cells that need a verified reading.

Markdown, HTML, and EPUB notes can cite other notes. Hamr reads the linked text once per note chain and keeps the original definitions in the companion. Links between EPUB content files, simple table cell notes, and mdBook's rendered HTML footnote markers use this same preparation. In mdBook source projects, the first duplicate definition takes precedence, matching mdBook's renderer. If standalone Markdown or HTML contains conflicting definitions, Hamr shows the relevant source location for review.
