%# General tab content. Top-level sections: Station Settings (identity,
%# display units, web access PIN, and an Advanced Settings disclosure
%# holding the domain name, the start-at-boot switch and the Features table),
%# Network (live interface state), and Software Update (signed-.deb installer).
%#
%# Each form posts to ``/section/general`` (or the appropriate sub-
%# route) with its own ``id``, ``hx-target=#its-id`` and
%# ``hx-select=#its-id``, so the unchanged combined response is
%# re-rendered but only the originating section's outer element is
%# actually swapped – the other sections stay put.
%#
%# Pre-split, this whole file lived inside a single ``<form
%# id="general-section">`` and the polling div inherited the form's
%# ``hx-target=#general-section`` (HTMX inherits each hx-* attribute
%# independently from ancestors). Every 5s the network_state partial
%# was innerHTML'd into the whole form, wiping all sibling fields and
%# leaving just the interface dump in place of the form. Splitting the
%# outer form eliminates that inheritance edge – each polling /
%# action element below now declares ``hx-target`` explicitly.

% update_state = update_status.get('state', '') if defined('update_status') and update_status else ''
% update_message = update_status.get('message', '') if defined('update_status') and update_status else ''
% update_error = update_status.get('error', '') if defined('update_status') and update_status else ''

%# ------------------------------------------------------------------
%# Station Settings – the device's identity + access in one box (web
%# UI cleanup). Two forms share the box: the Display-units form
%# live-applies on ``change`` via /settings/unit-system (no Save), and
%# the Station-name + Web-Access-PIN form saves together on submit via
%# /section/general (its parser already accepts ``psn_system_name`` +
%# ``web_pin``). Each inner form swaps only itself (``hx-select`` by
%# id) so the shared heading and the sibling form stay put.
%# ------------------------------------------------------------------
% _unit_system = config.ui.unit_system
<div class="section" data-fold-key="general-station" data-help="general-station" data-fold-default="expanded">
    <div class="section-head">
        <h2>Station Settings</h2>
        <span class="section-note">Identity, display units, web access, and startup</span>
    </div>

    %# Station name + Web Access PIN – one form, saved together via
    %# /section/general with the FQDN joined from Advanced Settings. A save's
    %# green or red ring goes around the Station Settings box, which holds every
    %# field the Save writes.
    <form id="general-network-section" class="{{'saved' if defined('saved') and saved else ''}}"
          hx-post="/section/general" hx-target="#general-network-section" hx-swap="outerHTML"
          hx-select="#general-network-section" hx-select-oob="#general-station-fqdn-row" hx-trigger="submit">
        <div class="group">
            <div class="row">
                <div class="field wide">
                    <label for="general-psn-system-name">Station name displayed on the network</label>
                    <input id="general-psn-system-name" type="text" name="psn_system_name" value="{{config.psn_system_name}}"
                           placeholder="OpenFollow"
                           hx-get="/api/validate/general/psn_system_name" hx-trigger="blur changed delay:200ms"
                           hx-target="#general-psn-system-name-error" hx-swap="innerHTML" hx-include="closest form"
                           aria-describedby="general-psn-system-name-error" aria-invalid="false">
                    <span id="general-psn-system-name-error" class="field-error"></span>
                </div>
            </div>
            <div class="row">
                <div class="field wide">
                    <label for="general-web-pin">Web access PIN (leave empty to disable)</label>
                    <input id="general-web-pin" type="password" name="web_pin" value="{{config.web_pin}}"
                           placeholder="No PIN set" autocomplete="off"
                           hx-get="/api/validate/general/web_pin" hx-trigger="blur changed delay:200ms"
                           hx-target="#general-web-pin-error" hx-swap="innerHTML" hx-include="closest form"
                           aria-describedby="general-web-pin-error" aria-invalid="false">
                    <span id="general-web-pin-error" class="field-error"></span>
                </div>
            </div>
        </div>

    </form>

    <form id="general-display-section"
          hx-post="/settings/unit-system" hx-target="#general-display-section" hx-swap="outerHTML"
          hx-select="#general-display-section" hx-trigger="change">
        <div class="group">
            <div class="row">
                <div class="field wide">
                    % include('partials/unit_system_select.tpl', select_id='general-unit-system', unit_system=_unit_system)
                </div>
            </div>
        </div>
    </form>

    %# Advanced Settings: the controls an operator sets once and then leaves
    %# alone. Deliberately carries no ``data-adv-key`` - that attribute is what
    %# persists a disclosure's open state, and this one opens closed on every load.
    % _startup_here = defined('startup_supported') and startup_supported
    <details class="inline-advanced">
        <summary>Advanced Settings</summary>
        <div class="inline-advanced-content">
            %# The FQDN is saved with the station name: ``form=`` joins that form from
            %# here, and its ``hx-select-oob`` re-renders this row with the stored name.
            % from openfollow.station_fqdn import mdns_name
            <div class="group">
                <div class="row" id="general-station-fqdn-row">
                    <div class="field">
                        <label for="general-mdns-address">mDNS address</label>
                        <input id="general-mdns-address" type="text" value="{{mdns_name()}}" placeholder="No mDNS name"
                               disabled aria-readonly="true">
                    </div>
                    <div class="field">
                        <label for="general-station-fqdn">Custom domain name (FQDN)</label>
                        <input id="general-station-fqdn" type="text" name="station_fqdn" value="{{config.station_fqdn}}"
                               form="general-network-section" placeholder="of-1.stage.example.com"
                               autocomplete="off" autocapitalize="off" spellcheck="false"
                               hx-get="/api/validate/general/station_fqdn" hx-trigger="blur changed delay:200ms"
                               hx-target="#general-station-fqdn-error" hx-swap="innerHTML" hx-include="#general-network-section"
                               aria-describedby="general-station-fqdn-error" aria-invalid="false">
                        <span id="general-station-fqdn-error" class="field-error"></span>
                        % _fqdn_problems = fqdn_problems if defined('fqdn_problems') else ()
                        % if config.station_fqdn and _fqdn_problems:
                        <div class="notice warning" role="status">
                            <div>Not every interface sends this name by DHCP.</div>
                            % for _problem in _fqdn_problems:
                            <div class="notice-sub">{{_problem}}</div>
                            % end
                        </div>
                        % end
                    </div>
                </div>
                % if _startup_here:
                %# Autostart applies on change.
                <div class="row">
                    %# Fetched on its own so the ``systemctl`` read stays off the
                    %# General render path, and so the switch reports the host's state
                    %# rather than a stored flag.
                    <div id="startup-settings" class="field checkbox-field">
                        <p class="muted">Loading startup settings…</p>
                    </div>
                </div>
                % end
            </div>
            %# Features: the boxes save with the box's Save (``form=``); feature-save.js asks
            %# before a Save switches one off. ``data-saved`` is the stored state.
            % from openfollow.configuration import EXPERIMENTAL_FEATURES, OFFERED_FEATURES
            % from openfollow.web.labels import FEATURE_LABELS
            <div id="features-section" class="group">
                <h3 class="group-title" id="features-heading">Features</h3>
                <input type="hidden" name="features_submitted" value="1" form="general-network-section">
                <div class="feature-table-wrap" role="group" aria-labelledby="features-heading">
                    <table class="data-table feature-table" aria-label="Features">
                        <thead>
                            <tr><th scope="col"><span class="visually-hidden">On</span></th><th scope="col">Feature</th><th scope="col">Description</th></tr>
                        </thead>
                        <tbody>
                            % for _key in OFFERED_FEATURES:
                            % _label = FEATURE_LABELS[_key]
                            % _id = 'features-' + _key.replace('_', '-')
                            % _on = config.ui.feature_on(_key)
                            <tr>
                                <td class="feature-check">
                                    <input type="checkbox" id="{{_id}}" name="{{_key}}" form="general-network-section"
                                           aria-describedby="{{_id}}-description" data-saved="{{'1' if _on else '0'}}"
                                           data-title="{{_label.title}}" data-off="{{_label.when_off}}" {{'checked' if _on else ''}}>
                                </td>
                                <th scope="row"><label for="{{_id}}">{{_label.title}}</label>{{!' <span class="badge-experimental">Experimental</span>' if _key in EXPERIMENTAL_FEATURES else ''}}</th>
                                <td id="{{_id}}-description" class="feature-description">{{_label.description}}</td>
                            </tr>
                            % end
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </details>

    %# Save sits at the box bottom (the display-units and autostart toggles
    %# above live-apply on change, so only the station name, PIN, FQDN and features need it).
    %# ``form=`` keeps it submitting the network form from outside it.
    <div class="actions">
        <button type="submit" form="general-network-section" class="save-btn">Save</button>
    </div>
