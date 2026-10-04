# Station Settings

This station's name, web access and display units, plus the settings you set once and leave alone.

**Station name displayed on the network** – up to 64 characters, shown in the web UI header and the Station Network list, and sent as the PSN system name. A cleared field reverts to `OpenFollow`.

**Web access PIN (leave empty to disable)** – a numeric PIN (1–32 digits) that protects every configuration route in the web UI. While unset, the interface is open to anyone on the network. Once set, browsers must supply the PIN to reach any non-asset page, and peer-to-peer configuration exchanges between OpenFollow stations are authenticated with it (the PIN itself never travels on the wire).

> Set a PIN on any station that is connected to a shared production network. Leaving it unset is acceptable only on isolated bench or point-to-point networks.

**Displayed unit system** – choose **Metric (m, m/s)** or **Imperial (ft / in, ft/s)**. This controls what the web UI and the Operator Screen show and parse across Camera, Grid, Markers, Movement, Trigger Zones, and the Setup Wizard. Stored configuration and every wire protocol – OSC, PSN, RTTrPM, OTP – always stay metric regardless of this setting. The selection takes effect as soon as you change it; no Save is needed.

**Advanced Settings** – opens closed on every page load.

**mDNS address** – the station's `.local` name, which computers on the same network open it by. Read only; it follows the station name.

**Custom domain name (FQDN)** – this station's name on a venue network with its own DNS, such as `of-1.stage.example.com`. The name has to resolve to this station in the venue's DNS. The web UI then accepts changes made through it, and the Operator Screen shows it in place of the `.local` name, which keeps working. On Linux it is also listed in `/etc/hosts`. Where OpenFollow manages the network settings it is sent to the DHCP server too (some register it in DNS, others ignore it), and **saving a changed name reconnects every network interface**: each link drops for a few seconds. A caution under the field lists any interface that does not send the name. **Remove FQDN** on the Operator Screen's Network screen clears it; where saving reconnects, it reads **Remove FQDN (interrupts network traffic)**.

**Autostart** – whether OpenFollow starts when the station powers on. Switching it off leaves the running station untouched.

> Off, this web UI is gone after the next reboot too, and switching it back on takes SSH or a keyboard on the station. Absent on macOS and where OpenFollow is not a system service.

**Experimental features** – shows Person Detection, RTTrPM Output and Lens Distortion, each marked *Experimental*. Turning it off also switches person detection off; turning it back on does not.

**Save** – saves the station name, PIN and custom domain name. No restart is needed.
