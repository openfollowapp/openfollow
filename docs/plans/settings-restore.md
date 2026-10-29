# Plan: restore a settings backup from the web UI

The station already writes a settings backup (`.ofbackup`) before every update
and before every import, Restore defaults and settings push
(`privilege/settings_backup.py`). Today the only way back is a `tar` command on
the station (`docs/PACKAGING.md`). This adds a list of the station's own backups
to the web UI, each with a Restore button. Issue #422.

## Decisions

| Topic | Decision |
|---|---|
| Entry point | A list of **this station's own** backups. No upload, and Import keeps taking `.ofsettings` only: an archive holds secrets and never leaves the station. |
| Placement | The Configuration card (`partials/config_transfer.tpl`), as a **Backups on this station** group under Export, Import and Restore Defaults. |
| Rows | Newest first, the update and web folders together. Columns **Made** (the browser's local time, "28 Oct 2026, 21:40"), **Before** (**Update to 0.5.0**, **Import**, **Restore Defaults**, **Settings push**, **Restore**; **Settings change** for a kind this version does not know) and **Version** (the version that wrote it). Running text says it in a sentence: "made before the update to 0.5.0", "an import", "Restore Defaults", "a settings push", "a restore". Each row's button is named for screen readers by its time and kind. |
| Scope | Everything the archive holds: `config.toml`, the marker catalog, `templates/user/` and `lens-calibrations/`. A part the archive does not hold stays as it is. Gallery media is not in an archive and stays as it is. |
| Kept on restore | `_DEVICE_IDENTITY_FIELDS` and the station name (`psn_system_name`), as the confirm dialog says. `enforce_network_mode` runs for the kept mode: a Simple station takes no pin back, an Advanced one gets the backup's own plane pins. The catalog goes to this station's `markers_catalog_path`. |
| Apply | Read and check the archive whole, then archive the current settings (kind `restore`), stage the catalog and folders beside their targets, and under the update lock (`server.restart_after`) save the config, rename the staged copies into place and request the restart. The restart dialog becomes the progress view, as for Restore Defaults. A failed backup does not block the restore, as with import. |
| Until the restart | Every writer of a restored part (config, catalog, templates, lens calibrations) writes inside `restore_hold.unless_held`; the restore holds all four under `restore_hold.GATE` with its config save and renames. A later write raises `HeldForRestartError`: a web write answers 409 "OpenFollow is restarting.", an on-screen save says so, a peer's catalog change stays in memory. |
| Catalog | Written back from the archive. After the restart the normal catalog sync with peers applies: newer entries and deletions from other stations win. The help says so. |
| A write that fails | Everything that takes space is staged and synced first: the journal, the catalog beside its target, each folder and the place its target is moved aside to, and `config.toml` itself. A failure there, or of the journal or config rename, writes nothing, holds nothing and does not restart: 500 "The settings could not be written." From the config rename on, every part is held and the commit never raises, so the restart always follows; a part that fails keeps the station's own, and the answer is a 500 naming the part with `restarting`, so the page keeps its restart progress with the reason and confirms with a caution toast that also says when no backup was made. |
| Cut short | The commit renames its journal into place, then the config, then every other part, and removes the journal when done. `recover_interrupted_restore`, at start before the config is read and before the next restore: with the journal, every part is finished from its staged copy, the config included; without it, all are discarded and a folder moved aside is put back. |
| Version | A backup written by a **newer** OpenFollow version is refused, with the reason. Debian `~rc` is a pre-release; an epoch and a revision (`-1`) are dropped. A version that does not parse, or a running 0.0.0, is never newer. Same or older: restored, every field through `__post_init__` as a hand-edited `config.toml`. |
| Damaged archive | 422 "This backup could not be read.", before any backup, nothing written. Caps: 64 MiB archive, 8 MiB a member, 64 MiB of members, 4096 members, 72 MiB unpacked in all (pax and long-name headers included). Refused too: an absolute or `..` name, a link or device, a file where another needs a folder, a catalog that is no TOML, a `config.toml` that does not load. A folder that does not list: 500, the list shows the error notice. |
| Unreadable config | Restore works while `config.toml` does not read, keeping the identity of its last whole read. An empty one is refused with the 409 every write gives (a restart restores it). The refusal on an unreadable config reads "Import a settings file or restore a backup to replace it." |
| One at a time | A restore while another runs or while one's restart is pending: 409 "Another restore is running." / "Restore again once the station is back." |
| Confirmation | A confirm dialog naming the backup and saying the station restarts. Only its final button (**Restore and Restart**) is red; the row's **Restore…** button is not. A refusal shows under the list with the lead "Not restored."; an unanswered request ends "then try again". |

## Design

- **Listing** (`GET /api/config/backups`): names, kinds, versions and times only, read from file names and manifests. Never the archive bytes or contents. Kind and version come from the manifest (`kind` / `version` for web backups, `from` for update backups); the file name is the fallback.
- **Restoring** (`POST /api/config/backups/restore`, body `{"name": …}`):
  - The name must be one the listing returns, never joined into a path from the request.
  - The archive is read as a capped gzip stream through `tarfile`, never extracted; the `data` filter's rules are applied by hand. Only the members a backup writes are kept (`config.toml`, `markers.toml`, `templates/user/…`, `lens-calibrations/…`).
  - Its version is compared with `openfollow.__version__` through `packaging.version`; newer answers 409 with the reason.
  - `config.toml` is loaded through `load_config` from a temporary file, so it gets the same normalisation as a hand-edited one; identity fields are copied from the running config.
  - Busy update: 409 as Restore Defaults. Malformed body: 400. Unknown name: 404. Damaged: 422. A write that fails: 500.
- **Module**: `config_restore.py`; the reading lives outside `privilege/settings_backup.py`, which stays the standard-library writer inlined into `preinst`.

## Work items

- [x] Plan approved, UI mockup approved (Artifact). A backup from a newer version stays in the list with its Restore button off and the row saying "Newer than this station"; the server refuses it too.
- [x] Listing: route, kind and version per archive, newest first, both folders; tests for each kind, a missing manifest, an unreadable folder.
- [x] Restore: route, name check, member filter and caps, version refusal, identity kept, catalog / templates / lens calibrations written, restart through `restart_after`; tests for each refusal (400, 404, 409 busy, 409 newer, a damaged archive) and each restored part.
- [x] Backup before restore (kind `restore`), and the restore going ahead when it fails.
- [x] Unreadable-config refusal text, with its tests.
- [x] UI: the list, the confirm dialog, the restart progress, errors through `window.OpenFollow.saveError`; JS tests.
- [x] Help `config-transfer.md` (also: a backup holds lens calibrations), `docs/architecture/config.md`, `docs/architecture/update.md`, `docs/PACKAGING.md` (the web restore beside the manual one).
- [x] What's new: a line that a station can now restore its own backups.
- [ ] Review to cosmetic, `make ci-remote`, station check on pi66 (restore an update backup and a web backup, check identity kept and the restart).
