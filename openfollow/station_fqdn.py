"""The fully qualified domain name an operator gives a station on a venue network.

One rule for every surface that reads or writes it: the config load, the web form, the
web server's accepted hosts and the HUD. Never resolved: the station only compares it.
"""

from __future__ import annotations

import re

FQDN_MAX_LEN = 253
# What an operator may type: the name plus the root's trailing dot.
FQDN_INPUT_MAX_LEN = FQDN_MAX_LEN + 1

_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")
_LABEL_MAX_LEN = 63


def canonical_host(raw: str) -> str:
    """*raw* as compared: trimmed, lower case, without the root's trailing dot."""
    host = raw.strip().lower()
    return host[:-1] if host.endswith(".") else host


def fqdn_problem(raw: str) -> str | None:
    """Why *raw* cannot be a station FQDN, or None when it can. A blank name is allowed."""
    name = canonical_host(raw)
    if not name:
        return None
    if len(name) > FQDN_MAX_LEN:
        return f"A name is at most {FQDN_MAX_LEN} characters."
    labels = name.split(".")
    if len(labels) < 2:
        return "Enter the full name with its domain, such as of-1.stage.example.com."
    for label in labels:
        if len(label) > _LABEL_MAX_LEN:
            return f"Each part between dots is at most {_LABEL_MAX_LEN} characters."
        if not _LABEL_RE.fullmatch(label):
            return "Use only letters, digits and hyphens between the dots, and no hyphen at the start or end of a part."
    if labels[-1].isdigit():
        return "Enter a name, not an IP address."
    if labels[-1] == "local":
        return "Names under .local are mDNS names, and the station already answers to its own."
    return None


def normalize_fqdn(raw: object) -> str:
    """*raw* as stored: the canonical name, or blank for anything that is not a valid FQDN."""
    if not isinstance(raw, str) or fqdn_problem(raw) is not None:
        return ""
    return canonical_host(raw)


def mdns_name() -> str:
    """``<hostname>.local``, or "" when the host has no usable name.

    The running hostname, never the station slug the config asks for: when the rename was
    skipped, the desired name is an address avahi never answers on.
    """
    from openfollow.privilege.device_repair import current_hostname

    name = current_hostname()
    return f"{name}.local" if name and name != "localhost" else ""


def web_ui_host(fqdn: str) -> str:
    """The name an operator opens the web UI by: *fqdn* when set, else the mDNS name."""
    return fqdn or mdns_name()
