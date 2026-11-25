# hamr 0.4.9.dev89

- Open HEIC and HEIF still photos and AVIF scans directly, in scan folders, or in ZIPs. Hamr follows the designated primary HEIF image and its display orientation, and keeps the exact original file with the book.
- Raise the page image limit to 16 megapixels so standard 12-megapixel phone photos can go straight into OCR.

# hamr 0.4.9.dev88

- Read English QuarkXPress 3.1, 3.2 and 3.3 publications from files, folders and ZIPs. Follow saved story chains across pages, keep distinct repeats, and retain the original publication and source byte ranges.

# hamr 0.4.9.dev87

- Read Adobe PageMaker publications with the PageMaker 6 and 6.5–7 record layouts. Follow the stored byte ranges of threaded text across pages, keep independently authored repeats, and preserve the original publication in the finished book.

# hamr 0.4.9.dev86

- Open UTF-8 FrameMaker MIF manuscripts directly or in folders and ZIPs. Hamr follows saved body flows, reads anchored text and footnotes, and keeps exact source locations for unresolved figures or variables.

# hamr 0.4.9.dev85

- Open Scribus SLA and SLA.GZ manuscripts from files, folders, and ZIPs. Follow saved text-frame links once, keep paragraph and style cues, and recover the exact manuscript and local artwork from the finished book.

# hamr 0.4.9.dev84

- Open InDesign IDML exports and InCopy ICML stories directly, in folders, and in ZIPs. Follow article order and page placement, read threaded text once, and retain the original story and layout parts with the audiobook.
- Read floating sidebars and illustrations beside their anchor paragraph so they do not interrupt the sentence.

# hamr 0.4.9.dev83

- Open historical Rails Guides Textile sources. Hamr reads the guide's title, introduction, and body, and preserves the authored programming examples for the usual code settings.
- Keep an embedded HTML example from taking over the title or reading order of an enclosing HTML fragment.

# hamr 0.4.9.dev82

- Open Textile manuscripts from a `.textile` file, folder, or ZIP. Numbered footnotes and named notes use the usual note settings. Local illustrations stay with the source, and code blocks follow the selected reading mode.
- Read a simple table's authored caption before its rows. The source companion keeps the caption at its original location.

# hamr 0.4.9.dev81

- Open DAISY Z39.98 authoring books (ZedAI) from `.xml`, `.zai`, a folder, or a ZIP. Hamr follows authored talking-book alternatives, preserves verse lines and linked notes, and reads the author's descriptions of figures. Index entries follow the ordinary reference setting.
- When opening a folder of publishing XML, keep relative links to chapters, illustrations, and descriptions inside the selected folder. Hamr now resolves links across its subfolders the same way it does inside a ZIP.

# hamr 0.4.9.dev80

- Open original OpenOffice.org Writer templates (`.stw`) and master documents (`.sxg`). For master sections, Hamr follows the older Writer links to nearby chapters and retains the exact files for recovery.

# hamr 0.4.9.dev79

- Read unpacked Writer master documents and linked chapters from folders and ZIPs. Hamr follows Writer's section order, keeps the original package parts for recovery, and recognizes an unrelated scan as a separate publication.

# hamr 0.4.9.dev78

- Read unpacked Writer document packages from a folder or ZIP. Hamr follows the authored text and table structure and retains each original package part for recovery.
- Open OpenOffice.org 1.x Writer documents (`.sxw`) through the built-in OpenDocument text reader. Hamr recognizes both a media marker and a manifest declaration, and retains the saved figure numbers, notes, text frames and list structure.
- Preserve explicitly continued list numbering across an intervening bulleted list in Writer documents.

# hamr 0.4.9.dev77

- Open Writer templates (`.ott`) as text. Templates use the same reading and source companion as ordinary Writer documents.
- Open a Writer master document (`.odm`), its folder of chapters, or a ZIP containing both. Hamr reads saved sections and follows local chapter references in Writer's order, including named sections. The companion retains the source files used for reading. Missing or unreadable chapters appear at their place in source review.

# hamr 0.4.9.dev76

- Read tracked row and column deletions in LibreOffice Writer ODT and Flat ODF. Surviving merged cells, inserted rows, and deliberately blank positions follow the visible table. The source companion records the deleted locations.

