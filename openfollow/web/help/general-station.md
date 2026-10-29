# Station Settings

This station's name, web access and display units, plus the settings you set once and leave alone.

**Station name displayed on the network** – up to 64 characters, shown in the web UI header and the Station Network list, and sent as the PSN system name. A cleared field reverts to `OpenFollow`.

**Web access PIN (leave empty to disable)** – a numeric PIN (1–32 digits) that protects the web UI. While unset, anyone on the network can open it. Set a PIN on any station on a shared production network. Stations also use it to authenticate configuration exchanges with each other.

**Displayed unit system** – choose if Metric (m, m/s) or Imperial (ft / in, ft/s) are used in the user interface. Output protocols and stored values always stay metric.

**mDNS address** – the station's `.local` name, which computers on the same network open it by. Read only; it follows the station name.

**Custom domain name (FQDN)** – this station's name on a venue network with its own DNS, such as `of-1.stage.example.com`. It has to resolve to this station in the venue's DNS. The Operator Screen then shows it in place of the `.local` name, which keeps working. **Saving a changed name reconnects every network interface**: each link drops for a few seconds. **Remove FQDN** on the Operator Screen's Network screen clears it.

**Autostart** – whether OpenFollow starts when the station powers on. Switching it off leaves the running station untouched, but this web UI is gone after the next reboot, and switching it back on takes SSH or a keyboard on the station. Absent on macOS.

**Save** – saves the station name, PIN, custom domain name and features. No restart is needed.

## Features

The table at the end of **Advanced Settings** has one row per feature. A change applies on **Save**. A Save that switches a feature off first lists what stops: **Turn off** saves, **Cancel** saves nothing. A feature that is off is hidden in the web UI and does not run, but its settings are kept. *Experimental* features are off on a new station.

- **Trigger zones** – the Trigger Zones and Zone Editor sections and the Toggle Zones bindings. Off, zones neither fire nor show.
- **MIDI** – the MIDI patches and each virtual fader's MIDI source. Off, no MIDI device is opened.
- **PSN input** – receiving other stations' markers. Off, they are neither shown nor counted by zones.
- **Lens line suggestions** – the suggested lines in the Setup Wizard's Lens step. Off, lines are drawn by hand.
- **Person detection** – the Person Detection tab. Off, detection does not run.
- **RTTrPM output** – the RTTrPM Output section. Off, nothing is sent.
- **Ground grid** – the Ground Grid section. Off, it is neither drawn nor drops markers onto its height.
- **Touch remote** – the Touch Remote section. Off, every remote lets go of its marker.
- **Zone heights** – the height fields of each zone in the Zone Editor. Off, zones are drawn on the stage floor and markers keep their own height.
