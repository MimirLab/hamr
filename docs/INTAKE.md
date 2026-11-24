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
| Publishing XML and other structured sources | Resident readers accept selected FB2, FB3, DTBook, DocBook, JATS and TEI documents, saved mail and MHTML, CSV, Jupyter notebook text, XLSX worksheet contents, PPTX slide text, OPML, SVG text and subtitles. Content and reading order depend on the source structures present. |
| Archive containers | ZIP and TAR are built in. 7z and RAR use a compatible local libarchive. Encrypted or unresolved members appear as an intake error. |
| Unencrypted classic Kindle and DjVu | The Linux x86-64 and Windows x64 packages include a compact Kindle reader. Supported uncompressed Kindle Print Replica editions (AZW4) go through the PDF reader. The Linux DjVu reader uses available page text and runs raster OCR for pages without it. |
| Selected old office formats | A compatible installed LibreOffice Writer can produce an ODT reading in an isolated Linux worker. Hamr keeps the exact original. |
| LRF and unencrypted LIT ebooks | A compatible local Calibre can be reused. Hamr can acquire a pinned Linux x86-64 helper on first use. The archive is 230 MiB and the installed helper is about 906 MiB. |

Authored equations in EPUB, HTML, Word and OpenDocument share a reading for powers, subscripts, fractions and roots. Hamr preserves each equation in the original and marks ambiguous layouts for source review. Inline equations, ordinary table cells and linked notes can use the same reading.

`hamr agent capabilities` reports registered routes and host availability. The recognized file type and the exact variant it can read are different. Hamr reports an unreadable variant or missing helper with the saved source intact.

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

Finished books include the original file or a portable `original.hamr.zip` with source hashes and original image bytes. Source locations in `source.html` and `companion.json` identify the member, page, frame and crop. This command recovers the original member from a moved book folder:

```sh
hamr source BOOK_DIRECTORY LOCATION_ID --output page.jpg
```