# hamr 0.4.9.dev75

- Read tracked paragraph joins and table revisions in Word documents. Joined words stay intact, and deleted rows and cells stay out of narration. The companion retains the original Word source and records the affected locations.

# hamr 0.4.9.dev74

- Read the current text of tracked RTF edits. Insertions and moved text are read in their final locations, and deleted words, note anchors, and breaks are omitted. The original file remains available from the companion.
- Reimport can repair planned text after automatic retakes have tried the old wording. Unchanged take selections and the attempt history are kept. User-selected takes still require an exact match.
- Recompute automatic narration directions when reimport changes their source. Passage-specific directions supplied by the reader remain bound to their exact source.

# hamr 0.4.9.dev73

- Preserve inline bold and italics from RTF documents and Scrivener manuscript text. Named style inheritance and local resets follow the exact words into the shared narration planner.

# hamr 0.4.9.dev72

- Carry bold, italic and oblique character styles from OpenDocument text and presentations into the same narration planner as Word, EPUB and HTML. Hamr follows inherited and nested text styles, respects explicit resets and keeps the style definition linked to its exact source words.

# hamr 0.4.9.dev71

- Read simple text examples from the verified lshort LaTeX package. The generated result is read in order and its exact source stays linked beside the printed code. The code is quiet in listen mode and available to the procedure reading. Examples that need a separate compilation still appear in source review.
- Preserve explicit emphasis and font-shape marks in HTML-derived documents and clearly defined Word character styles. Eligible dialogue uses the same expressive narration in both.
- Read nonbreaking digit groups with compound units as one measurement. For example, 299 792 458 m/s reads as a single speed.

# hamr 0.4.9.dev70

- Speech review accepts an exact filename written compactly by a verifier when the planned reading spells out its extension at the same location. Different names, extensions and paths still require review. Run `hamr qa JOB --recheck` to apply the comparison to saved recordings.

# hamr 0.4.9.dev69

- Keep two short displayed commands inside their written either-or sentence. This works in structured source and in native PDF with matching framed, fixed-pitch lines. The companion keeps the exact locations of the pieces. Standalone code blocks follow the selected code policy.
- Recognize declared paragraph styles for preformatted text and source code in Word and OpenDocument. Consecutive code paragraphs within the same source container retain their lines as one quiet companion reference in listen mode.

# hamr 0.4.9.dev68

- Source review, interrupted jobs and speech-review findings print follow-up commands for the Hamr installation and state folder that opened the job. This also works with custom state paths that contain spaces or apostrophes.

# hamr 0.4.9.dev67

