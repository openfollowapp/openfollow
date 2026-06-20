# Debian packaging (offline `.deb`)

OpenFollow ships as a self-contained Debian package for Raspberry Pi OS. The
`.deb` bundles the runtime **and all Python dependencies** in a private
virtualenv at `/opt/openfollow/venv`, so it installs and runs **with no
internet**. It installs the systemd service, the boot splash, and autostart, and
is the input artifact for the `rpi-image-gen` appliance image build
(see [Appliance image](#appliance-image-rpi-image-gen)).

> Build time is online; install time is offline. The build resolves PyPI wheels
> and the `pypsn` git dependency and compiles any sdists, then bakes everything
> into the package. `dpkg -i` afterwards fetches nothing.

> **GObject-Introspection stack is the OS's, not bundled.** The venv is created
> with `--system-site-packages` and the pip-built `PyGObject`/`pycairo` are
> removed, so `gi` / GStreamer come from the distro packages (`python3-gi`,
> `python3-gi-cairo`, the `gir1.2-*` typelibs). Reason: pip
> resolves `PyGObject>=3.56` (the new **girepository-2.0** ABI), which Debian
> Trixie (`python3-gi 3.50` on `libgirepository-1.0-1`) cannot load –
> `ImportError: libgirepository-2.0.so.0`. Using the OS bindings keeps the
> bindings, the typelibs and `libgirepository` coherent. Everything else (numpy,
> pygame, bottle, mido, the openfollow package, …) is still bundled in the venv.
>
> **Do not add `python3-gst-1.0` to `Depends`.** Its `gi/overrides/Gst.py`
> makes `Gst.Caps.get_structure()` return a `StructureWrapper` that lacks
> `.get_value()`, so `video/receiver.py` and `video/detection.py` raise
> `AttributeError` reading frame width/height. The app targets the raw typelib
> `Gst.Structure` (which has `.get_value()`); the GStreamer Python *overrides*
> package is intentionally absent. Keep it uninstalled on the build host too,
> since the `--system-site-packages` venv would otherwise pick up its overrides.

## Files

| Path | Purpose |
| --- | --- |
| `packaging/build-deb.sh` | Builds the `.deb` (run natively on the target Pi). |
| `packaging/debian/control.in` | Package metadata; `@VERSION@/@ARCH@/@PYTHON_DEP@/@ALSA_DEP@` are filled in by the build from the build host. |
| `packaging/debian/openfollow.service` | Main systemd unit (Cage Wayland kiosk, runs the bundled venv). |
| `packaging/debian/openfollow-splash.service` + `splash.sh` | Boot splash unit + KMS launcher. |
| `packaging/debian/render-splash.sh` | Pre-renders the splash PNG at build time. |
| `packaging/debian/{postinst,prerm,postrm}` | Create the `openfollow` user + linger, enable/disable the units. |
| `.github/workflows/release-deb.yml` | Release CI: builds the `.deb`, signs it into an `.ofupdate` bundle, and attaches both to the release – on a GitHub-hosted ARM64 runner (inside a `debian:trixie` container). |

## Install layout

```
/opt/openfollow/venv/                      all Python deps + the openfollow package
/usr/lib/systemd/system/openfollow.service
/usr/lib/systemd/system/openfollow-splash.service
/usr/share/openfollow/{openfollow.svg,splash.png,splash.sh,config.example.toml,install-ndi.sh,install-detection.sh}
/usr/share/openfollow/scripts/export_onnx.py   model-export script (Download Model action shells out to it)
/var/lib/openfollow/                       service user home + WorkingDirectory (config.toml)
/var/lib/openfollow/config.example.toml    first-boot seed; bootstrap copies it to config.toml
```

The example is shipped into `/var/lib/openfollow/` (not just `/usr/share`) because
`bootstrap_config_if_missing` looks for it next to `config.toml` – the
WorkingDirectory – so a fresh device seeds the curated config on first boot
instead of falling back to bare dataclass defaults.

The service runs as a dedicated `openfollow` login user (created by `postinst`)
with `loginctl enable-linger` so `/run/user/<uid>` exists at boot for the Cage
Wayland session. The unit binds web port 80 via `AmbientCapabilities=CAP_NET_BIND_SERVICE`.

## Build it

Run **on the target architecture/OS** – the venv embeds the build host's Python
ABI, so the package only runs on a matching Pi OS release. CI does this on a
GitHub-hosted ARM64 runner (`ubuntu-24.04-arm`, native arm64 – no cross-compile)
inside a `debian:trixie` container, so the venv's Python (3.13) matches the
Trixie image; the `ubuntu-24.04-arm` host itself ships Python 3.12.

```bash
# Build prerequisites (CI installs these automatically):
sudo bash scripts/install-system-deps.sh
sudo apt-get install -y python3-venv python3-dev build-essential pkg-config \
  dpkg-dev libgirepository-2.0-dev libcairo2-dev libasound2-dev \
  python3-cairo gir1.2-rsvg-2.0 librsvg2-bin

bash packaging/build-deb.sh            # -> dist/openfollow_<version>_<arch>.deb
```

The version comes from `pyproject.toml` (`0.2.3rc6` → Debian `0.2.3~rc6`, so a
pre-release sorts before the final release). Override with `OF_DEB_VERSION=…`.

## Install it (offline)

On a Pi whose base image already provides the `Depends` (GStreamer, GTK, Cage,
seatd, kanshi, the `gir1.2-*` typelibs, …):

```bash
sudo apt-get install -y ./openfollow_*.deb   # or: sudo dpkg -i ./openfollow_*.deb
```

`postinst` creates the user, enables linger, and starts the service. Open the
web UI at `http://<pi-ip>/`.

> In a chroot/image-build context (no running systemd), the maintainer scripts
> enable the units but skip the live start – exactly what `rpi-image-gen` needs.

## Release flow

`.github/workflows/release-deb.yml` triggers on a published GitHub Release (or
`workflow_dispatch`), builds on a GitHub-hosted `ubuntu-24.04-arm` runner (in a
`debian:trixie` container), then wraps the `.deb` in a **signed update bundle**
and attaches both the bundle and the raw `.deb` to the release.

The bundle `openfollow_<version>_<arch>.ofupdate` is a plain tar of three members:
the `.deb`, a `SHA256SUMS` line for it, and `SHA256SUMS.sig` – an openssl RSA
signature over the checksum, made with the release private key
(`OPENFOLLOW_RELEASE_PRIVKEY` Actions secret). The matching public key ships in
the package (`openfollow/runtime/release-pubkey.pem`); the device verifies the
signature and checksum before installing (see the in-app updater below).

Both are release assets: the signed `.ofupdate` is what the in-app updater
downloads and verifies, while the raw `openfollow_<version>_<arch>.deb` is
attached for a one-step manual `apt`/`dpkg` install (trusted via GitHub/HTTPS,
like the appliance images). Older releases that shipped only the bundle can have
the `.deb` extracted from it:

```bash
tar xf openfollow_<version>_<arch>.ofupdate openfollow_<version>_<arch>.deb
```

## Appliance image (rpi-image-gen)

For a turnkey deploy, the `.deb` is baked into a flashable **Raspberry Pi OS Lite
(Trixie, arm64) image**, built with
[`rpi-image-gen`](https://github.com/raspberrypi/rpi-image-gen). The image boots
headless straight into the Cage Wayland kiosk. Two board targets are built from the
**same `.deb`, the same custom layer, and a shared base config** – each per-board
config just includes the common base and overrides the device layer + image name:

| Path | Purpose |
| --- | --- |
| `packaging/image/config/openfollow-common.yaml` | Shared base config (not built directly): the `openfollow` account, image size, custom layer, and `.deb` path – everything both boards have in common. |
| `packaging/image/config/openfollow-cm5.yaml` | Build config: **CM5** device (`rpi-cm5` layer, 16 GB eMMC defaults). Includes the common base; overrides only the device layer + image name. |
| `packaging/image/config/openfollow-pi5.yaml` | Build config: **standard Pi 5** device (`rpi5` layer, SD-card default). Includes the common base; overrides only the device layer + image name. |
| `packaging/image/layer/openfollow.yaml` | Custom layer (shared): installs the `.deb` (apt resolves its `Depends` from the Pi OS repos), applies the headless boot config, enables SSH. |

**What the image adds over the `.deb`** (OS/boot concerns the `.deb` deliberately
does not own): silent kiosk boot (`cmdline.txt` console → tty3 + `quiet`,
`disable_splash=1`), HDMI pinned to 1920×1080@60 (DMT), `dtoverlay=disable-wifi` +
`disable-bt`, `getty@tty1` masked, and the SSH server enabled.

**Network stack: NetworkManager.** The layer installs and enables `network-manager`
(and masks `systemd-networkd`) instead of relying on the stock `systemd-net-min`
layer. OpenFollow's in-app Network page configures interfaces through `nmcli`, so
NetworkManager must own the links – on a `systemd-networkd` image the `nmcli` binary
is absent, the privilege broker's `network.nm.*` rows read as `unavailable`, and the
Network page silently falls back to a read-only view. This also matches the Ansible
deploy path, which already assumes NetworkManager. The `.deb`'s
`NetworkManager-wait-online` timeout drop-in bounds a cable-less boot to ~15 s.

**Single account.** `rpi-image-gen`'s `rpi-user-credentials` layer creates the
`openfollow` login user (password `openfollow`, **passwordless sudo**, in the
`video/render/input/audio/plugdev/dialout/sudo` groups) *before* the `.deb`
installs, so the deb's `postinst` finds it and only adds linger + enables the
units. That one account is **both** the SSH maintenance login and the Cage kiosk
service user. **Change the default password on a production unit.**

### Build it

Run natively on arm64 (CI uses a GitHub-hosted `ubuntu-24.04-arm` runner directly
– no container, since `rpi-image-gen`/`mmdebstrap` bootstrap a Trixie rootfs
regardless of host). `rpi-image-gen` is cloned at a pinned ref; the build is
online (apt pulls the deb's `Depends`), the result is offline.

```bash
cp dist/openfollow_*_arm64.deb packaging/image/openfollow.deb
git clone --depth 1 --branch v2.6.0 \
  https://github.com/raspberrypi/rpi-image-gen ~/rpi-image-gen
sudo ~/rpi-image-gen/install_deps.sh
cd ~/rpi-image-gen
# CM5 (eMMC):
./rpi-image-gen build -S /path/to/repo/packaging/image -c openfollow-cm5.yaml
# -> work/image-openfollow-cm5/openfollow-cm5.img (+ a zstd copy under deploy-*/)
# Standard Pi 5 (SD card):
./rpi-image-gen build -S /path/to/repo/packaging/image -c openfollow-pi5.yaml
# -> work/image-openfollow-pi5/openfollow-pi5.img
```

### Flash it (CM5 eMMC)

The CM5's eMMC is not removable, so flash it over USB with
[`rpiboot`](https://github.com/raspberrypi/usbboot): put the carrier in eMMC-boot
mode (nRPIBOOT jumper / `rpiboot -d`), then write the image to the exposed block
device with `rpi-imager` or `dd`. The rootfs auto-expands to fill the eMMC on
first boot.

### Flash it (standard Pi 5, SD card)

Write `openfollow-pi5_<version>.img.xz` to a microSD card with **Raspberry Pi
Imager** (**Choose OS → Use custom**) or `dd`, then boot the Pi 5 from it. The
rootfs auto-expands to fill the card on first boot.

### NVMe storage (auto-mount, presence-gated)

The CM5 ships an NVMe **slot**, not a guaranteed drive. A first-boot oneshot
(`openfollow-mount-nvme.service`, defined in `layer/openfollow.yaml` hook 2d)
handles whatever it finds, with an empty slot as an ordinary, fully supported
configuration:

- **No drive** – the unit's `ConditionPathExistsGlob=/dev/nvme*` makes systemd
  skip it cleanly; the appliance boots and runs from eMMC. Never an error, never
  a boot stall.
- **Drive with an existing filesystem** – mounted as-is at `/mnt/nvme`,
  persisted by-UUID in `/etc/fstab` with `nofail` (so a later drive removal
  can't block boot). The drive is **never reformatted**.
- **Truly blank drive** (no partition table, no filesystem, no on-disk
  signatures) – provisioned with a GPT label + a single ext4 partition, then
  mounted. The blank check is conservative: a drive that carries any
  partition/signature but nothing mountable is left untouched, not wiped.

On a mounted drive the hook creates `/mnt/nvme/openfollow/yolo/{models,cache}`
owned by the `openfollow` service user. Detection storage auto-resolves to
`/mnt/nvme/openfollow/yolo` whenever `/mnt/nvme` is mounted, else to a `yolo`
folder under the service working directory (`/var/lib/openfollow/yolo`) – see
`resolve_detection_storage_path`. There is no per-unit config edit and no web
field: storage is fully automatic (an absolute `detection.storage_path` in
`config.toml` still overrides). This mirrors the Ansible deploy's mount layout
(`scripts/ansible/install-raspberry-pi.yml`, `mount_nvme`).

### Release flow (CI)

`.github/workflows/release-deb.yml` builds the images in an `image` job that
`needs: build` and fans out over a `target: [cm5, pi5]` matrix. Each leg runs on
its own GitHub-hosted `ubuntu-24.04-arm` runner – so the two boards build in
parallel with an isolated `$RUNNER_TEMP` – downloads the `openfollow-deb` artifact
from the same run, clones `rpi-image-gen` at the pinned `v2.6.0`, builds
`openfollow-<target>.yaml`, compresses to `.img.xz`, uploads it as the
`openfollow-image-<target>` workflow artifact, and (on a published release)
attaches it to the GitHub Release next to the `.ofupdate` bundle.

### Software BOM (SBOM)

`rpi-image-gen` ships an `sbom-base` layer (pulled in transitively by the image
layer) that runs [Syft](https://github.com/anchore/syft) over the finished
rootfs on every build. That single scan catalogues **both** the apt/dpkg OS
packages **and** the Python packages in the bundled `/opt/openfollow/venv` – so
it is the authoritative list of every piece of software actually on the image,
with versions and declared licenses. Each `image` matrix leg collects its own and
attaches **`openfollow-<target>_<version>.spdx.json`** (SPDX 2.3 JSON) to the
GitHub Release alongside the `.img.xz`, and uploads it as the
`openfollow-sbom-<target>` workflow artifact on every run. It is the
machine-readable companion to the curated
[`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md); the optional
`detection` extra is not installed in the image, so it is documented there
rather than in the SBOM.

### Compliance gate

The release is gated on that SBOM. After it is collected,
[`packaging/image/check_sbom_compliance.py`](../packaging/image/check_sbom_compliance.py)
scans it and **fails the build before either release-attach** if it finds:

- **NDI** anywhere, by package name – the proprietary NDI SDK/plugin, whose
  license metadata is unreliable.
- **Detection extras** (`onnxruntime` / `opencv` / `ultralytics`) bundled in the
  **venv** – OpenFollow's own optional feature. These are permissively licensed,
  so a transitive copy in the OS media stack (Debian's `gstreamer1.0-plugins-bad`
  pulls `libonnxruntime`) is fine; the gate only asserts the extras stay out of
  the bundled venv. The name gate also keeps Ultralytics (AGPL-3.0) out of the
  venv regardless of its license.

There is **no blanket AGPL-license gate**: OpenFollow itself is
AGPL-3.0-or-later, so AGPL is the image's own license, not a forbidden one. The
Debian operating system it sits beside is an independent work combined on the
same medium (mere aggregation, predominantly GPL-2.0). This proves "OpenFollow's
detection feature and NDI are not shipped" is enforced, not just intended, and
keeps the proprietary NDI code out of the image. The accepted, redistributable
Raspberry Pi GPU firmware blob is proprietary but is **not** flagged.

## Removed: in-app git updater

The old web "Update From Source" updater (git pull + `poetry install` + restart)
has been **removed entirely** – the signed-`.deb` release installer is now the
sole update path. (It was already inert on a `.deb` install, where `/opt/openfollow`
is not a git checkout.)

The in-app updater is the **GitHub Releases signed-bundle installer**
([`openfollow/runtime/deb_update.py`](../openfollow/runtime/deb_update.py)): the
General → **Software Update** section's *Check & Install Latest* downloads the
newest release's `openfollow_<version>_<arch>.ofupdate` bundle, and *Offline
install* takes an operator-supplied `.ofupdate` over the LAN. Both paths verify
the bundle's signature (against the on-device public key) and the `.deb`'s
SHA-256 before installing, and fail closed otherwise. See
[`SERVICE.md`](./SERVICE.md#web-update-button) for the install/privilege details.