</div>


% if defined('startup_supported') and startup_supported:
<script>
// Start-at-boot switch. Turning it OFF is confirmed first: the web UI is part
// of OpenFollow, so after the next reboot there is no page left to undo it
// from. Defined on window so re-running this script after an HTMX section swap
// reassigns rather than redefines. Uses the shared modal helpers from base.tpl.
//
// This script is the region's only writer. A second one - an hx-trigger="load"
// on the region itself - repainted it from a read issued before the write,
// so the switch flicked back to the state it had just left. Every request
// takes a sequence number and a reply older than the newest one is dropped.
window._autostartSeq = window._autostartSeq || 0;

window.applyAutostartHtml = function (html, seq) {
  if (seq !== window._autostartSeq) return;
  const region = document.getElementById('startup-settings');
  if (region) region.innerHTML = html;
};

window.loadAutostart = async function () {
  const seq = ++window._autostartSeq;
  try {
    const resp = await fetch('/section/general/startup');
    if (resp.redirected || !resp.ok) return;
    window.applyAutostartHtml(await resp.text(), seq);
  } catch (err) {
    /* The region keeps its placeholder; the next section render retries. */
  }
};

window.onAutostartToggle = async function (input) {
  const enable = input.checked;
  if (!enable) {
    const proceed = await modalConfirm({
      title: 'Stop OpenFollow starting at boot?',
      message: 'Not recommended. This web interface is part of OpenFollow, so once this '
        + 'station restarts there is no page to switch it back on \u2013 you would need SSH, '
        + 'or a keyboard and screen on the station itself.',
      confirmLabel: 'Turn it off',
      cancelLabel: 'Keep starting at boot',
      danger: true,
    });
    if (!proceed) { input.checked = true; return; }
  }
  input.disabled = true;
  const seq = ++window._autostartSeq;
  const body = new URLSearchParams();
  if (enable) body.set('autostart', 'on');
  const saveError = window.OpenFollow.saveError;
  const box = saveError.boxFor(input);
  let html;
  let failure = null;
  try {
    const resp = await fetch('/section/general/startup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
    });
    // A redirect here is the PIN login; rendering what it returns would put the
    // sign-in page inside the switch's box.
    if (resp.redirected) failure = { error: 'Your session has expired.', action: 'Reload the page and sign in again.' };
    else if (!resp.ok) failure = await saveError.fromResponse(resp);
    else html = await resp.text();
  } catch (err) {
    failure = saveError.UNREACHABLE;
  }
  if (failure) {
    input.disabled = false;
    input.checked = !enable;
    saveError.show(box, failure, 'Not changed.');
    return;
  }
  saveError.clear(box);
  window.applyAutostartHtml(html, seq);
};

