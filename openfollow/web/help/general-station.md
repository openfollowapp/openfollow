# Station Settings

This station's name, web access and display units, plus the settings you set once and leave alone.

**Station name displayed on the network** – up to 64 characters, shown in the web UI header and the Station Network list, and sent as the PSN system name. A cleared field reverts to `OpenFollow`.

**Web access PIN (leave empty to disable)** – a numeric PIN (1–32 digits) that protects every configuration route in the web UI. While unset, the interface is open to anyone on the network. Once set, browsers must supply the PIN to reach any non-asset page, and peer-to-peer configuration exchanges between OpenFollow stations are authenticated with it (the PIN itself never travels on the wire).

> Set a PIN on any station that is connected to a shared production network. Leaving it unset is acceptable only on isolated bench or point-to-point networks.

**Displayed unit system** – choose **Metric (m, m/s)** or **Imperial (ft / in, ft/s)**. This controls what the web UI and the Operator Screen show and parse across Camera, Grid, Markers, Movement, Trigger Zones, and the Setup Wizard. Stored configuration and every wire protocol – OSC, PSN, RTTrPM, OTP – always stay metric regardless of this setting. The selection takes effect as soon as you change it; no Save is needed. The Setup Wizard's Grid Setup step offers the same choice.

**Advanced Settings** – opens closed on every page load.

**mDNS address** – the station's `.local` name, which computers on the same network open it by. Read only; it follows the station name.

**Custom domain name (FQDN)** – this station's name on a venue network with its own DNS, such as `of-1.stage.example.com`. The name has to resolve to this station in the venue's DNS. The web UI then accepts changes made through it, and the Operator Screen shows it in place of the `.local` name, which keeps working. On Linux it is also listed in `/etc/hosts`. Where OpenFollow manages the network settings it is sent to the DHCP server too (some register it in DNS, others ignore it), and **saving a changed name reconnects every network interface**: each link drops for a few seconds. A caution under the field lists any interface that does not send the name. **Remove FQDN** on the Operator Screen's Network screen clears it; where saving reconnects, it reads **Remove FQDN (interrupts network traffic)**.

**Autostart** – whether OpenFollow starts when the station powers on. Switching it off leaves the running station untouched.

> Off, this web UI is gone after the next reboot too, and switching it back on takes SSH or a keyboard on the station. Absent on macOS and where OpenFollow is not a system service.

**Save** – saves the station name, PIN and custom domain name. No restart is needed.

## Features

The table at the end of **Advanced Settings** lists what this station uses: one row per feature, with its title and a short description. Each box applies as soon as it changes, without **Save**. A feature that is off is hidden in the web UI and does not run, but its settings are kept: ticking it again brings it back as it was. Its own switch (such as **Enabled** under RTTrPM Output) still applies on top. Those marked *Experimental* are off on a new station.

- **Trigger zones** – the Trigger Zones and Zone Editor sections and the Toggle Zones bindings. Off, zones neither fire nor show on the Operator Screen.
- **MIDI** – the MIDI patches and each virtual fader's MIDI source. Off, no MIDI device is opened.
- **Lens distortion** – the Camera tab's Lens distortion values and the Setup Wizard's Lens step. Off, the Operator Screen, mouse, person detection and zones all work as if the lens were straight, and the wizard leaves the saved values alone. A camera pinned with a lens needs its corners pinned again before a show.
- **PSN input** – receiving other stations' markers. Off, they are neither shown nor counted by zones. Nothing is hidden.
- **Person detection** – the Person Detection tab. Off, detection does not run.
- **RTTrPM output** – the RTTrPM Output section and its Interface Assignment row. Off, nothing is sent.
- **Paths** – the Paths and Path Editor sections, the Pathfinder and Toggle Paths bindings and OSC path commands. Off, every marker on a path is let go; each path keeps its Active setting.
- **Ground grid** – the Ground Grid section. Off, it is neither drawn nor drops markers onto its height.
- **Calibration points** – the Camera tab's calibration points and the Setup Wizard's Advanced section in Corner Pinning. Off, they are only hidden: the saved points still count.
- **Touch remote** – the Touch Remote section and its launcher. Off, every remote lets go of its marker.

**Apply to all stations** – sends this list to every station on the network.