- Open LaTeX chapters with inline verbatim commands and locally declared code readers. A code example containing `\begin` or unmatched braces stays bound to its own source range. Formatting-only environments retain their prose, and verified code displays follow the ordinary companion policy. Generated example input stays recoverable and its rendered output has a scoped source-review entry. [LaTeX intake](docs/INTAKE.md#latex)

# hamr 0.4.9.dev66

- Read supported LaTeX text commands declared in the manuscript or a local style file. Hamr follows fixed argument lists, optional text and star branches when their visible output can be read from the source. It keeps the original style files and command locations in the companion. Generated content that needs verification appears in source review. [LaTeX intake](docs/INTAKE.md#latex)

# hamr 0.4.9.dev65

- Read primes and bound sums and products in authored equations. TeX, MathML and Word equations use the shared math reader. Word keeps the author's limit visibility.
- Read equations that use local LaTeX command definitions with literal argument lists. The plan retains both the equation and the definitions it used. [LaTeX intake](docs/INTAKE.md#latex)
- Follow linked endnotes written as native list items in an HTML or EPUB endnote collection. [Document notes](docs/INTAKE.md#linked-notes)

# hamr 0.4.9.dev64

- Read explicitly introduced lists of symbols aloud, such as “dollar sign, ampersand, and number sign.” The plan preserves the exact source characters. Ordinary prices, conjunctions, and protected code keep their established readings.
- Read supported inline dollar-delimited Markdown equations through the shared expression reader. Currency and code examples retain their usual handling. [Document intake](docs/INTAKE.md#source-readers)

# hamr 0.4.9.dev63

- Open a LaTeX manuscript, source folder, or archive with the built-in reader. Hamr follows literal chapter includes and reads supported notes, lists, equations, and tables through the shared planner. Text that spans an input file stays together in narration and points back to its source locations. [LaTeX intake](docs/INTAKE.md#latex)

# hamr 0.4.9.dev62

- Honor explicitly decorative EPUB images while keeping missing descriptions and damaged text in source review. Retain an authored em-dash cutoff at the end of a spine item before reviewing damage in the next item.
- Recognize a scanned PDF bibliography when the heading and author-year citation structure agree in the native and visual observations. Respect the existing reference-reading setting. Flag corroborated repeated page sequences and keep both scans in the source.
- When a source-bound caption decision resolves a PDF reading-order hold, clear that hold from the adjoining prose. Keep independent OCR findings.
- Compare British and American spellings such as “behaviour,” “traveller,” and “defence” without treating them as changed speech. Preserve the original verifier text.

# hamr 0.4.9.dev61

- Review flagged speech in a local browser with source text, the intended reading, verifier transcripts, and exact recordings. Filter or sample recurring disagreements, see likely missing speech and changed values first, and revisit saved decisions. Use `hamr review JOB` for a short summary and playback and export commands. [Speech review](docs/USAGE.md#review-saved-speech)
- Recheck saved transcripts after a comparison fix with `hamr qa JOB --recheck`. Matching spoken and written line ranges compare by their endpoints. Changed numbers, missing words, and listening findings still need inspection.
- Plan natural time readings in explicit narration contexts, such as “woke at 7:43.” Report a planned reading problem separately from a faulty recording so a retake starts with the corrected words.

# hamr 0.4.9.dev60

- Open Apple Pages documents through the bundled Linux reader. Tested XML and IWA editions preserve tables, linked notes and editable layout stories through the usual audiobook pipeline. Open a `.pages` file, a package folder, or one inside an archive. [Apple Pages intake](docs/INTAKE.md#apple-pages)

# hamr 0.4.9.dev59

- Read keyboard instructions in prose. Named chords such as `Ctrl+Shift+P` keep their modifiers together, and contextual legacy shortcuts such as `^OD` read as sequential keys. The plan keeps each printed shortcut and its source range.
- Compare equivalent modifier spellings, written email addresses, and common English spelling variants during speech checks. Changed keys, address fields, and words still require review.

# hamr 0.4.9.dev58

- Open WordStar 7 for DOS manuscripts directly, from a folder, or within an archive. The built-in reader follows saved paragraph styles and notes, restores prose across soft line endings, and preserves the original byte ranges. [WordStar intake](docs/INTAKE.md#wordstar-7)
- Treat a written apostrophe before the same spoken s suffix as a spelling difference during speech verification. A missing suffix or changed word still needs inspection.

# hamr 0.4.9.dev57

- Compare `50%`, “fifty percent” and “fifty per cent” as the same spoken rate. Preserve a missing unit or changed value. Match plural SHA spellings and equivalent Git terminology during speech checks.
- Compare “open parentheses” with “open parenthesis” while preserving missing or reversed grouping markers.

# hamr 0.4.9.dev56

- Read explicitly introduced caret equations through the shared math reader. Ordinary text, HTML and native PDF keep the original expression and speak its operators and powers.
- Preserve verified boxed PDF terminal transcripts in the companion under the ordinary code policy. Read a native PDF folio as page furniture when its isolated printed number matches the declared page label.
- Read verified Asciidoctor PDF Tip panels without sending the surrounding native text through OCR. Retain the icon, divider and source coordinates.
- Treat matching SHA spellings such as `SHA-1` and `SHA1` as the same identifier in speech checks. A changed hash variant or nearby quantity still needs inspection.

# hamr 0.4.9.dev55

- Read styled numeric powers aloud when the surrounding expression establishes the role. AsciiDoc, Markdown, HTML, EPUB, Word, OpenDocument and native PDF pass their source evidence to the same reader. Linked notes still follow their target and uncertain markers appear in source review.
- Keep adjacent raised digits on their native PDF line when size, position and glyph order identify the base. Original glyph positions remain available in the plan.
- Keep native PDF text when a verified information icon repeats an adjacent written Note label. The original icon remains recorded with its page coordinates.

# hamr 0.4.9.dev54

- Open AsciiDoc manuscripts and local chapter includes through shared reading preparation. Hamr reads tables, captions, callouts and footnotes, and keeps the included originals recoverable. [AsciiDoc intake](docs/INTAKE.md#asciidoc)

# hamr 0.4.9.dev53

- Open Org manuscripts from a file, folder, or archive. The built-in reader follows the authored export selection, notes, tables, lists and local includes through shared reading preparation. Complete included files remain recoverable. [Org mode intake](docs/INTAKE.md#org-mode)
- Read the written task label `TODO` as “to-do” in ordinary prose. Saved plans and literal identifiers preserve their own reading.

# hamr 0.4.9.dev52

- Open tested ClarisWorks word processing and WriteNow documents with the bundled Linux reader. They pass through the same text preparation as OpenDocument. Hamr recognizes extensionless originals and retains the source in the companion. [Classic Mac intake](docs/INTAKE.md#classic-mac-writing-documents)
- Read editable text stories and their tables from OpenDocument text frames. Preserve the source positions of merged cells during classic Macintosh conversion.

# hamr 0.4.9.dev51

- Read automatic list labels in OpenDocument Writer and Flat OpenDocument text. Hamr follows saved item starts and explicit continuation links and uses the same spoken labels as Word and HTML.
- Read RTF’s saved list marker when its declared type and displayed text agree. The plan retains the original byte range. An unresolved numbered item appears in source review.
- Treat equivalent legacy apostrophes, initials, and clock spellings as the same spoken words during verification. Preserve changes in meaning and the original recognition record.

# hamr 0.4.9.dev50

- Read saved automatic numbering in Word documents and Roman markers in HTML and EPUB. The plan keeps the original marker notation, start values, and supported nesting and restarts.
- Keep a compound description such as “she whispered angrily” attached to that speaker’s dialogue. Continuing quotes keep their delivery when plain narration follows.
- Recover multiword labels in searchable PDF tables when native print runs and repeated alignment agree. Preserve every fragment when column order needs review.
- Report the exact disputed words when required speech checks find a mismatch. A low overall error rate can still hide a word that changes the meaning. Automatic retakes for a residual word change require independent observations of the same change.
- Check that Bubblewrap can start the required isolation before reporting a local converter ready.

# hamr 0.4.9.dev49

- Open tested MacWrite, MacWrite Pro and Nisus Writer documents with the bundled Linux reader. Read their text, linked notes and tables through the ordinary OpenDocument preparation.
- Preserve both original files for a matched AppleDouble pair, including inside ZIP archives. Verify all three BinHex checksums and keep unidentified or incomplete fork material out of narration.

# hamr 0.4.9.dev48

- Read printed clocks with seconds and an explicit AM or PM, and related identifiers that share a printed prefix, through the shared contextual reader. Preserve their original spelling and exact field values in the plan.
- Distinguish calendar ordinals from time units in speech QA. Compare exact identifier groups and time fields across common transcript spellings. A changed digit, quantity sign, or AM/PM still needs inspection.

# hamr 0.4.9.dev47

- Open tested WordPerfect documents with the resident Linux reader. It uses the shared OpenDocument narration path and keeps the exact original. [WordPerfect intake](docs/INTAKE.md#wordperfect-documents)
- Open Flat OpenDocument text directly, from a folder, or inside an archive. Body text and inline MathML use the same reader as packaged OpenDocument Writer documents.

# hamr 0.4.9.dev46

- Open Shanda Bambook ebooks through the shared narration pipeline. Follow the authored chapter directory and retain exact embedded chapter files and artwork. [Bambook intake](docs/INTAKE.md#bambook-ebooks)
- Bind Rocket eBook artwork when its reference differs only by letter case and identifies one embedded file.

# hamr 0.4.9.dev45

- Open unprotected Rocket eBooks through the built-in HTML pipeline. Preserve the declared first page, original chapter and image records, and linked source locations in the companion. [Rocket eBook intake](docs/INTAKE.md#rocket-ebooks)
- Keep verified legacy HTML contents lists and misplaced document metadata out of the spoken chapter text. Links to named HTML anchors resolve to their source passages.
- Recover legacy HTML with deeply nested unclosed paragraphs when an independent parse preserves every word in order.
- Reuse authored illustration titles from verified local index links. Inline image titles stay in the companion so surrounding sentences remain continuous.

# hamr 0.4.9.dev44

- Read Weasel Reader zTXT books directly or from archives. The built-in reader handles both serial and random-access compression and preserves the original text, reader bookmarks, and annotations. Weasel Reader books use the same narration preparation as ordinary text. [Palm Database books](docs/INTAKE.md#palm-database-books)

# hamr 0.4.9.dev43

- Read ordered-list numbers and repeated printed paragraph labels as part of the narration. Recognized Roman chapter labels stay with their headings, and the original notation remains in the companion. Lists and numbered paragraphs use the same preparation across document readers.

# hamr 0.4.9.dev42

- Read PalmDOC and unencrypted eReader books natively. PalmDOC reads the complete text across record boundaries, including saved books with a reading bookmark. eReader uses shared chapters and notes, with exact record and artwork recovery. [Palm Database books](docs/INTAKE.md#palm-database-books)

# hamr 0.4.9.dev41

- Open Palm Markup Language books and PMLZ archives with the built-in reader. Authored chapters, footnotes, and sidebars use shared narration, while the companion keeps original source locations and images. [Palm ebook intake](docs/INTAKE.md#palm-markup-language)

# hamr 0.4.9.dev40

- Open OpenDocument presentations, templates, and flat XML directly. Hamr follows saved slides and presenter notes, reads authored tables and supported chart data, and uses resident OCR for eligible image-only slides. Recover exact package members and embedded flat XML images. [OpenDocument presentation intake](docs/INTAKE.md#opendocument-presentations)

# hamr 0.4.9.dev39

- Turn a PowerPoint deck into chapters with its saved slide order, grouped text, presenter notes, merged tables, and supported chart data. Image-only slides can use Hamr's built-in OCR. Recover exact slide, note, chart, and image parts from the companion. [PowerPoint intake](docs/INTAKE.md#powerpoint-presentations)

# hamr 0.4.9.dev38

- Open Jupyter v3 and v4 notebooks directly or in a ZIP. Hamr reads saved cells in order, speaks a single reading of each usable result, and keeps code and graphics in the companion. Source recovery can return exact embedded images. [Notebook intake](docs/INTAKE.md#jupyter-notebooks)
- Speech verification resolves a documented spelling variation for the name SymPy in matching context and retains the unedited recognition report.

# hamr 0.4.9.dev37

- Read XPS and OpenXPS pages printed as images or glyph outlines using the bundled renderer and OCR on compatible Linux hosts. Hamr compares readings at multiple resolutions, keeps unresolved text at its original page location, and preserves exact page parts for later recovery. [Document intake](docs/INTAKE.md#source-readers)

# hamr 0.4.9.dev36

- Open text-bearing XPS and OpenXPS packages directly, from an extracted folder, or inside a ZIP. Hamr follows declared pages and page stories, reads table spans, and preserves exact page parts in the companion. Pages with graphics and no text report where a visual reading is needed. [Document intake](docs/INTAKE.md#source-readers)

# hamr 0.4.9.dev35

- Searchable PDFs and scans can read simple borderless tables with aligned headings and repeated numeric or date columns. Detected tables with competing grouped readings appear at their page location in source review. Native PDF extraction caches follow updates to the table reader. [Table intake](docs/INTAKE.md#source-readers)

# hamr 0.4.9.dev34

- Searchable PDFs and scanned pages can read tables with a visible grid, including merged cells. The companion keeps each cell's page and glyph or OCR location. Hamr checks possible OCR border characters against a separate crop of the cell. [Table intake](docs/INTAKE.md#source-readers)

# hamr 0.4.9.dev33

- A tagged PDF can use the shared table reader when its tags identify the visible text. Hamr can read merged and nested cells, continue one table across pages, and retain repeated print headings in the companion. Conflicting tags preserve the visible reading or identify the table for source review.
- Writer/Web OpenDocument packages, including `.oth` files and copies inside ZIPs, open as documents. Their embedded previews stay with the original.
- Long structured tables use shorter synthesis requests that end at complete sentences. Speech QA matches spoken leading-zero identifiers with the same printed digits and still checks each digit for errors.

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
