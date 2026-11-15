# Software Update

Install a newer OpenFollow release and restart the service – no SSH session needed.

**Installed** shows the version currently running.

**Version … is available** appears when the station's own background check, at startup and when its address changes, found a newer release on GitHub. Without an internet uplink no box appears. After a failed install the failure shows in its place, with the reason on a second line.

**Install Latest Version Online** checks GitHub for the newest release. If it is newer, a confirmation dialog appears; confirming downloads the signed update bundle (`.ofupdate`) for this device's architecture, verifies it and installs it. A locked dialog shows progress and the page reloads once the new version is up, then opens **What's new** once per update. **(What's new)** after the version at the foot of the page opens it again. If the device is already current, nothing is changed.

The device briefly goes away while the service restarts. An install that fails before the service has stopped shows its reason in the dialog, and the running version stays. An update is refused while OpenFollow is restarting. If the updater lacks a privilege, run **Apply Permissions** on the Device page first.

> **Needs internet.** This option reaches GitHub. On an isolated show LAN with no uplink, use **Install .ofupdate File**.

## Settings backup

Before an update installs, the station saves its configuration, marker catalog, your own templates and saved lens calibrations. The Media Gallery and the detection models are not included. Backups are kept in `/var/lib/openfollow/backups/`, one per install; the ten newest are kept. A backup holds the web PIN and stream passwords, so only the OpenFollow service user can read it.

If the backup can't be made, the update still installs. To keep your own copy, use **Export Configuration** under **General → Configuration Backup & Import** first.

To restore one, use **Restore…** beside it under **General → Configuration Backup & Import → Backups on this station**.

## What's verified before install

Every update, online or offline, is refused on any failure of these checks:

- **Signature** – made with the OpenFollow release key.
- **Checksum** – the package's SHA-256 must match the signed manifest.
- **Identity** – the package must be `openfollow` and match this device's architecture.

## Offline install

**Install .ofupdate File** opens a file picker for an `openfollow_<version>_<arch>.ofupdate` bundle, downloaded from the release on a machine with internet. Choosing the file starts the install straight away, after the same verification. Unlike the online path, it does not gate on version: you can reinstall the same version or downgrade. Only a bundle signed for an OpenFollow release installs.
