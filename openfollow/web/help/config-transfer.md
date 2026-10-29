# Configuration

Move a station's full settings in and out as a single `.ofsettings` file, and put every setting back to its default.

**Export Configuration** – downloads every saved setting as a `.ofsettings` file.

**Import Configuration…** – opens a file picker for a `.ofsettings` file from any OpenFollow station. Choosing a file asks first, and nothing is applied until **Import** is pressed. A backup of the current settings is saved, then every show setting is replaced, including the station name, marker selection, detection masks and remembered video sources. The page reloads and confirms the file. The automatic update check and clock sync, pre-release offers, update source and network backend change from the next start. What stays is what connects this box: the web login PIN, port and interface, the station's identity, its interface pins and labels, local file paths, and the per-marker move speeds.

**Restore Defaults** – returns every setting to a fresh install's value, after a confirmation and a backup of the current settings. The station restarts to finish the reset and the page returns on its own. It is refused while a software update is running. The web login PIN, port and interface, the station's identity and local file paths stay. Marker definitions in the shared catalog and uploaded media are untouched.

**Backups** – the station keeps the ten newest backups taken before an import, a restore to defaults or pushed settings, in a `backups/web` folder beside its configuration. When a backup could not be made, the change still goes ahead and its confirmation turns orange; the station's log holds the reason.

**Pushed settings** – when another station sends its settings here (**Apply to all stations** on its Grid or Camera page), every web UI page shows a dialogue until **Remove Warning** is pressed. It lists each push since: the time, the sending station's name and address, and what it sent. The Operator Screen shows the newest push as a blue row in its status corner, and its Settings menu lists **Remove Pushed Settings Warning** first. The settings are already applied when the warning appears; removing it only acknowledges them, everywhere at once. A push never renames this station. A push of **every shared setting** leaves what each station keeps for itself, such as OSC destinations, trigger zones, interface pins, the login and the station name. A restart of OpenFollow clears the list.
