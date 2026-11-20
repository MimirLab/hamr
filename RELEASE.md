# hamr 0.2.3

`hamr setup --profile vulkan-f16` now dispatches directly to backend setup. It no longer tries to open a saved listening profile with that name. Missing saved listening profiles and missing files identify their paths.

All F16/Q8/Q6/Q4 CPU and Vulkan model choices are built into the application, together with Q8 CPU text encoding: nine implemented profiles. Selecting one downloads its pinned runtime and weights; unrelated models are not acquired. The CPU text-encoder profile now supplies the required integer generation-stream count.

Plain `hamr doctor` queries resident graphics hardware, Vulkan devices and available memory, then explains a suggested starting profile. The isolated, bounded Vulkan inventory requires neither vulkan-tools nor a speech model, creates no logical device and submits no GPU work. `hamr setup` offers guided selection in a terminal; explicit `--profile` remains available for scripts. JSON reports expose observations, compatibility reasons, download sizes and testing status. Unknown hardware, shared memory and multiple GPUs carry explicit uncertainty.

hamr is the primary command and Python distribution. `audiobook` remains a compatibility command, and both `python -m hamr` and `python -m audiobook` work. New installations use hamr directories and `HAMR_HOME`; upgrades retain existing owned state and active legacy interpreter paths. The state schema remains 16.

All 45 reviewed cast voices include their original conditioning reference/transcript and approved introduction. `hamr voice sample CHARACTER --play` now plays the same custom introduction as the cast guide, offline before model setup. The wheel bundles all 45 previews with a verified manifest; source checkout and installed playback use identical MP3 bytes. Changing previews preserves voice-conditioning identities and existing job pins. Candidate recordings, source archives and review files stay outside the product.

The GitHub cast guide now includes **45 custom introductions**, reviewed by the user, with their exact lines and selection commands. The selected recordings total 500.4 seconds; public MP3 previews occupy approximately 6 MB. Every export matched the selected reviewed take's hash, retained its full duration and passed mpv decoding. The source archive and application wheel include these public previews and their transcript/checksum manifest alongside the final cloning references.

## Release checks

The product passed **635 regression tests**, including 40 focused profile/setup checks. Tiny pinned-asset fixtures exercise all nine CLI setup routes through acquisition, size/SHA-256 verification, extraction and activation after a simulated health check. These tests also check that memory, driver and generic startup failures preserve active settings; unsupported platforms and catalog-only engines stop before acquisition. Fixture health is not real speech qualification. Public introduction checks enforce the exact cast, hashes and manifest, reject extra/missing audio and enforce that every declared introduction is included in the installed wheel.

The real built-in inventory on a Radeon RX 6800 XT identified hardware Vulkan and approximately 15.4 GiB free VRAM, suggesting F16 with reasons. Neither vulkaninfo nor nvidia-smi was installed. No speech model was loaded for that probe. Linux GPU execution has prior real F16 long-narration/clone evidence and Q8 short-control evidence on this GPU; that does not establish every GPU vendor or quantized profile.

Offline fresh installation and the live upgrade passed with both console/module entry points and all 45 bundled references. The live upgrade retained all 25 saved job payloads, preferences and active model settings unchanged. Release archives passed the product allowlist, reference hashes and private-path/key checks. Hardware-specific research launchers remain outside the product.

Existing 0.2.1 qualification includes a synthetic tone-control job interrupted during assembly that recovered after installer upgrade with zero new takes. Targeted repair changed one conditioning key. CPU-only resume refused a missing take, repaired output assembled after supplying control PCM, and cleanup retained current pinned content and the previous publication. These fixtures qualify mechanics rather than narration quality. All 45 references previously passed mpv null-output decoding, selection and queue checks.

Detailed project receipts and book material remain private in the development workspace. The state schema remains 16; no job, reference or model migration is required by this update.

## Sharing and privacy

Use the checked wheel or source archive produced by `uv build` and `python3 tools/check_release.py`. The source archive includes an explicit list of product files, with no Git history. Private books, transcripts of those books, job databases, browser review state, project evidence and diagnostics are excluded. The application wheel includes the final character reference recordings, exact transcripts and approved generated introductions deliberately supplied for the built-in cast. The source archive keeps the canonical introduction files under `docs/listen`; the wheel maps those same files into its packaged previews.

The public product repository has clean history. Do not push the separate research workspace/history. Product planning, rendering and recovery operate independently of development research services.

## Remaining qualification

Q6, Q4, CPU text encoding and all four CPU-only choices are **implemented but experimental**: real startup, complete speech output, memory peaks and comparative listening remain to be qualified for each. RAM ranges in the guide are planning estimates; they are not measured peaks. CUDA/MLX and other research entries have no setup adapter. The [profile guide](README.md#which-profile-should-i-choose) distinguishes these states.

To qualify an experimental profile without changing active book settings, select its ID in an isolated state. This example uses Q6; repeat with each experimental ID on hardware with sufficient memory:

```sh
trial=$(mktemp -d)
printf '%s\n' 'The fox waits beneath the old oak tree. Do not open the gate. Fifty-seven birds return at dawn, and four remain beside the river.' > "$trial/control.md"
hamr --home "$trial/state" doctor --json > "$trial/doctor.json"
hamr --home "$trial/state" setup --profile vulkan-q6 --json > "$trial/setup.json"
hamr --home "$trial/state" queue "$trial/control.md" --voice cast:nigel-thornberry --output "$trial/output" --json
# Replace JOB with the job ID printed by queue:
hamr --home "$trial/state" show JOB --content
hamr --home "$trial/state" preview JOB --utterance u00001 --play
hamr --home "$trial/state" resume JOB
hamr --home "$trial/state" status JOB --json
```

Compare the whole spoken control to the exact planned text and an F16 control, including negation, numbers, voice detail, boundaries and nonverbal artifacts. Record elapsed generation time and sample peak process RSS, system RAM and GPU memory through model startup and speech; desktop usage and sampling interval belong with those measurements. Then qualify longer narration with at least two cast references, offline ASR and full listening. Inspect any held publication through the field guide's source/speech review workflow rather than bypassing it. Repeat on AMD, NVIDIA and Intel hardware before claiming broad compatibility or publishing benchmark rankings.

Synthetic controls, decoder checks and transcript comparisons cannot certify expressive delivery or exclude every audible defect. Full-book listening and broader difficult-document qualification continue separately. No hardware-stability claim follows from this release. Missing narration remains a publication hold; the engine does not silently skip failed takes.
