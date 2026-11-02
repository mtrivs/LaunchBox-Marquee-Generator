# Developer notes — Marquee Generator 2.4.1

## Architecture

The app remains a small local Python/Tkinter/Pillow application. Rendering is separate from discovery, orchestration, and installation so a UI/workflow change does not silently change exported pixels.

| Module | Responsibility |
| --- | --- |
| `marquee.py` | LaunchBox records, source matching, geometry/effects, filename conventions, rendering, CLI and backward-compatible `generate` entry point. |
| `app.py` | Tk widget ownership, preferences, live revision control, queued worker results, counted progress display. |
| `system_list.py` | Native checkboxes, independent preview focus, scoped scrolling and icon explanations. |
| `color_picker.py` | Nonblocking, cancellable HSV/RGB/HEX picker and custom swatches. |
| `live_preview.py` | Targeted single-game file queries, region discovery, random eligible-game selection. |
| `batch_jobs.py` | Exact work preparation, sequential batch processing, verified export inventory, checkpoints/reports. |
| `image_cache.py` | Thread-safe byte-bounded LRU storage for decoded source buffers. |
| `logo_packs.py` | Read-only installed pack enumeration and deterministic logo resolution. |
| `installation.py` | Read-only plans, hash revalidation, complete backups, installation journal, conflict-aware restore. |
| `workflow_ui.py` | Install/restore review screens and validated batch/cache preferences. |
| `io_utils.py` | Chunked hashing, atomic JSON/CSV writes, OS-held export/library locks. |

## Thread and rendering contracts

- Tk widgets and `ImageTk.PhotoImage` objects are created/updated only on the GUI thread. Filesystem work uses workers and posts results through `App.events`.
- A preview has one active renderer at a time. Requests are debounced; `render_id` invalidates older results. Worker-owned sources/caches are not changed while rendering: changing a logo replaces the context rather than mutating its `selected` dictionary.
- `image_cache` entries are shared read-only decoded RGBA images. The renderer copies a retrieved image before trimming, tinting or applying other transformations. A source over the configured budget is decoded for working use but is not retained. Batch and game-specific preview caches are separate. Immutable bundled template buffers may be shared with the global byte-limited cache; the per-context cache references the same buffer, not a duplicate, before the renderer copies it for mutation.
- `PreviewLayerCache` stores completed layers. Its key includes element settings, selected source paths, fallback text identity, and only relevant dynamic color/crop/mask state. Never add a rendering dependency without also extending this key and its pixel-parity tests.
- The GUI retains one byte-limited layer cache across preview games. Static template keys omit game identity and include local asset timestamps. Game-dependent keys are scoped by a fresh immutable `cache_namespace` per loaded game/logo context, preventing stale reuse when external art changes. Scan and memory-setting changes replace the shared cache; a worker retains its own snapshot. Only one preview renderer may access it at a time. Batch output uses the normal renderer. Cache budgets count retained pixel payloads, not total process memory or temporary decoding buffers.
- `VERSION` is the application version. `RENDER_VERSION` changes only when rendering semantics change. Keeping it stable avoids unnecessary whole-library rebuilds for UI-only releases.

## Batch API and counted progress

The existing `marquee.generate(...)` arguments retain their positions. New keyword arguments are:

```python
progress_state=None       # callback receiving independent dict snapshots
checkpoint_games=25       # integer 1..10000
logo_overrides=None       # {platform_name: installed_pack_name_or_auto}
```

`progress` still receives human-readable strings. `progress_state` receives:

```text
phase: preparing | batch | stopped | complete
platform: current system name
game_title: current game, published before source lookup/rendering
system_index / system_count: one-based current system, total selected systems
system_done / system_total: completed attempts, eligible planned attempts
  in the current system
overall_done / overall_total: completed attempts, eligible planned attempts
  across all selected systems
```

Totals are counted from a metadata snapshot before processing starts. Filtering/limits are applied before adding eligible work. Generated, verified, unchanged, retained/error and shared-name attempts all advance processed-work counts; hidden/non-game records do not. Percentages measure completed attempts, not elapsed time or success rate. Zero-work systems/batches and stopped batches need explicit handling.

The GUI has separate overall and per-system bars, with Stop Processing to their right. A `game_title` snapshot is emitted before each attempt without advancing counts; counters advance only when the attempt completes. The status below the bars displays that system/title instead of repeating counts. The existing text progress callback retains CLI completion counts; the GUI consumes game identity from structured events and uses text callbacks only for preparation messages.

