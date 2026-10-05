# Configuration

Move a station's full settings in and out as a single `.ofsettings` file – duplicate a config across stations, archive a rig before changes, or recover from a snapshot – and put every setting back to its default.

**Export Configuration** – downloads the current configuration as a `.ofsettings` file containing every saved setting (camera, grid, markers, zones, OSC, MIDI, input, display).

**Configuration File (.ofsettings)** – select a `.ofsettings` file previously exported from any OpenFollow station; it's validated before anything is applied. Files exported by earlier versions, ending `.openfollowsettings`, import the same way.

**Import Configuration** – applies the selected file. The station's network IP is preserved; every other setting is replaced and takes effect straight away, and the page reloads.

**Restore Defaults** – returns every setting to the value a fresh install has: camera, grid, markers, zones, OSC, MIDI, input, display and video source. A confirmation dialogue appears first, and there is no undo. The station restarts to finish the reset – a few settings only take effect at startup – and the page returns on its own once it is back. What stays is what describes this box rather than the show: the web login PIN, port and interface – network access is not reset, so this page keeps working – the station's identity (its name follows that identity, as it did on the first run), and local file paths such as the detection model storage. Marker definitions in the shared catalog and uploaded media are not settings, so they are untouched.
