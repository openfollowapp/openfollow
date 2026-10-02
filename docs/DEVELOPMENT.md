# Development

Everything here is for development and source-based builds. For a normal show
deployment, use the [pre-built image or `.deb`](../README.md#installation).

The supported interpreter is **Python 3.13**. The optional detection / export
extras require it; a 3.14+ venv has no upstream wheels and will not install them.

## Development environment (macOS)

macOS support is intended for **local development and testing**. For production/show deployment, use Raspberry Pi.

Prerequisites:

```bash
# Taskfile (task runner)
brew install go-task

# Python via pyenv (recommended)
brew install pyenv
pyenv install 3.13
pyenv local 3.13

# Poetry (package manager)
curl -sSL https://install.python-poetry.org | python3 -

# System dependencies (GStreamer + GTK). The gst-plugins-* are now bundled into
# the single `gstreamer` formula, which also pulls in gtk+3 transitively.
brew install pygobject3 gstreamer
```

Install and run:

```bash
poetry install
poetry run openfollow
```

> On macOS the on-device **Settings → Open Web UI** action opens the running web
> UI in your default browser (the embedded overlay used on Linux/Pi isn't
> available on macOS). The UI is also reachable from any browser on the LAN.

### Troubleshooting

- **`poetry install` fails with `EnvCommandError` (macOS / pyenv).** If the
  traceback shows `platform.mac_ver()` returning an empty string, your pyenv
  Python was built without macOS framework support, so pip can't compute the
  wheel platform tag when it builds a dependency from source. Reinstall the
  interpreter with framework support and recreate the venv:

  ```bash
  PYTHON_CONFIGURE_OPTS="--enable-framework" pyenv install 3.13 --force
  pyenv local 3.13
  poetry env remove --all   # drop the venv built against the old interpreter
  poetry install
  ```

- **`objc[...]: Class X is implemented in both libgtk-3.0.dylib and
  libgtk-4.1.dylib` in the console, or GTK-related crashes.** Some other brew
  formula on the machine pulled in `gtk4` (OpenFollow only needs `gtk+3`, via
  the `pygobject3` / `gstreamer` formulas above). Two GTK major versions
  loaded into the same process is a known source of "spurious casting
  failures and mysterious crashes" per macOS's own Objective-C runtime
  warning. Check for the conflict and unlink `gtk4` while running OpenFollow:

  ```bash
  brew list --formula | grep -E '^gtk(\+3|4)$'
  brew unlink gtk4   # re-link later with `brew link gtk4` if another app needs it
  ```

### NDI input (macOS)

Unlike Linux, nothing needs building – the `ndisrc` element ships with the brew
`gstreamer` formula, and the NDI runtime (`libndi`) comes from NDI Tools, which also
gives you sources to receive (Test Patterns, Studio Monitor).

```bash
brew install --cask ndi-tools   # installs libndi to /usr/local/lib + NDI test apps
gst-inspect-1.0 ndisrc          # should print the element
```

Then pick **NDI** as the video source in the Web UI (or press `N` on-device).

### Build a macOS `.dmg`

Package the dev tree into a self-contained `.app` / `.dmg` (bundles Python, the
GTK/GStreamer stack, and the detection + export toolchains, so it runs on a clean
Mac). Requires the dev setup above plus `brew install librsvg create-dmg`:

```bash
make dmg    # -> dist/OpenFollow-<version>-<arch>.dmg
```

The output is single-arch and large (~2-2.5 GB, torch is bundled). The app is
ad-hoc signed, not notarized, so clear Gatekeeper's quarantine flag on first run:

```bash
xattr -dr com.apple.quarantine "/Applications/OpenFollow.app"   # or right-click -> Open
```

See [PACKAGING.md](PACKAGING.md#macos-dmg-developer-build) for details.

## Development environment (Linux – Debian/Ubuntu)

For local development on a Debian/Ubuntu desktop. Needs internet for `apt` and Poetry.

```bash
# System packages (GStreamer, GTK, build deps) – the same single source of truth
# the Pi installer uses. It also pulls the kiosk packages (cage/seatd/kanshi),
# which are unused but harmless on a desktop.
sudo bash scripts/install-system-deps.sh

# Poetry (package manager)
pipx install poetry   # or: curl -sSL https://install.python-poetry.org | python3 -
```

Install and run – reuse the system GStreamer/GTK bindings instead of building them:

```bash
poetry config virtualenvs.options.system-site-packages true
poetry install
poetry run openfollow
```

### NDI input (Linux)

NDI needs the proprietary [NDI SDK](https://ndi.video/for-developers/ndi-sdk/download/) plus
the `gst-plugin-ndi` GStreamer element. `scripts/install-ndi.sh` installs both; the one step
it can't do is the SDK download, which NDI gates behind a browser and a licence agreement.
Download the Linux SDK (`Install_NDI_SDK_*_Linux.tar.gz`), then run the script as your
normal user, not root – it calls `sudo` itself where needed:

```bash
scripts/install-ndi.sh path/to/Install_NDI_SDK_*_Linux.tar.gz   # or omit the path if it's in $HOME
gst-inspect-1.0 ndisrc   # should now print the element
```

Then pick **NDI** as the video source in the Web UI (or press `N` on-device). The manual
procedure the script automates is on the website:
[NDI® install](https://openfollow.app/docs/ndi-install.html).

## AI person detection (optional)

Use `-E detection` to **run** detection (ONNX Runtime inference backend):

```bash
poetry install -E detection          # ONNX Runtime backend
```

Use `-E export` to **convert** models. The `export` extra pulls the ultralytics
toolchain (heavy – torch; install it on a workstation, never the show Pi):

```bash
poetry install -E export              # ultralytics export toolchain
poetry run python scripts/export_onnx.py yolov8n.pt --imgsz 320 --opset 17
```

Or, from the Web UI **Person Detection → Model → Download model**, install the export tools
and export a catalogued model straight into `<storage_path>/models/` (workstation + internet
only).

If `-E detection` / `-E export` fails to install, check your venv is on Python
3.13: a 3.14+ venv has no upstream torch/scipy/onnxruntime wheels yet, so switch
the venv back to 3.13.

Then enable detection in the Web UI **Person Detection** section and pick your model from the
**Model** dropdown (only models already in the storage folder are listed). The storage location
is automatic; set `detection.storage_path` in `config.toml` to override it.
See [detection help](../openfollow/web/help/detection.md).

## Source-based install on a Raspberry Pi

These options install from a **source checkout** instead of a pre-built release –
use them for development, for Pi models without a published image, or when you want
to track a branch. They set up Poetry + Python dependencies on the device.

Target: **Raspberry Pi OS Lite 64-bit**. For a Compute Module 5, first flash plain
Raspberry Pi OS Lite (enable SSH and create a user – the Ansible examples assume
`pi`) using the [rpiboot steps](https://openfollow.app/docs/installation.html#cm5-flash),
then run one of the installers below. To rebuild the release artifacts, see
[PACKAGING.md](PACKAGING.md).

### Ansible

Install Ansible on your workstation:

```bash
# macOS
brew install ansible

# Debian/Ubuntu
sudo apt install -y ansible
```

Then, from your workstation (Linux/macOS), run the installer directly against the Pi (no inventory file required). Run the command from the repository root, and use an existing SSH account on the Pi (usually `pi` on Raspberry Pi OS):

```bash
ansible-playbook -i '<pi-ip>,' -u pi scripts/ansible/install-raspberry-pi.yml
```

Replace `<pi-ip>` with your Raspberry Pi’s IP address. If you paste the command from a rich-text source, make sure the quotes are plain ASCII `'` characters, not curly quotes.

If you need password-based SSH/sudo on first boot, add:

- `-k` to prompt for the SSH password
- `-K` to prompt for the sudo password (often not needed on Raspberry Pi OS)

After the first run, you can log in with:

```bash
ssh openfollow@<pi-ip>
# password: openfollow
```

By default the playbook installs from the public source at `https://openfollow.app/code/openfollowapp.git` (keyless HTTPS) – even when run from inside a git checkout. To install from a private repo instead, pass `-e openfollow_repo_url=git@host:owner/repo.git` (see the deploy-key note below).

Common overrides:

```bash
ansible-playbook -i '<pi-ip>,' -u pi scripts/ansible/install-raspberry-pi.yml \
  -e openfollow_repo_url=https://github.com/<your-org>/openfollow.git \
  -e openfollow_repo_version=main \
  -e nvme_mount_src='UUID=<your-nvme-uuid>'
```

**No NVMe drive?** NVMe mounting is on by default. If the Pi has no NVMe drive, disable it so the install doesn't try to partition/mount a missing disk:

```bash
ansible-playbook -i '<pi-ip>,' -u pi scripts/ansible/install-raspberry-pi.yml \
  -e mount_nvme=false
```

**Installing from a private fork (SSH)?** The public repo clones over HTTPS and needs no key. Only if `openfollow_repo_url` is a private SSH URL (`git@github.com:...`) do you need a deploy key. Generate one on your workstation and add the public key as a deploy key on the GitHub repo (Settings → Deploy keys):

```bash
ssh-keygen -t ed25519 -f ~/.ssh/openfollow-deploy -N ""
# then paste ~/.ssh/openfollow-deploy.pub into the repo's deploy keys
```

The playbook auto-detects this key at `~/.ssh/openfollow-deploy`, copies it to the Pi, and uses it for the clone. If it isn't present, the SSH steps are skipped (HTTPS install).

**Installing from a downloaded zip / tag archive (no internet clone)?** An extracted release archive has no `.git` and the Pi can't reach a clone URL, so point the installer at a local copy of the source instead. On your workstation, turn the extracted folder into a small git repo, copy it onto the Pi, then pass its path as `openfollow_repo_url`:

```bash
# In the extracted source folder on your workstation:
git init -q -b main && git add -A && git commit -qm "release"
rsync -a ./ openfollow@<pi-ip>:/home/openfollow/openfollow-src/

# Then run the installer pointing at the local copy:
ansible-playbook -i '<pi-ip>,' -u pi scripts/ansible/install-raspberry-pi.yml \
  -e openfollow_repo_url=/home/openfollow/openfollow-src
```

The `git` step runs on the Pi, so it clones from `/home/openfollow/openfollow-src` locally – no GitHub access needed.

What the playbook does (high level): installs system packages, creates an `openfollow` user, mounts an NVMe drive at `/mnt/nvme` if one is present (set `-e mount_nvme=false` to skip), installs Poetry + Python deps, enables `seatd`, and installs/starts the `openfollow` systemd service. It also reclaims the apt and Poetry download caches at the end of each run so repeated installs/updates don't fill a small SD card.

The Python install is **runtime-only** (`poetry install --only main`): it skips the dev/CI toolchain (ruff, pytest, mypy, bandit, pip-audit, …) the device never runs. Pass `-e openfollow_install_dev=true` only on a Pi you use to run `make ci` (see `make ci-remote`).

Ansible service defaults: `User=openfollow`, `WorkingDirectory=/home/openfollow/openfollow` (from `openfollow_user` and `openfollow_repo_dir` in `scripts/ansible/install-raspberry-pi.yml`).

When it completes, open the Web UI at `http://<pi-ip>:80` from another device on the same network.

> [!NOTE]
> The Ansible playbook intentionally does **not** install NDI (NDI SDK + `gst-plugin-ndi`). This is due to external licensing and build requirements – run `scripts/install-ndi.sh` on the Pi afterwards (see [NDI input (Linux)](#ndi-input-linux)).

### Manual install

Use this only if you can’t (or don’t want to) use Ansible.

1. Install Taskfile and Poetry:

   ```bash
   # Taskfile
   curl --location https://taskfile.dev/install.sh -o /tmp/task-install.sh
   sudo sh /tmp/task-install.sh -d -b /usr/local/bin
   rm /tmp/task-install.sh

   # Poetry
   curl -sSL https://install.python-poetry.org | python3 -
   ```

2. System packages (the same package list the Ansible installer uses):

   ```bash
   sudo bash scripts/install-system-deps.sh
   ```

   > The optional **3D Mouse** (3Dconnexion 6DOF) input is off by default. To use it, the
   > OpenFollow service user must be in the `plugdev` group and the udev rule
   > `packaging/udev/99-openfollow-3dmouse.rules` must be installed to `/lib/udev/rules.d/`
   > (then `sudo udevadm control --reload && sudo udevadm trigger`) so the `/dev/hidraw*`
   > node is group-accessible. The Ansible, `.deb` and image installs do this automatically;
   > for a manual install, copy the rule yourself. On macOS for development, `brew install hidapi`.

3. NDI (NDI input only): `scripts/install-ndi.sh`, see [NDI input (Linux)](#ndi-input-linux).

4. Install and run OpenFollow:

   ```bash
   # Allow Poetry to use the system-installed GStreamer/GTK bindings
   poetry config virtualenvs.options.system-site-packages true

   poetry install
   poetry run openfollow
   ```

5. Headless display over SSH (renders to HDMI via Cage):

   ```bash
   sudo systemctl enable --now seatd
   ./scripts/run-on-display.sh
   ```

6. Auto-start at boot (systemd):

   ```bash
   task install && task start
   ```

   `task install` copies `config/openfollow.service` as-is – it uses the static unit `config/openfollow.service`, which defaults to `User=pi` and `WorkingDirectory=/home/pi/openfollow`. If your setup uses another account/path (for example `openfollow`), update `User=`, `WorkingDirectory=`, `ExecStart=`, and `/run/user/<uid>` references there, then run `sudo loginctl enable-linger <user>`.

## Managing the service

On the Pi (SSH):

```bash
sudo systemctl status openfollow
sudo journalctl -u openfollow -f
sudo systemctl restart openfollow
```

The service name is always `openfollow`. The service user and path depend on the
install method: the packaged image / `.deb` install as the `openfollow` user under
`/opt/openfollow`. The Taskfile shortcuts and the web update path are in
[SERVICE.md](SERVICE.md).

## Testing

```bash
poetry run pytest

# or
poetry run pytest -m unit
poetry run pytest -m integration
poetry run pytest -m smoke

# Taskfile shortcut
task test
```

## Runtime baseline capture

Before and after runtime-related refactors, capture a baseline from existing telemetry (`/api/stats`):

```bash
poetry run python scripts/baseline_runtime_stats.py \
  --url http://127.0.0.1:8080/api/stats \
  --duration 60 \
  --interval 1
```

Optional JSON output for commit-to-commit comparison:

```bash
poetry run python scripts/baseline_runtime_stats.py --duration 60 --interval 1 --json
```

For reproducible comparisons, keep test conditions consistent (same input source, resolution, controller activity, and
capture duration).