window.loadAutostart();
</script>
% end

%# ------------------------------------------------------------------
%# Network Interface. One region toggling between read-only
%# view (disabled fields + live 5s poll) and the editable form (Apply /
%# Renew / Cancel). Lazy-loaded so the General render stays cheap and so it
%# sits OUTSIDE the Web Access form (the network ``<form>`` isn't nested);
%# the 5s poll lives inside the view, so edits are never clobbered.
%# ------------------------------------------------------------------
<div class="section" data-fold-key="general-network-interface" data-help="general-network-interface" data-fold-default="expanded">
    <div class="section-head">
        <h2>Network Interface Settings</h2>
    </div>
    <div id="network-interface" hx-get="/section/network/status" hx-trigger="load"
         hx-target="this" hx-swap="innerHTML">
        <p class="muted">Loading network configuration…</p>
    </div>
</div>

%# ------------------------------------------------------------------
%# 2b. Network Interface Assignment – which network each function uses. Sits
%# below Network Interface Settings ("what address does this NIC have") and reads
%# the same interface list. Lazy-loaded like the network region so the
%# General render doesn't pay for the address resolution.
%# ------------------------------------------------------------------
%# Advanced only: in Simple the station interface carries every function.
% if config.network_mode == 'advanced':
<div id="interface-assignment" hx-get="/section/interface_assignment" hx-trigger="load"
     hx-target="this" hx-swap="innerHTML">
    <p class="muted">Loading interface assignment…</p>
</div>
% end

