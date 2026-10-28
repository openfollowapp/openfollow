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
| Rows | Newest first. Each row names when it was made (the browser's local time), what made it (**Before update from 0.4.3**, **Before import**, **Before Restore Defaults**, **Before settings push**, **Before restore**) and the version that wrote it. The update and web folders are listed together. |
| Scope | Everything the archive holds: `config.toml`, the marker catalog, `templates/user/` and `lens-calibrations/`. This station's device identity (`_DEVICE_IDENTITY_FIELDS`) is kept. Gallery media is not in an archive and stays as it is. |
| Apply | Archive the current settings first (kind `restore`), write the archive's files, restart. The same shape as Restore Defaults: `server.restart_after`, the restart dialog that becomes the progress view. A failed backup does not block the restore, as with import. |
| Catalog | Written back from the archive. After the restart the normal catalog sync with peers applies: newer entries and deletions from other stations win. The help says so. |
| Version | A backup written by a **newer** OpenFollow version is refused, with the reason. Same or older: restored, every field through `__post_init__` as a hand-edited `config.toml`. |
| Unreadable config | Restore works while `config.toml` does not read. The refusal on an unreadable config then reads "Import a settings file or restore a backup to replace it." |
| Confirmation | A confirm dialog naming the backup and saying the station restarts. Only its final button (**Restore and restart**) is red; the row's Restore button is not. |

## Design

- **Listing** (`GET /api/config/backups`): names, kinds, versions and times only, read from file names and manifests. Never the archive bytes or contents. Kind and version come from the manifest (`kind` / `version` for web backups, `from` for update backups); the file name is the fallback.
- **Restoring** (`POST /api/config/backups/restore`, body `{"name": …}`):
  - The name must be one the listing returns: matched against `list_archives` of both folders, never joined into a path from the request.
  - The archive is opened with `tarfile` (`filter="data"`). Only the members a backup writes are read (`config.toml`, `markers.toml`, `manifest.json`, `templates/user/…`, `lens-calibrations/…`), regular files only, each under a size cap.
  - Its version is compared with `openfollow.__version__` through `packaging.version` (a Debian `~rc` read as a pre-release); newer answers 409 with the reason.
  - `config.toml` is loaded through `load_config` from a temporary file, so it gets the same normalisation as a hand-edited one; identity fields are copied from the running config.
  - Under the update lock, through `server.restart_after`: write `config.toml`, the catalog at this station's `markers_catalog_path`, and replace `templates/user/` and `lens-calibrations/` with the archive's. Then restart.
  - Busy update: 409 as Restore Defaults. Malformed body: 400. Unknown name: 404.
- **Module**: the reading lives outside `privilege/settings_backup.py`, which stays the standard-library writer inlined into `preinst`.

## Work items

- [x] Plan approved, UI mockup approved (Artifact). A backup from a newer version stays in the list with its Restore button off and the row saying "Newer than this station"; the server refuses it too.
- [ ] Listing: route, kind and version per archive, newest first, both folders; tests for each kind, a missing manifest, an unreadable folder.
- [ ] Restore: route, name check, member filter and caps, version refusal, identity kept, catalog / templates / lens calibrations written, restart through `restart_after`; tests for each refusal (400, 404, 409 busy, 409 newer, a damaged archive) and each restored part.
- [ ] Backup before restore (kind `restore`), and the restore going ahead when it fails.
- [ ] Unreadable-config refusal text, with its tests.
- [ ] UI: the list, the confirm dialog, the restart progress, errors through `window.OpenFollow.saveError`; JS tests.
- [ ] Help `config-transfer.md` (also: a backup holds lens calibrations), `docs/architecture/config.md`, `docs/architecture/update.md`, `docs/PACKAGING.md` (the web restore beside the manual one).
- [ ] What's new: a line that a station can now restore its own backups.
- [ ] Review to cosmetic, `make ci-remote`, station check on pi66 (restore an update backup and a web backup, check identity kept and the restart).
