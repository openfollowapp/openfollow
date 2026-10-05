# Security policy

## Reporting a vulnerability

Please report security problems privately, never in a public issue, pull request or discussion.

Use GitHub's private vulnerability reporting: on this repository's **Security** tab, choose **Report a vulnerability**. Only the maintainers see the report, and a fix can be prepared in a private fork before anything is public.

Include what you can of:

- the OpenFollow version (shown on the About page) and how it was installed (Raspberry Pi image, `.deb`, macOS app);
- what an attacker needs: a place on the show network, a browser session, the web PIN, physical access;
- the steps that reproduce it, and what happens;
- anything you have found about how to fix it.

Please do not attach a configuration file (`.ofsettings`) from a real show: it carries the camera logins and SRT passphrases the station uses. A diagnostics bundle has those redacted, so it can go into the private report, but it still describes the show network (addresses, host and station names), so never post one publicly.

## What happens next

We acknowledge a report as soon as we can and agree a disclosure date with you. The fix ships in a new release, and the advisory is published once that release is available, crediting you unless you would rather not be named.

## Supported versions

Security fixes go into the newest release. There are no long-term support branches, so update to the latest release to receive them. A release candidate is covered while it is the newest build.

## Scope

OpenFollow runs on show networks, usually with no internet connection. In scope are:

- the web UI and its API;
- traffic between stations: settings pushes, discovery, marker catalog sync;
- the update path: `.ofupdate` signature checks and the privileged helpers;
- the packaging: the `.deb`, the Raspberry Pi image and the macOS app.

These are known limits of the current design and are not treated as vulnerabilities on their own:

- PSN, OTP, RTTrPM and OSC carry no authentication by protocol design. OSC input can be restricted with `osc.allowed_sender_ips`.
- Without a web PIN, the web UI and its API are open to anyone on the network.
- The web UI is served over plain HTTP.
