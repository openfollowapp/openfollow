# Software Update

Install a newer OpenFollow release and restart the service – no SSH session needed.

**Installed** shows the version currently running on this device.

**Check & Install Latest** queries GitHub for the newest published release. If it is newer than the installed version, a confirmation dialog appears; confirming downloads the signed update bundle (`.ofupdate`) for this device's architecture, verifies it, and installs it. Progress is shown in a locked dialog and the page reloads automatically once the new version is up. If the device is already current, you'll see an "already up to date" message and nothing is changed.

The install runs detached from the running service (the package restarts the service itself), so the device briefly goes away and comes back on the new version. If the updater is missing the privilege it needs, install fails with a hint to run **Apply Permissions** on the Device page first.

> **Needs internet.** This option reaches `api.github.com` and the release download. On an isolated show LAN with no uplink, use **Offline install** instead.

## What's verified before install

Every update – online or offline – is checked before anything is installed as root, and refused on any failure:

- **Signature** – the bundle carries a signature made with the OpenFollow release key. The device holds the matching public key and rejects anything not signed by it, so a tampered or third-party package cannot install.
- **Checksum** – the package's SHA-256 must match the signed manifest, so a truncated or corrupted download is caught.
- **Identity** – the package inside must be `openfollow` and match this device's architecture.

## Offline install

For venues with no internet, expand **Offline install** to install an update bundle you supply.

- **Choose file** – pick an `openfollow_<version>_<arch>.ofupdate` bundle, downloaded from the release on a machine that does have internet.
- **Upload & Install** – uploads it over your local network and installs it after the same signature + checksum verification as the online path.

Unlike the online check, the offline path does **not** gate on version – you can deliberately reinstall the same version or downgrade, as long as the bundle is properly signed.

> The bundle is a single file that contains the `.deb`, its checksum, and the signature. Only an `.ofupdate` signed for an OpenFollow release will install – an unsigned or hand-built package is rejected.