%# ------------------------------------------------------------------
%# 3. Software Update – GitHub Releases signed-bundle (.ofupdate) installer.
%#
%# Default-collapsed: most operators rarely update manually. Hidden on
%# hosts where the .deb installer can't run (macOS) – ``update_supported``.
%# ------------------------------------------------------------------
% if defined('update_supported') and update_supported:
<div id="general-software-update-section" class="section"
     data-fold-key="general-software-update" data-help="general-software-update"
     data-fold-default="{{'expanded' if (defined('update_available') and update_available) else 'collapsed'}}">
    <div class="section-head">
        <h2>Software Update</h2>
        <span class="section-note">Online from GitHub, or from an .ofupdate file</span>
    </div>
    % busy = 'disabled' if update_state in ('queued', 'running', 'restarting') else ''

    <p class="field-note">Installed: v{{current_version}}</p>

    %# At most one box: a failed install outranks the release online sync found.
    % if update_state == 'failed':
    <div class="update-notice error">{{update_message or 'Update failed.'}} {{update_error}}</div>
    % elif defined('update_available') and update_available:
    <div class="notice" role="status">
        <div>Version {{latest_version}} is available.</div>
        <div class="notice-sub">Install it online, or install an .ofupdate file.</div>
    </div>
    % end

    %# The same two actions in every state: online first, the offline bundle beside it.
    <div class="actions">
        <button type="button" class="update-btn" onclick="openfollowCheckUpdate(this)" {{busy}}>
            Install Latest Version Online
        </button>
        <input type="file" id="general-update-file" style="display:none"
               accept=".ofupdate" {{busy}}
               onchange="openfollowUploadUpdate(this)">
        <button type="button" class="btn-secondary"
                onclick="document.getElementById('general-update-file').click()" {{busy}}>
            Install .ofupdate File
        </button>
    </div>
</div>

<script>
// Software Update: check GitHub for a newer release, then ask the operator to
// confirm before installing. Defined on window so re-running the script after an
// HTMX section swap simply reassigns it (no duplicate-definition error). Uses the
// shared modal helpers (openModal / modalConfirm) defined in base.tpl.
window.openfollowCheckUpdate = async function (btn) {
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Checking…';
  let info;
  try {
    const resp = await fetch('/section/general/deb-update/check', {
      headers: { 'Accept': 'application/json' },
    });
    info = await resp.json();
  } catch (err) {
    info = { ok: false, error: String(err) };
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }

  if (!info.ok) {
    openModal({
      title: 'Update check failed',
      bodyHTML: '<p>Could not reach the update server:</p><p>' + escapeHTML(info.error || 'Unknown error') + '</p>',
      footerButtons: [{ label: 'Close', kind: 'primary', onClick: () => closeModal() }],
    });
    return;
  }

  if (!info.available) {
    openModal({
      title: 'Up to date',
      bodyHTML: '<p>This device already runs the latest version (' + escapeHTML(info.current) + ').</p>',
      footerButtons: [{ label: 'Done', kind: 'primary', onClick: () => closeModal() }],
    });
    return;
  }

  const confirmed = await modalConfirm({
    title: 'Update available',
    message: 'Version ' + info.latest + ' is available (installed: ' + info.current
      + '). Install it now? The device restarts automatically when finished.',
    confirmLabel: 'Install now',
    cancelLabel: 'Not now',
  });
  if (!confirmed) return;
  openfollowUpdateProgress('Starting update…');

  // Start the install and confirm it was actually queued before polling – a
  // rejected request (another update in flight) must not leave the operator
  // staring at a locked progress modal that never resolves.
  let started;
  try {
    started = await openfollowUpdateJSON('/section/general/deb-update', {
      method: 'POST', headers: { 'Accept': 'application/json' },
    }, 15000);
  } catch (err) {
    started = { ok: false, error: err.name === 'AbortError' ? 'The device did not answer.' : (err.message || String(err)) };
  }
  if (!started.ok) {
    openModal({
      title: 'Could not start update',
      bodyHTML: '<p>' + escapeHTML(started.error || 'The update could not be started.') + '</p>',
      footerButtons: [{ label: 'Close', kind: 'primary', onClick: () => closeModal() }],
    });
    return;
  }
  openfollowPollUpdate(info.latest);
};