Both progress rows remain packed at all times, including startup, idle, preview, preparation and file operations. Do not hide the system row with `pack_forget`. Outside measured batch generation it shows zero with a ready/waiting label, while the overall bar animates for uncounted work. Batch completion/Stop retain both measured values.

Do not call `ttk.Progressbar.stop()` after setting a determinate value: Tk resets it to zero. Stop animation first, then set the value; later unrelated worker events must retain that value. Live preview and uncounted preparation keep the indeterminate animation.

## Selection and UI ownership

`App.selected()` returns checked batch systems; `App.preview_selection()` returns the independently focused system name. Preview, palette editing, crop rendering and logo changes use preview focus. Generation and installation filters use checked systems. No checks still permits a focused preview. An empty installation filter retains the existing review-all-verified behavior.

`SystemList` uses native checkboxes and system-name buttons inside a scoped scrolling canvas. Name activation never checks a system. Bulk checkbox changes preserve focus and accepted/draft color edits. The first check can initialize preview focus. Keyboard Space operates a checkbox; name buttons support standard keyboard activation and arrow navigation. While a batch/file operation runs, name/check/bulk controls and regeneration choices are frozen; only batch Stop is enabled.

Generate is enabled only with a scanned library, checked systems and no active batch/file job. The checked count is derived from checkbox values. A cooperative Stop sets the cancellation event and disables itself; it does not interrupt the renderer or PNG write. Do not merge the separate overall/current-system bars. Preview/preparation retain the indeterminate indicator.

The refresh icon is a code-drawn UI asset in a separate header above the preview, labeled New preview, with an explanatory tooltip. Never place controls over the image, since users need to inspect every pixel when fine tuning. Generated artwork pixels are unaffected. The footer is bound to the currently chosen output path and contains no copy-map/report instructions. Export reports continue to be written for compatibility and advanced use.

## Checkpoint and verification files

Export JSON state is schema version 1. New installation restore journals are version 3; version 1/2 journals remain supported. The exception among export files is `marquee-state.json`, which preserves the older plain relative-path-to-signature mapping.

- **`marquee-checkpoint.json`**: authoritative `{version, run_id, library, state, progress}` snapshot. Commit this first, then publish compatible projections/reports. JSON data is written to a sibling temporary file, flushed/fsynced and atomically replaced.
- **`marquee-state.json`**: compatible input signature mapping. Recovery overlays the authoritative checkpoint for the same selected installation.
- **`marquee-manifest.json`**: `{version, library, images}`. `images` maps export-relative paths to `{platform, title, game_id, sha256, size, signature}`. A row enters this inventory only after actual generation or exact verification, not merely because an existing file has the right dimensions/name.
- **Reports**: latest-run CSV projections. Their completed-game checkpoints make partial runs inspectable. A crash between independent projection writes may leave reports behind the authoritative snapshot; a new run republishes current reports.

A checkpoint is made at the configured completed-game interval, after the next completed game when 30 seconds have elapsed, at each system boundary, and after completion/Stop. PNG contents are flushed before a checkpoint attests to them. Because a PNG and a multi-file metadata snapshot cannot be committed as one filesystem transaction, a crash can leave an uncheckpointed PNG. Recovery renders the expected image and compares exact static-PNG RGB pixels before adopting that file. Invalid, animated, EXIF-rotated, unknown or changed images are not silently adopted.

Already verified exports are rehashed before being treated as unchanged. A changed verified file is retained and removed from installation eligibility until verified/rebuilt. Input signatures still detect genuine source/style changes.

## Installation invariants

