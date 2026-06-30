%# General tab content. Top-level sections: Station Settings (identity +
%# display units + web access PIN), Network (live interface state), and
%# Software Update (signed-.deb release installer).
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

% if defined('update_feedback') and update_feedback:
<div class="update-notice">{{update_feedback}}</div>
% end

%# Restart notice lives at the top level so it doesn't get clipped
%# when one of the inner forms swaps. Polls /section/general every
%# 2s and reloads the page once the request succeeds – the polling
%# response is discarded (hx-swap=none).
% if defined('restarting') and restarting:
<div class="restart-notice"
     hx-get="/section/general"
     hx-trigger="every 2s"
     hx-swap="none"
     hx-on::after-request="if(event.detail.successful) window.location.reload()">App is restarting... Please wait.</div>
% end

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
        <span class="section-note">Identity, display units, and web access</span>
    </div>

    %# Station name + Web Access PIN – one form, saved together via
    %# /section/general. ``save-flash`` reproduces the green save
    %# confirmation the old standalone ``.section`` form had now that
    %# this form is nested inside the shared Station Settings box.
    <form id="general-network-section" class="save-flash {{'saved' if defined('saved') and saved else ''}} {{'restarting' if defined('restarting') and restarting else ''}}"
          hx-post="/section/general" hx-target="#general-network-section" hx-swap="outerHTML"
          hx-select="#general-network-section" hx-trigger="submit">
        <div class="group">
            <h3 class="group-title">Station name</h3>
            <div class="row">
                <div class="field wide">
                    <label>Station name</label>
                    <input id="general-psn-system-name" type="text" name="psn_system_name" value="{{config.psn_system_name}}"
                           placeholder="OpenFollow"
                           hx-get="/api/validate/general/psn_system_name" hx-trigger="blur changed delay:200ms"
                           hx-target="#general-psn-system-name-error" hx-swap="innerHTML" hx-include="closest form"
                           aria-describedby="general-psn-system-name-error" aria-invalid="false">
                    <span id="general-psn-system-name-error" class="field-error"></span>
                    <span class="field-note">Identifies this station in PSN output and on the network.</span>
                </div>
            </div>
        </div>
        <div class="group group--divider">
            <h3 class="group-title">Web access</h3>
            <div class="row">
                <div class="field wide">
                    <label>PIN (leave empty to disable)</label>
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
        <div class="group group--divider">
            <h3 class="group-title">Display units</h3>
            <div class="row">
                <div class="field wide">
                    <label for="general-unit-system">Unit system</label>
                    <select id="general-unit-system" name="unit_system">
                        <option value="metric" {{'selected' if _unit_system == 'metric' else ''}}>Metric (m, m/s)</option>
                        <option value="imperial" {{'selected' if _unit_system == 'imperial' else ''}}>Imperial (ft / in, ft/s)</option>
                    </select>
                    <span class="field-note">Units shown in the web UI and on-device overlay. Storage, OSC, and PSN/RTTrPM/OTP stay metric regardless.</span>
                </div>
            </div>
        </div>
    </form>

    %# "Show experimental features" opt-in; a separate form so its change
    %# does not also trigger the units form.
    <form id="general-experimental-section"
          hx-post="/settings/experimental" hx-swap="none" hx-trigger="change">
        <div class="group group--divider">
            <h3 class="group-title">Experimental features</h3>
            <div class="row">
                <div class="field checkbox-field wide">
                    <label for="general-show-experimental">Show experimental features</label>
                    <div class="checkbox-wrap">
                        <input type="checkbox" id="general-show-experimental" name="show_experimental_features"
                               {{'checked' if config.ui.show_experimental_features else ''}}
                               onchange="onExperimentalToggle(this)">
                    </div>
                </div>
            </div>
        </div>
    </form>

    %# Save sits at the box bottom (the display-units and experimental toggles
    %# above live-apply on change, so only Station name + Web Access PIN need it).
    %# ``form=`` keeps it submitting the network form from outside it.
    <div class="actions">
        <button type="submit" form="general-network-section" class="save-btn">Save</button>
    </div>
