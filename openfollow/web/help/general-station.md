# Station Settings

Identity, display preferences, and web access for this station. Changes here apply immediately – no restart is required.

**Unit system** – choose **Metric (m, m/s)** or **Imperial (ft / in, ft/s)**. This controls what the web UI and the Operator Screen show and parse across Camera, Grid, Markers, Movement, Trigger Zones, and the Setup Wizard. Stored configuration and every wire protocol – OSC, PSN, RTTrPM, OTP – always stay metric regardless of this setting. The selection takes effect as soon as you change it; no Save is needed.

**Show experimental features** – off by default. When enabled it reveals early / rough features still in development: **Person Detection** (its own tab), **RTTrPM Output** (Output tab), and **Lens Distortion** (the Camera tab and the Setup Wizard's corner-pinning step), each marked with an *Experimental* badge. Toggling it applies instantly – no reload. Turning it **off** also disables person detection so a hidden feature can't keep running unseen; turning it back **on** does not re-enable it – you switch it back on deliberately in its own section. Lens distortion is a visual gate only: hiding the sliders does not change a coefficient you already saved, so an existing correction keeps bowing the overlay until you set it back to `0` / `0`.

**Station name** – a human-readable name for this device, up to 64 characters. It appears in the web UI header, in the Server Network list on the Overview tab, and is broadcast as the PSN system name that other show-control tools see on the network. Defaults to `OpenFollow`; on first boot a memorable two-word suffix is appended automatically so a fleet of stations stays distinguishable. If you clear the field it reverts to the default.

**PIN (leave empty to disable)** – a numeric PIN (1–32 digits) that protects every configuration route in the web UI. While unset, the interface is open to anyone on the network. Once set, browsers must supply the PIN to reach any non-asset page, and peer-to-peer configuration exchanges between OpenFollow stations are authenticated with it (the PIN itself never travels on the wire).

> Set a PIN on any station that is connected to a shared production network. Leaving it unset is acceptable only on isolated bench or point-to-point networks.

**Save** – writes the Station name and PIN to disk. Both fields apply live as soon as you save; no app restart is needed.
