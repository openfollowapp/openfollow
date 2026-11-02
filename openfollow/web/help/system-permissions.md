# System permissions

The error box at the top of the page means this station can't run its system actions: installing updates, saving to a USB storage device, changing network settings, setting the clock and the Pi camera setup. Tracking and the outputs keep working. It takes one fix over SSH from a computer on the same network; the box is gone once the page reloads.

In the commands, `<your-login>` is the user you created when you installed the operating system, and `<station-address>` is this station's IP address or `.local` name. Root can't log in over SSH with a password, so each command logs in as your user and switches to root with `su`, which asks for the **root** password.

## sudo is not installed

OpenFollow runs every system action through `sudo`. A Debian install set up with a root password has none. Install it and restart OpenFollow:

```
ssh -t <your-login>@<station-address> "su - -c 'apt-get update && apt-get install -y sudo && systemctl restart openfollow'"
```

This needs an internet connection on the station. Without one, download the `sudo` package for your Debian release and architecture on another computer, copy it over with `scp sudo_*.deb <your-login>@<station-address>:`, and install that file instead: `apt-get install -y ./sudo_*.deb` in place of `apt-get update && apt-get install -y sudo`.

## System permissions are missing

`sudo` is installed, but the rules the OpenFollow package ships are gone, for example deleted by hand. Reinstalling the package puts them back. Copy the OpenFollow `.deb` for this station's version (shown at the foot of the page) and architecture (`arm64` on a Raspberry Pi, `amd64` on a PC) to the station, then reinstall it:

```
scp openfollow_<version>_<arch>.deb <your-login>@<station-address>:
ssh -t <your-login>@<station-address> "su -c 'apt-get install -y --reinstall ./openfollow_<version>_<arch>.deb'"
```

The station restarts OpenFollow on its own.

## No SSH

If `ssh` reports "connection refused", the station has no SSH server. Run the part inside the double quotes, from `su` on, at a text console on the station itself.