// The device's JSON answer, or throw: ``null``, an array or a bare value parses
// fine but has none of the fields, and reading one would leave the lock stuck.
window.openfollowUpdateObject = function (data) {
  if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('Unexpected response from the device.');
  return data;
};

// JSON request that gives up after ``ms``, body read included: anything awaited
// behind the locked modal must settle, or the page stays inert with no way out.
window.openfollowUpdateJSON = function (url, opts, ms) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), ms);
  return fetch(url, { ...opts, signal: ctrl.signal })
    .then((resp) => resp.json())
    .then(openfollowUpdateObject)
    .finally(() => clearTimeout(timer));
};

// Locked progress modal (no ×, no ESC, no backdrop close). Opened once, then
// only its status line is rewritten, so frequent progress updates don't
// restart the spinner or steal focus. The line takes focus, since the page
// behind is inert and the modal has no button.
window.openfollowUpdateProgress = function (msg) {
  const line = document.getElementById('update-progress-msg');
  if (line) {
    line.textContent = msg;
    return;
  }
  openModal({
    title: 'Installing update',
    dismissable: false,
    bodyHTML: '<div class="modal-progress"><div class="modal-spinner"></div>'
      + '<p id="update-progress-msg" role="status" aria-live="polite" tabindex="-1">' + escapeHTML(msg) + '</p></div>'
      + '<p>Keep this device powered on. It restarts automatically and this page'
      + ' reloads when the update finishes.</p>',
    footerButtons: [],
  });
};

// Poll /api/update-status and reflect live install progress in a locked modal
// until a terminal state. The install runs detached, so failures only surface
// here – the modal stays open so the operator can't dismiss progress and miss
// a failure. On success the page auto-reloads. Callers open the modal first.
window.openfollowPollUpdate = async function (versionLabel) {
  let sawProgress = false;
  let unreachableSince = null;
  let idleWaits = 0;
  const beganAt = performance.now();
  // Monotonic elapsed time, not poll counts: a poll can wait out its own
  // timeout, and a wall-clock jump must not end the wait early or late.
  const UNREACHABLE_MS = 5 * 60 * 1000;  // restart window before giving up
  const MAX_IDLE = 12;                   // ~18 s queued-but-never-advancing -> assume it never started
  const GIVE_UP_MS = 15 * 60 * 1000;     // absolute backstop so the modal can't lock forever
  const showUpdating = openfollowUpdateProgress;
  // Dismissable fall-back for every stuck/abandoned path so the operator is
  // never trapped behind the locked progress spinner.
  const showStuck = (title, msg) => openModal({
    title: title,
    bodyHTML: '<p>' + escapeHTML(msg) + '</p>',
    footerButtons: [{ label: 'Reload', kind: 'primary', onClick: () => location.reload() }],
  });
  for (;;) {
    await new Promise((r) => setTimeout(r, 1500));
    if (performance.now() - beganAt >= GIVE_UP_MS) {
      showStuck('Still working…', 'The update is taking longer than expected. '
        + 'Reload the page to check the current version.');
      return;
    }
    let st;
    try {
      st = await openfollowUpdateJSON('/api/update-status', { headers: { 'Accept': 'application/json' } }, 5000);
      unreachableSince = null;
    } catch (err) {
      // Only a dropped connection (fetch's TypeError) shows the service is
      // restarting; a timeout or a garbled answer is just no answer, and must
      // not turn a job that never started into "Update complete".
      const dropped = err instanceof TypeError;
      if (dropped) sawProgress = true;
      if (unreachableSince === null) unreachableSince = performance.now();
      if (performance.now() - unreachableSince >= UNREACHABLE_MS) {
        showStuck('Device unreachable', 'The device has been unreachable for several minutes. '
          + 'Check that it is powered on, then reload this page.');
        return;
      }
      if (dropped) showUpdating('Restarting onto version ' + versionLabel + '…');
      continue;
    }
    if (st.state === 'failed') {
      openModal({
        title: 'Update failed',
        bodyHTML: '<p>' + escapeHTML(st.message || 'The update did not complete.') + '</p>'
          + (st.error ? '<p>' + escapeHTML(st.error) + '</p>' : ''),
        footerButtons: [{ label: 'Close', kind: 'primary', onClick: () => closeModal() }],
      });
      return;
    }
    if (st.state === 'idle') {
      if (!sawProgress) {
        // Queued but the worker never advanced (a no-op "already up to date"
        // race, or it never picked the job up) – surface what the server
        // reported and let the operator reload instead of polling forever.
        if (++idleWaits >= MAX_IDLE) {
          showStuck('Update did not start', st.message
            || 'The update did not start – the device may already be up to date.');
          return;
        }
        continue;
      }
      // Briefly confirm, then reload onto the new version automatically.
      openModal({
        title: 'Update complete',
        dismissable: false,
        bodyHTML: '<div class="modal-progress"><div class="modal-spinner"></div>'
          + '<p>Update complete – reloading…</p></div>',
        footerButtons: [],
      });
      setTimeout(() => location.reload(), 1200);
      return;
    }
    if (st.state === 'queued') {
      // Waiting to be picked up is not progress: a job that never leaves the
      // queue ends as "did not start", not in the 15-minute backstop.
      if (++idleWaits >= MAX_IDLE) {
        showStuck('Update did not start', 'The update was queued but never started. '
          + 'Reload the page to check the current version.');
        return;
      }
      continue;
    }
    sawProgress = true;
    // Every step (download, verify, install) reports under one state, so
    // follow the message, not the state.
    showUpdating(st.message || ('Installing version ' + versionLabel + '…'));
  }
};

