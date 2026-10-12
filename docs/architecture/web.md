# Web UI

Covers `web/`: routes, auth, peers, the Setup Wizard. The rules that apply everywhere are in [CLAUDE.md](../../CLAUDE.md).

## UI copy: explanations live in the help drawer (REQUIRED)

**Do not add inline help to web forms.** A `<label>` (plus a `placeholder` where
one genuinely helps) is the whole of a control's inline copy. Everything else –
what the setting does, when to use it, defaults, side-effects, caveats, examples,
and "manage X under Y" pointers – goes in that section's **help drawer markdown**
(`openfollow/web/help/<section>.md`, surfaced by the per-section `?` drawer via
`data-help="<section>"`).

- **A new `field-note` is a review blocker**, including a terse one-line pointer.
  "For a camera looking from upstage", "Comma-separated; e.g. …", and
  "Manage destinations under OSC Destinations." have all been stripped on sight.
  If the label alone doesn't identify the control, fix the label.
- When you add or change a control, update the matching
  `openfollow/web/help/<section>.md`. The help drawer is the single home
  for the explanation, so it can't drift between the form and the docs.
- The same goes for `title` tooltips carrying explanation. A `title` is fine for
  a badge or icon with no visible label (e.g. the "This session" interface badge).
- Existing `field-note`s predate this rule – leave them where they are unless you
  are already editing that block, then move the text into the help `.md`.
