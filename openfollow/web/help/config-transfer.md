# Configuration Backup & Import

Move a station's settings in and out as a `.ofsettings` file, put them back to their defaults, or bring back a backup the station saved before a change.

**Export Configuration** – downloads every saved setting as a `.ofsettings` file.

**Import Configuration…** – applies a `.ofsettings` file from any OpenFollow station, after a confirmation and a backup of the current settings. Show settings change at once; the update check, clock sync and network backend from the next start. What connects this box stays: the web login, port and interface, the station's identity, interface pins and labels, local file paths and the marker move speeds.

**Restore Defaults** – returns every setting to a fresh install's value, after a confirmation and a backup, then restarts the station. Network access, the station's identity and local file paths stay; the shared marker catalog and uploaded media are untouched.

**Backups on this station** – the backups saved before each update, import, restore and settings push, newest first. **Restore…** brings one back and restarts the station. When a backup could not be made, the change still goes ahead and says so.

**Pushed settings** – when another station sends its settings here, every page and the Operator Screen show a warning listing each push: when, from which station and what it sent. The settings are already applied; **Remove Warning** only acknowledges them, everywhere at once. A push never changes what each station keeps for itself, such as its name, login, interface pins, OSC destinations and trigger zones.

### Restoring a backup

What a restore brings back and what stays.

A restore replaces the configuration, the marker catalog, saved templates and lens calibrations with the backup's; a part the backup does not hold stays as it is. The station name, network access and local file paths stay. After the restart, marker changes from other stations follow as usual. A backup made by a newer OpenFollow version cannot be restored. When a part could not be written, the station restarts on the rest and names that part. Backups hold passwords, so they never leave the station.
