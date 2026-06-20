# Configuration Transfer

Back up and restore a station's full settings as a single `.openfollowsettings` file – duplicate a config across stations, archive a rig before changes, or recover from a snapshot.

**Export Configuration** – downloads the current configuration as a `.openfollowsettings` file containing every saved setting (camera, grid, markers, zones, OSC, MIDI, input, display).

**Configuration File (.openfollowsettings)** – select a `.openfollowsettings` file previously exported from any OpenFollow station; it's validated before anything is applied.

**Import Configuration** – applies the selected file. The station's network IP is preserved; every other setting is replaced. If any imported setting needs a restart, a confirmation dialogue appears first; otherwise the page reloads automatically.

The restart dialogue offers three choices:

- **Restart Now** – apply everything and restart; the page reloads once the station is back online.
- **Apply Without Restart** – apply what can take effect immediately; the rest waits for the next restart.
- **Cancel** – discard the import; nothing changes.