</div>

%# ------------------------------------------------------------------
%# Network Interface. One region toggling between read-only
%# view (disabled fields + live 5s poll) and the editable form (Apply /
%# Renew / Cancel). Lazy-loaded so the General render stays cheap and so it
%# sits OUTSIDE the Web Access form (the network ``<form>`` isn't nested);
%# the 5s poll lives inside the view, so edits are never clobbered.
%# ------------------------------------------------------------------
<div class="section" data-fold-key="general-network-interface" data-help="general-network-interface" data-fold-default="expanded">
    <div class="section-head">
        <h2>Network Settings</h2>
        <span class="section-note">Device IP address configuration</span>
    </div>
    <div id="network-interface" hx-get="/section/network/status" hx-trigger="load"
         hx-target="this" hx-swap="innerHTML">
        <p class="muted">Loading network configuration…</p>
    </div>
</div>

%# ------------------------------------------------------------------
%# 3. Software Update – GitHub Releases signed-bundle (.ofupdate) installer.
%#
%# Default-collapsed: most operators rarely update manually.
%# ------------------------------------------------------------------
<div id="general-software-update-section" class="section"
     data-fold-key="general-software-update" data-help="general-software-update" data-fold-default="collapsed">
    <div class="section-head">
        <h2>Software Update</h2>
        <span class="section-note">Install the latest release from GitHub</span>
    </div>

    <p class="field-note">Installed: v{{current_version}}</p>

    % if update_state == 'failed':
    <div class="update-notice error">{{update_message or 'Update failed.'}} {{update_error}}</div>
    % end
    %# ``update_feedback`` is rendered once at the top of this partial.

    <div class="actions">
        <button type="button" class="update-btn" onclick="openfollowCheckUpdate(this)" {{'disabled' if update_state in ('queued', 'running', 'restarting') else ''}}>
            Check &amp; Install Latest
        </button>
    </div>

    %# Offline install – collapsed by default; venues without internet expand this.
    <details class="inline-advanced" data-adv-key="general-offline-install">
        <summary>Offline install</summary>
        <div class="inline-advanced-content">
            <p class="field-note">Install an .ofupdate release bundle without internet access.</p>
            <input type="file" id="general-update-file" style="display:none"
                   accept=".ofupdate"
                   {{'disabled' if update_state in ('queued', 'running', 'restarting') else ''}}
                   onchange="document.getElementById('general-update-filename').textContent = this.files[0] ? this.files[0].name : ''">
            <div class="actions">
                <button type="button" class="btn-secondary"
                        onclick="document.getElementById('general-update-file').click()"
                        {{'disabled' if update_state in ('queued', 'running', 'restarting') else ''}}>
                    Choose file
                </button>
                <span id="general-update-filename" class="field-note" style="margin:0;align-self:center"></span>
            </div>
            <div class="actions">
                <button type="button" class="update-btn" onclick="openfollowUploadUpdate(this)" {{'disabled' if update_state in ('queued', 'running', 'restarting') else ''}}>
                    Upload &amp; Install
                </button>
            </div>
        </div>
    </details>
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

  // Start the install and confirm it was actually queued before polling – a
  // rejected request (another update in flight) must not leave the operator
  // staring at a locked progress modal that never resolves.
  let started;
  try {
    const resp = await fetch('/section/general/deb-update', {
      method: 'POST', headers: { 'Accept': 'application/json' },
    });
    started = await resp.json();
  } catch (err) {
    started = { ok: false, error: String(err) };
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

// Poll /api/update-status and reflect live install progress in a locked modal
// until a terminal state. The install runs detached, so failures only surface
// here – the modal stays open (no ×, no ESC, no backdrop close) so the operator
// can't dismiss progress and miss a failure. On success the page auto-reloads.
window.openfollowPollUpdate = async function (versionLabel) {
  let sawProgress = false;
  let lastState = '';
  let failCount = 0;
  let idleWaits = 0;
  let polls = 0;
  const MAX_FAIL = 200;   // ~5 min unreachable (restart window) before giving up
  const MAX_IDLE = 12;    // ~18 s queued-but-never-advancing -> assume it never started
  const MAX_POLLS = 600;  // ~15 min absolute backstop so the modal can't lock forever
  // Locked progress modal: spinner + status line, no footer buttons.
  const showUpdating = (msg) => openModal({
    title: 'Installing update',
    dismissable: false,
    bodyHTML: '<div class="modal-progress"><div class="modal-spinner"></div>'
      + '<p>' + escapeHTML(msg) + '</p></div>'
      + '<p>Keep this device powered on. It restarts automatically and this page'
      + ' reloads when the update finishes.</p>',
    footerButtons: [],
  });
  // Dismissable fall-back for every stuck/abandoned path so the operator is
  // never trapped behind the locked progress spinner.
  const showStuck = (title, msg) => openModal({
    title: title,
    bodyHTML: '<p>' + escapeHTML(msg) + '</p>',
    footerButtons: [{ label: 'Reload', kind: 'primary', onClick: () => location.reload() }],
  });
  showUpdating('Installing version ' + versionLabel + '…');
  for (;;) {
    await new Promise((r) => setTimeout(r, 1500));
    if (++polls >= MAX_POLLS) {
      showStuck('Still working…', 'The update is taking longer than expected. '
        + 'Reload the page to check the current version.');
      return;
    }
    let st;
    try {
      st = await (await fetch('/api/update-status', { headers: { 'Accept': 'application/json' } })).json();
      failCount = 0;  // reset on successful fetch
    } catch (err) {
      sawProgress = true;  // connection dropped – the service is restarting
      failCount++;
      if (failCount >= MAX_FAIL) {
        showStuck('Device unreachable', 'The device has been unreachable for several minutes. '
          + 'Check that it is powered on, then reload this page.');
        return;
      }
      showUpdating('Restarting onto version ' + versionLabel + '…');
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
    sawProgress = true;
    if (st.state !== lastState) {
      lastState = st.state;
      showUpdating(st.message || ('Installing version ' + versionLabel + '…'));
    }
  }
};

// Offline install: upload an operator-supplied .ofupdate bundle and install it
// locally (no GitHub, no internet). Defined on window so re-running after an HTMX
// section swap reassigns rather than redefines.
window.openfollowUploadUpdate = async function (btn) {
  const input = document.getElementById('general-update-file');
  const file = input && input.files && input.files[0];
  if (!file) {
    openModal({
      title: 'No file selected',
      bodyHTML: '<p>Choose an .ofupdate release bundle to install first.</p>',
      footerButtons: [{ label: 'Close', kind: 'primary', onClick: () => closeModal() }],
    });
    return;
  }

  const proceed = await modalConfirm({
    title: 'Install this file?',
    message: 'Install "' + file.name + '" and restart the device? '
      + 'It uploads over your local network – only install files you trust.',
    confirmLabel: 'Install now',
    cancelLabel: 'Cancel',
  });
  if (!proceed) return;

  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Uploading…';
  let info;
  try {
    // Send the bundle as the raw request body (not multipart); filename as a query param.
    const resp = await fetch('/section/general/deb-upload?filename=' + encodeURIComponent(file.name), {
      method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream' },
      body: file,
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
      title: 'Upload failed',
      bodyHTML: '<p>Could not install that file:</p><p>' + escapeHTML(info.error || 'Unknown error') + '</p>',
      footerButtons: [{ label: 'Close', kind: 'primary', onClick: () => closeModal() }],
    });
    return;
  }

  // The server has accepted + queued the install. Show the same "updating"
  // notice as the online path (the connection drops when the service restarts
  // mid-install). When the uploaded version is the same as or older than what's
  // installed, prepend an honest downgrade/reinstall note – the install is
  // already underway, so this is informational, not a cancel point.
  openfollowPollUpdate(info.version);
};
</script>
