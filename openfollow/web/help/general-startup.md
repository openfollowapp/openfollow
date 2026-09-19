# Startup

Whether this station launches OpenFollow by itself when it is powered on. The switch reads the station's own service state each time this box opens, so it always shows what will actually happen at the next boot – not a saved preference that could have drifted from it.

**Start OpenFollow at boot** – on for every station installed from a release package or image. Switching it off leaves the running station untouched: video, tracking and output all keep going until the station is restarted or powered down. From the next boot onwards it comes up with no OpenFollow running.

> This web interface is served by OpenFollow itself. A station that no longer starts it at boot also serves no page to switch it back on – that takes an SSH session, or a keyboard and screen attached to the station.

Leave it on unless you have a specific reason not to. The usual one is a bench or development machine where OpenFollow is started by hand.

The switch is absent when there is nothing to change: on a workstation that is not running OpenFollow as a system service, and on macOS.
