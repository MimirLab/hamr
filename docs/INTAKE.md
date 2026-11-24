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
| HTML, DOCX, ODT and RTF | Resident readers preserve supported paragraphs, headings, visible revisions, notes and simple tables. Selected complex objects that require a verified reading appear in source review. |
| TextBundle folders and TextPack archives | Hamr opens the descriptor's text payload and preserves its companion files. Version 1 uses Markdown. Version 2 can declare Markdown, plain text, HTML, or RTF. |
| mdBook folders and archives | Hamr follows the chapter links in the source directory's `SUMMARY.md` and reads included local material through the Markdown reader. A draft link stays in the companion. |
| Publishing XML and other structured sources | Resident readers accept selected FB2, FB3, DTBook, DocBook, JATS and TEI documents, saved mail and MHTML, CSV, Jupyter notebook text, XLSX worksheet contents, PPTX slide text, OPML, SVG text and subtitles. Content and reading order depend on the source structures present. |
| Archive containers | ZIP and TAR are built in. 7z and RAR use a compatible local libarchive. Encrypted or unresolved members appear as an intake error. |
| Unencrypted classic Kindle and DjVu | The Linux x86-64 and Windows x64 packages include a compact Kindle reader. Supported uncompressed Kindle Print Replica editions (AZW4) go through the PDF reader. The Linux DjVu reader uses available page text and runs raster OCR for pages without it. |
| Compiled HTML Help (`.chm`) | The resident Linux x86-64 reader follows the authored HTML table of contents. A linked topic outside the contents stays accessible in the companion. The plan reports unlisted topics and unreadable entries. |
| Selected old office formats | A compatible installed LibreOffice Writer can produce an ODT reading in an isolated Linux worker. Hamr keeps the exact original. |
| LRF and unencrypted LIT ebooks | A compatible local Calibre can be reused. Hamr can acquire a pinned Linux x86-64 helper on first use. The archive is 230 MiB and the installed helper is about 906 MiB. |

Authored equations in EPUB, HTML, Word and OpenDocument share a reading for powers, subscripts, fractions and roots. Hamr preserves each equation in the original and marks ambiguous layouts for source review. Inline equations, ordinary table cells and linked notes can use the same reading.

Structured XML such as JATS can supply the same readings through one authored MathML rendition. Hamr keeps the other renditions in the original article.

When a searchable PDF establishes an equation, raised script or scientific table cell from its printed layout, Hamr carries that reading into the same preparation step. The companion retains the exact glyph range and cell location. Source review identifies geometry that leaves the reading unresolved.

`hamr agent capabilities` reports registered routes and host availability. The recognized file type and the exact variant it can read are different. Hamr reports an unreadable variant or missing helper with the saved source intact.

For a CHM with a known legacy encoding, pass `--encoding windows-1252` or the actual source encoding. The resident CHM decoder runs locally with Bubblewrap. Its tested path uses a readable HTML contents file inside the CHM. Binary-only contents and popup controls without a local HTML target still need qualification.

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

Markdown, HTML, and EPUB notes can cite other notes. Hamr reads the linked text once per note chain and keeps the original definitions in the companion. Links between EPUB content files, simple table cell notes, and mdBook's rendered HTML footnote markers use this same preparation. In mdBook source projects, the first duplicate definition takes precedence, matching mdBook's renderer. If standalone Markdown or HTML contains conflicting definitions, Hamr shows the relevant source location for review.
