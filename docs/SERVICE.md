# Service Management (Raspberry Pi)

## Quick Commands

```bash
task install    # One-time setup
task start      # Start service
task stop       # Stop service
task restart    # Restart service
task status     # Check status
task logs       # View logs
task enable     # Autostart at boot
task disable    # Disable autostart
task uninstall  # Remove service
```

## Manual Run

```bash
task dev        # Run manually (not as service)
```

## Web Update Button

The General section in the web UI includes a **Software Update** action that installs
a newer OpenFollow release from GitHub Releases as a **signed `.ofupdate` bundle**.
(This is the sole update path; the earlier `git pull` + `poetry install` updater has
been removed – see "Removed: in-app git updater" in [`PACKAGING.md`](./PACKAGING.md).)

A bundle is a tar of the `.deb`, a `SHA256SUMS`, and an RSA signature over it. Before
anything is installed as root, both update paths verify the **signature** (against the
on-device public key) and the **`.deb`'s SHA-256**, and fail closed on any mismatch –
so a tampered, truncated, or unsigned package cannot install.

**Check & Install Latest:**

1. Queries the GitHub Releases API for the newest published (incl. pre-release) tag of
   the repo in `update_github_repo` (default `openfollowapp/openfollow`).
2. If newer than the installed version, downloads the `openfollow_<version>_<arch>.ofupdate`
   bundle for this device's architecture to `/tmp`.
3. Verifies the bundle signature + checksum, extracts the inner `.deb`, re-checks its
   package identity + architecture, then installs it via `apt-get install`.
4. Restarts the `openfollow` service onto the new version.

**Offline install** (collapsed by default) uploads an operator-supplied `.ofupdate`
bundle over the LAN and installs it the same way – same verification, but no GitHub
fetch and no version gate (downgrades and reinstalls are allowed for a properly signed
bundle).

### Detached install

The install runs in a transient `systemd-run` unit, **detached** from `openfollow.service`:
the package's `prerm` stops the service mid-install, which would otherwise kill the
in-process `apt-get` and leave the package half-configured. The new version clears the
on-disk update status on startup.

### Required privilege

The service user needs a NOPASSWD sudoers rule for the install (`systemd-run` → the
update wrapper script + `apt-get`). This is provisioned by the package / Ansible install;
if it is missing, the update fails with a hint to run **Apply Permissions** on the Device
page first.

### Failure behavior

- A network/API error, a wrong-architecture or non-`openfollow` asset, or a missing
  privilege aborts the update before any package is installed; the service keeps running.
- On failure the staged `/tmp/openfollow-update-*` artifact is removed and the General
  section shows the error.

## Service Details

- **Name:** `openfollow`
- **File:** `/etc/systemd/system/openfollow.service`
- **User:** `openfollow`
- **Display:** HDMI via Cage compositor
- **Auto-restart:** Yes (5s delay)
- **Logs:** `journalctl -u openfollow`

## Web UI unreachable after pinning it to an interface

`web_bind_iface` restricts the web UI to one interface. If that interface is
the wrong one, the UI stops answering where you are. Three ways back, cheapest
first:

1. **The station's own screen.** Settings → Network lists the address that
   reaches the UI on each adapter, and offers **Serve web UI on all
   interfaces**, which clears the pin and restarts. This works on a gamepad
   and needs no keyboard or network access.
2. **The pinned address.** The same screen shows which address does answer;
   browse to that from a machine on that network.
3. **Edit the config file** over SSH or on the SD card, then restart:

```bash
sudo -u openfollow sed -i '/^web_bind_iface/d' /var/lib/openfollow/config.toml
sudo systemctl restart openfollow
```

The pin only ever takes effect on restart, and an interface that has no
address at boot is ignored: the UI serves on every interface and the station
says so both on its own screen and in the web Interface Assignment panel. A
pin cannot leave the station with no web UI at all.

## NVMe for YOLO models (recommended on Pi)

If internal flash is nearly full, mount NVMe at `/mnt/nvme` and set:

```toml
[detection]
storage_path = "/mnt/nvme/openfollow/yolo"
```

Then ensure the path exists and is writable by the service user:

```bash
sudo mkdir -p /mnt/nvme/openfollow/yolo/models
sudo chown -R openfollow:openfollow /mnt/nvme/openfollow
```
