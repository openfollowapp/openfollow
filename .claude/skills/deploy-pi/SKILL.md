---
name: deploy-pi
description: >-
  Use when deploying or provisioning OpenFollow to a Raspberry Pi via Ansible, or
  when asked to "deploy to the pi", "provision", "run the ansible playbook",
  "install on raspberry". Covers the install playbook, the ad-hoc inline-inventory
  invocation idiom (no inventory file in repo), related .deb/image artifacts, and
  the docs to keep in sync.
---

# Deploy OpenFollow to a Raspberry Pi

The provisioning playbook is
[`scripts/ansible/install-raspberry-pi.yml`](../../../scripts/ansible/install-raspberry-pi.yml).
It sets up the `openfollow` service account, clones the repo, builds a Poetry
venv (`poetry install --only main`; `-e install_detection_extra=true` adds the
detection extra, `-e openfollow_install_dev=true` adds the dev/CI group for a
testing Pi), templates the systemd
units (`openfollow.service`, `openfollow-splash.service`), mounts NVMe, and
configures silent boot.

## Confirm the target first

Deploying mutates a real device – confirm the host/IP with the operator before
running. Don't auto-proceed.

## Ad-hoc invocation (no inventory file in this repo)

Use an inline one-host inventory (the trailing comma is required) and SSH as `pi`:

```bash
ansible-playbook -i <host>, -u pi scripts/ansible/install-raspberry-pi.yml
```

`<host>` is the Pi's hostname or IP (e.g. `openfollow` or `10.0.0.5`). Privileged
tasks escalate via `become`.

## Related artifacts (context, not run by this skill)

- Offline `.deb`: `packaging/build-deb.sh` (native arch/OS; bundles a venv at
  `/opt/openfollow/venv`).
- Flashable Pi OS image + signed `.ofupdate` bundle: the `release-deb.yml`
  workflow.
- Background: [`docs/SERVICE.md`](../../../docs/SERVICE.md) (systemd + Ansible
  deployment) and [`docs/PACKAGING.md`](../../../docs/PACKAGING.md) (deb/image
  build + release flow).

## Docs gate

If provisioning steps, Ansible vars/flags, or systemd unit templates changed,
update `docs/SERVICE.md` / `docs/PACKAGING.md` to match in the same change.
