# hamr 0.2.1

hamr is the primary command and Python distribution. `audiobook` remains a compatibility command, and both `python -m hamr` and `python -m audiobook` work. New installations use hamr directories and `HAMR_HOME`; upgrades retain existing owned state and active legacy interpreter paths. The state schema remains 16.

All 45 reviewed cast voices are included as one final reference WAV and one transcript/metadata file each. Browsing plays the same reference used for cloning. Extra recordings, source archives and review files stay outside the product.

## Release checks

The cleaned product passed 577 regression tests, including release-content boundary checks. Hardware-specific research launchers and their tests remain outside the product. Actual offline fresh installation and an upgrade from 0.1.36 passed with paths containing spaces and Unicode. All 45 voices passed selection, queue and mpv null-output decoding checks. Both console/module entry points and a detached empty-queue worker passed.

A synthetic tone-control job interrupted during assembly recovered after the real installer upgrade with zero new takes. Targeted repair changed one conditioning key. CPU-only resume refused a missing take, repaired output assembled after supplying control PCM, and cleanup retained current pinned content and the previous publication. These fixtures qualify mechanics rather than narration quality.

The live branding upgrade preserved 25 existing job payloads byte for byte and retained narrator preferences. Detailed project receipts and book material remain private in the development workspace. Final wheel/source archives are checked against the product allowlist, reference hashes and private-path/key patterns before installation.

## Sharing and privacy

Use the checked wheel or source archive produced by `uv build` and `python3 tools/check_release.py`. The source archive includes an explicit list of product files, with no Git history. Private books, transcripts of those books, job databases, browser review state, project evidence and diagnostics are excluded. Only the character reference recordings and their exact transcripts deliberately supplied for the built-in cast are included as voice data.

Create a public repository from the checked source archive with fresh history. Do not push the existing research workspace/history. Product planning, rendering and recovery operate independently of development research services.

## Remaining qualification

Synthetic controls, decoder checks and transcript comparisons cannot certify expressive delivery or exclude every audible defect. Full-book listening and broader difficult-document qualification continue separately. No hardware-stability claim follows from this release. Missing narration remains a publication hold; the engine does not silently skip failed takes.