// Offline install: choosing an .ofupdate bundle installs it locally (no GitHub,
// no internet), with no second step. Called from the file input's change event;
// defined on window so re-running after an HTMX section swap reassigns rather
// than redefines.
window.openfollowUploadUpdate = async function (input) {
  const file = input.files && input.files[0];
  if (!file) return;
  // Cleared so picking the same file again after a failure fires change again.
  input.value = '';

  // The request covers the upload and the device's signature and checksum
  // check, so the locked modal goes up now, not when it answers.
  openfollowUpdateProgress('Uploading update…');
  let info;
  try {
    // XHR rather than fetch: only XHR reports upload progress.
    info = await new Promise((resolve) => {
      const xhr = new XMLHttpRequest();
      // A slow link is fine; bytes that stop moving, or a device that never
      // answers once it has the bundle, end the wait instead of the lock.
      let stalled = false;
      let watchdog = 0;
      let sent = 0;
      const arm = (ms) => {
        clearTimeout(watchdog);
        watchdog = setTimeout(() => { stalled = true; xhr.abort(); }, ms);
      };
      // Every outcome settles here: a progress event arriving afterwards must
      // not reopen the lock over the result.
      const finish = (result) => {
        clearTimeout(watchdog);
        xhr.upload.onprogress = xhr.upload.onload = null;
        resolve(result);
      };
      xhr.open('POST', '/section/general/deb-upload?filename=' + encodeURIComponent(file.name));
      // Raw request body, not multipart; the filename rides along as a query param.
      xhr.setRequestHeader('Content-Type', 'application/octet-stream');
      xhr.upload.onprogress = (ev) => {
        // Only bytes that actually moved count as progress.
        if (ev.loaded > sent) {
          sent = ev.loaded;
          arm(30000);
        }
        if (ev.lengthComputable) {
          openfollowUpdateProgress('Uploading update… ' + Math.floor((ev.loaded / ev.total) * 100) + '%');
        }
      };
      xhr.upload.onload = () => {
        arm(120000);
        openfollowUpdateProgress('Verifying update…');
      };
      xhr.onload = () => {
        try {
          finish(openfollowUpdateObject(JSON.parse(xhr.responseText)));
        } catch (err) {
          finish({ ok: false, error: 'Unexpected response from the device (HTTP ' + xhr.status + ').' });
        }
      };
      // An abort or timeout ends the request without onload, like a network error.
      xhr.onerror = xhr.onabort = xhr.ontimeout = () => finish({
        ok: false,
        error: stalled ? 'The device stopped responding.' : 'The upload was interrupted.',
      });
      arm(30000);
      xhr.send(file);
    });
  } catch (err) {
    info = { ok: false, error: String(err) };
  }

  if (!info.ok) {
    openModal({
      title: 'Upload failed',
      bodyHTML: '<p>Could not install that file:</p><p>' + escapeHTML(info.error || 'Unknown error') + '</p>',
      footerButtons: [{ label: 'Close', kind: 'primary', onClick: () => closeModal() }],
    });
    return;
  }

  // Accepted and queued; the connection drops while the service restarts.
  openfollowPollUpdate(info.version);
};
</script>
% end