1. Opening a review is read-only. Opening the app or finishing generation never installs automatically.
2. A ready replacement must be inside the expected staged folder, have a valid current game ID, match its recorded hash, and be a 1920 x 360 PNG. Canonical filenames are validated; verified full-GUID filenames can survive path shortening and moving the export. Destination names are derived for the current configured folder/path budget. Unique ordinary titles use their unnumbered name; GUID disambiguation remains for duplicates/shortened names. Plan stamps include `older` displaced images, their reserved numbered paths, and `retained` alternatives. An identical installed generated title plus retained numbered alternatives is unchanged, preventing repeated copies on every install.
3. Only configured Arcade - Marquee folders participate. File enumeration does not descend into reparse directories. Resolved paths are checked against their allowed roots. No ROM/metadata/Banner/Steam Banner cleanup is performed.
4. Ownership indexes are built once per system. They include all imported games, even hidden ones. Exact, GUID, and conservative unnumbered stripped-title possibilities are combined for ambiguity checks. Possible sharing blocks renaming; matching intentionally does not promise perfect replication of all LaunchBox fuzzy rules.
5. The user selects ready rows and a backup location for each operation. A fresh plan is rebuilt immediately before writes; source/destination hashes and names must match the reviewed snapshot.
6. Library locks serialize installs/restores from different exports. Export locks also prevent generation/installation overlap. OS locks release on process exit; library lock files live under the user temporary directory, outside LaunchBox.
7. A unique dated backup is created outside LaunchBox and the installed/generated image directories. **All old image copies are verified before any original is renamed.** Displaced unnumbered title originals are renamed within their current directory to ordinary numbered image filenames, preserving bytes/extensions. Existing numbered/ROM/GUID alternatives are retained, not renamed. `NumberedNames` scans each directory once per review, allocates after the highest suffix across supported formats with case-insensitive keys, and reserves planned names. Never replace an occupied preservation name. Refuse names beyond the supported path budget rather than silently changing the base title. Backup image formats are preserved under short numbered names; the manifest retains original paths.
8. `restore-manifest.json` is committed before mutations and after each game. Entry states are `pending`, `installing`, `installed`, `restoring`, and `restored`. Version 3 records contain a per-operation `operation_id` and each displaced image's numbered `preserved` path. `generated_preserved` is initially null and is selected/frozen by restore review, since additional numbered alternatives may have appeared since installation. Write all these mappings before mutations so a crash between individual renames can be reconstructed from hashes and actual file presence. Top-level status records preparation, completion, interruption or partial restore. A process crash may leave an `installing` record; that is still recoverable.
9. Copy promotion refuses a destination that appeared after review. Windows rename supplies no-replace behavior; the POSIX equivalent uses a hard link. Unexpected targets are never silently replaced.
10. Restore validates the reviewed journal is unchanged, checks actual backup/target hashes, and revalidates each game immediately before touching it. Newer artwork is a conflict. Existing originals already matching backup bytes are left in place. Version 3 restore renames the unchanged generated PNG to its reviewed next available numbered filename before reactivating originals. External backups are used only when needed. Backups and numbered generated copies are retained. Version 1/2 journals retain their earlier restore semantics.

Failures during backup preparation leave installed originals untouched. Failures during mutation leave the journal/backups available for a separate reviewed restore. Do not automatically roll back over user edits.

## Logos, preferences, and portability

Preview discovery caches configured folder and platform-logo resolution by library, system, type and chosen pack until Scan. Automatic legacy success must return before pack enumeration. Pack file enumeration uses `os.scandir` metadata to avoid one SMB stat per logo.

Automatic logo resolution preserves the legacy per-platform Clear Logo folder, then searches installed pack roots/platform branches, preferring Nostalgic and legacy-conversion packs. An explicit selection takes priority and falls back if missing. Scrape-as aliases are supported. Discovery reads installed files only; it does not change LaunchBox's selected pack or fetch media.

GUI preferences validate object structure, HEX values and numeric bounds. Color picker drafts are transient; Apply/Cancel updates the accepted palette. Rapid accepted edits coalesce into an atomic settings write, with a final flush before generation/close. Cache changes replace preview caches without mutating buffers held by an active renderer.

App assets/modules are relative to the extracted folder. Library and output paths come from current user selections. Do not add test-machine share paths, credentials, absolute asset paths, or automatic destructive startup actions.

New numbered preservation deliberately keeps older artwork available to LaunchBox. It does not rely on an unverified universal exact-before-numbered priority rule. The legacy `inactive_path` helper exists solely to validate older version 2 restore journals; new installation/restore actions use numbered image filenames.

## Tests and changes

Run `python -m unittest discover -p "test_*.py"` using the documented Python/Pillow requirements. The suite uses disposable local LaunchBox fixtures. Install/restore tests never target the user's real library. New checks cover counters, zero work, cancellation, crash recovery, verification, moved GUID exports, shared candidates, changed reviews, backup conflicts, interrupted install/restore, locks, bounded/oversize caches, logo priority, and actual review-window button handlers.

When changing code:

- Add pixel-parity coverage for cache/render dependencies and preserve full 1920 x 360 output.
- Add both read-only-plan and mutation/recovery tests for installation behavior.
- Never infer generated provenance from dimensions alone.
- Keep comments focused on contracts, invariants and non-obvious failure handling. Routine statements should remain self-explanatory.