- `section-note` (the one-line subtitle beside a section's `<h2>`) is a *name*,
  not help: "Marker speed limits, default speed, and default position" is fine;
  a sentence teaching behaviour is not.

## Key routes
| Route | Method | Description |
|---|---|---|
| `/` | GET | Main config page |
| `/section/overview` | GET | Station list partial (HTMX-polled every 5s) |
| `/section/<name>` | GET | Config section partial |
| `/section/movement` | POST | Save movement settings (speed limits + default position) |
| `/section/general` | POST | Save + apply general settings |
| `/video-input/ndi/sources` | GET | NDI source `<option>` list (served by the NDI plugin's `web_routes()`) |
| `/network/interfaces/by_name` | GET | Interface `<option>` list (iface-keyed) |
| `/section/network/status` | GET | The interface card, every row read-only |
| `/section/network/edit/<iface>` | GET | Same card with that one interface's row editable |
| `/section/network/apply` | POST | Validate + write IPv4 config via the privileged adapter |
| `/section/network/renew` | POST | Renew DHCP lease via the privileged adapter |
| `/section/general/startup` | GET/POST | Start-at-boot switch; reads `systemctl is-enabled`, writes via the `service.enable` / `service.disable` grants |
| `/api/info` | GET | JSON: system_name, ip, port |
| `/api/peers` | GET | JSON: discovered peers |
| `/api/config/export` | GET | Download full config as JSON file |
| `/api/config/import` | POST | Import config JSON (preserves device identity); every section applies live |
| `/api/config/reset` | POST | Restore every setting to `AppConfig()` defaults, keeping the device-identity fields; restarts |
| `/api/config/<section>` | GET/POST | JSON config API |
| `/api/config/<section>/broadcast` | POST | Push config to all peers |
| `/wizard` | GET | Setup wizard (camera positioning + grid calibration) |
| `/api/video/snapshot/full` | GET | Full-resolution JPEG snapshot (503 if no feed) |
| `/api/wizard/project` | POST | Project grid corners + ref point to screen coords |
| `/api/wizard/unproject` | POST | Unproject screen points to world plane, return delta |
| `/api/wizard/solve` | POST | Run DLT solve from 4 corner screen positions |

## HTMX notes
- HTMX version: 1.9 – use `hx-on::after-request` (double colon), NOT `hx-on:htmx:after-request`
- Overview auto-refresh: `hx-trigger="every 5s"`
- Restart detection: polls `/section/general` every 2s; on success → `window.location.reload()`

## Setup wizard (`/wizard`)
7-step guided workflow for camera positioning and grid calibration:
1. **Preparation** – info + SVG stage layout illustration
2. **Grid Setup** – width, depth, z_offset, spacing, x_offset, y_offset; dynamic SVG illustration updates from input
3. **Video Source** – select and configure camera input (reuses video source UI); saved before moving on
4. **Camera Position** – pos_x/y/z, pitch/yaw/roll, fov; dynamic isometric illustration
5. **Reference Mapping** – draggable crosshair for coarse calibration (single known point); rigid-body shift of all corners
6. **Corner Pinning** – 4 draggable corners, DLT solve, solved camera params displayed
7. **Review & Apply** – read-only summary of all values + green overlay; Apply or Discard

Key implementation details:
- Server-side projection/unprojection via `/api/wizard/project` and `/api/wizard/unproject` to avoid JS↔Python coordinate math mismatches
- SVG viewBox matches native image resolution so coordinates map 1:1 regardless of CSS scaling
- `sessionStorage` persists wizard state across accidental navigation/refresh
- Touch-friendly: 44px hit areas, `touch-action: none`
- Keyboard arrow support for draggable points (debounced at 300ms)
- DLT-solved values back-propagate to form fields when navigating back
- Finish uses JSON API (`/api/config/camera`, `/api/config/grid`) to avoid resetting unrelated bool fields; `applyAndFinish()` must check each response's `.ok` before clearing session + redirecting (silent-drop regression guard in `tests/test_wizard.py`)
- **Input validation:** `/api/wizard/{project,unproject,solve}` must return 400 – not 500 – on malformed bodies. All float coercion and shape checks (including numpy array construction from caller-supplied coords) belong *inside* the try/except. Shared `_wizard_camera_params()` helper validates the 7-field camera vector
- **Step nav semantics:** `<nav aria-label="Setup wizard steps">` landmark with `aria-current="step"`, **not** `role=tablist`/`role=tab`. A tablist without `aria-selected` / `aria-controls` / `role=tabpanel` is an incomplete ARIA tab pattern; for a linear wizard, nav + aria-current is the correct lighter-weight semantics
- Snapshot blob URLs: `loadSnapshot()` must revoke the previous `URL.createObjectURL` after the new image loads to avoid a per-refresh memory leak
- "No feed" UX: each preview container has a sibling `.wizard-no-feed` placeholder div; `setPreviewVisibility()` toggles both so steps 5–7 never render blank when `/api/video/snapshot/full` fails

## Camera calibration

Calibration is **web-only**. The Setup Wizard's Corner-Pinning step drives a 4-corner DLT solve:

- 4-corner quad: DSL (downstage-left), DSR, USR, USL (in PSN coords)
- `scene/solver.solve_camera_dlt()` solves the camera params from the four screen↔world corner correspondences; the wizard back-propagates the solved values into the camera form.
- Server-side projection / unprojection (`/api/wizard/project`, `/unproject`, `/solve`) keeps the JS and Python coordinate math in sync.

## Peer authentication (`web/peer_auth.py`)
Peer-to-peer config broadcast is HMAC-signed. The web PIN is the HMAC key; the PIN itself never leaves the host.

- **Signature headers:** `X-Auth-Signature` (hex HMAC-SHA256) + `X-Auth-Timestamp` (unix seconds).
- **Signed payload:** `method\npath\nSHA256(body)\ntimestamp` – path includes the query string when present, so a query is semantically bound to the signed operation.
- **Timestamp window:** `TIMESTAMP_WINDOW_SECONDS = 30` – tolerant of typical NTP-free LAN skew, too narrow for offline replay to be practical.
- **Pre-auth body cap:** `_check_auth` requires `Content-Length` and rejects requests over `peer_auth.MAX_SIGNED_BODY_SIZE` (1 MiB) with 413 before reading – the verifier has to hash the full body before the HMAC proves the sender, so an unauthenticated client could otherwise force an unbounded spool.
- **The PIN never travels:** a third-party script authenticates by cookie (browser flow) or signs with `peer_auth.sign()`.
- **Host binding not included:** OpenFollow only performs broadcast-to-all operations and restricts targets to private IPs (`_is_private_peer_ip`), so cross-peer replay produces only the same effect a legitimate broadcast would have.
- **Offline brute-force note:** a captured signed request lets an attacker test candidate PINs offline. The HMAC construction only hides the PIN from the wire – it does not make a short / low-entropy PIN strong. Operators wanting meaningful resistance should use a longer shared secret; a future KDF derivation would raise the cost further.

## Browser auth (`_check_auth` + `login_submit`)
- `web_pin` non-empty → every non-asset route requires either a valid HMAC signature (peer path above) or the `_openfollow_auth` cookie.
- Cookie is set with `httponly=True`, `path="/"`, **`samesite="strict"`**. Strict blocks the browser from attaching the cookie to any cross-site request, which defeats CSRF without a separate token layer. OpenFollow is LAN-only so the reduced cross-site ergonomics are acceptable.
- `/login`, `/assets/*` and the About pages are exempt from auth. The login page shows no station state.

## Refused names and failed saves (`_check_auth`, `static/js/save-feedback.js`)
- **A change through a name the station does not accept** (an `Origin` / `Referer` host outside `_allowed_request_hosts()`, the CSRF / DNS-rebind defence) is answered by `_refused` in the shape its caller reads: JSON `{error, action, href}` for HTMX and script requests, and a page for a plain form post (`_is_navigation`: `Sec-Fetch-Mode: navigate`, else `Accept: text/html` without `HX-Request`) – the login page with the reason where "Incorrect PIN" sits, or `refused.tpl`. Never Bottle's bare 403 page.
- **The address offered** is the local address the connection arrived on (`SERVER_ADDR`, recorded by `_QuietHandler.get_environ`): the browser just reached it, and no DNS lookup is involved. Loopback (a proxy on the box) offers none.
- **On page load**, every full page (index, wizard, login, about) spreads `_page_host_context()`, so a page opened through such a name shows the red banner before anything is edited. The `Host` header decides it; the refusal stays the backstop, because a reverse proxy can make `Host` and `Origin` disagree.
- **Every save reports failure the same way** (`window.OpenFollow.saveError`): the form's ring flashes red for 0.55 s like the green save flash, and a red line under its actions says what the station observed plus one next step, until the next edit or save. Non-GET HTMX requests are covered globally (`htmx:responseError` / `htmx:sendError`); a script-driven save calls `saveError.show(box, info, lead, near)`. A dialog that closed before its request failed hands the line to the section last clicked. Don't add toasts, alerts or modals for a failed save; the offline update upload keeps the updater's own dialog, and the save to a USB storage device ends its dialog on the export's result (a refused start still reports through `saveError`). `tests/test_save_feedback.py` fails a template or input plugin that saves by script without the helper.

## Login throttle (`web/login_throttle.py`)
Per-IP exponential-backoff lockout on PIN authentication. Without this, a 4-digit PIN is exhausted in seconds over a LAN – no rate limit, no attempt counter, no back-off.

- **Curve:** 1 s after the first failure, doubling each subsequent failure (1, 2, 4, 8, 16, 30 …), capped at `_MAX_LOCKOUT_S = 30 s`. The exponent saturates at 60 to keep `2 ** n` inside float range under sustained probing – without this an attacker could drive `failures` past 1024 over ~8.5 hours and turn brute-force traffic into server 500s via `OverflowError`.
- **Two vectors, one throttle:** `setup_routes` builds a single `LoginThrottle` instance shared by `POST /login` (form path) and the peer-auth signature verifier in `_check_auth`. An attacker can't sidestep the lockout by alternating between vectors. The lockout check runs **before** any HMAC compute on the peer path, so a flood of bogus signatures can't keep the verifier hashing.
- **Reset on success:** a correct PIN check (`record_success`) clears that IP's history. Idle entries (no failure within `_RESET_AFTER_S = 10 min`) get GC'd by both per-IP cleanup in `remaining_lockout` and a periodic full sweep in `record_failure` (amortised O(1) per call) – required because an attacker rotating through one-off IPs would otherwise grow `_entries` without bound.
- **`_is_stale` requires both conditions:** an entry is collectable only when the idle threshold has passed *and* `lockout_until` has expired. Defends against a misconfigured `reset_after_s < max_lockout_s` where the per-IP GC could otherwise drop an entry mid-lockout, letting an attacker reset their failure count by waiting one idle window.
- **Wire response:** locked-out callers get `429 + Retry-After: <seconds>`. The header is computed via `math.ceil(remaining)` so it never overshoots the cap (`int(remaining) + 1` would advertise `31` on a `30 s` cap). `bottle.abort()` discards thread-local response headers, so the 429 is raised as `HTTPResponse(headers={"Retry-After": …})` directly – not via `abort()`.
- **Threading:** every public method takes the instance lock, and `now = self._clock()` is read **inside** the critical section so a contended caller can't compute `lockout_until = now + delay` against a stale timestamp.
- **Per-IP, not per-account:** acceptable because there is only one shared PIN. Legitimate users behind the same NAT share lockout – a known trade-off on a LAN show network. No persistence across server restarts; matches the LAN threat model.
- **Coverage:** 100% line + branch in `tests/test_login_throttle.py` (mock-clock unit tests, including thread-safety stress) + integration tests in `tests/test_web_server.py` (live server, real `urllib`, both vectors). Module is in the `mypy --strict` batch.

## Broadcast target restriction
`_send_config_to_peer` / `_send_config_import_to_peer` refuse to POST to non-private IPs – `ipaddress.IPv4Address.is_private` covers RFC 1918, link-local, and loopback, which is the set a legitimate peer can plausibly have. Closes the SSRF vector where a crafted beacon advertised an attacker-chosen endpoint (e.g. `web_port=22` or an internal service port).

## Service name as an argument
`_is_valid_service_name` rejects any value starting with `-` as well as anything outside its pattern, so `update_service_name` can never be parsed as a `systemctl` option; an invalid name falls back to the default unit (`_service_unit_name`).
