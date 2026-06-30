% from openfollow.web.labels import pretty_label
% # MIDI Learn capture status. State machine: armed/waiting → listening poll;
% # captured → banner + restored Capture button + OOB fragments for form fields;
% # timeout/unavailable/idle → status banners. OOB fragments re-render full
% # option lists (HTMX whole-element swap). aria-live="polite" for a11y.
% _status = poll.get('status', 'idle')
% if _status in ('armed', 'waiting'):
    <span class="status-pill status-pill-info" aria-live="polite">Listening for MIDI…</span>
    <span class="field-note">Press a key, turn a knob, or move a fader.</span>
    <div hx-get="/section/osc/midi/learn/poll/{{row_id}}"
         hx-trigger="every 250ms"
         hx-target="#osc-midi-capture-status-{{row_id}}"
         hx-swap="innerHTML"></div>
% elif _status == 'captured':
    <span class="status-pill status-pill-ok" aria-live="polite">Captured</span>
    <button type="button" class="button-secondary"
            hx-post="/section/osc/midi/learn/arm/{{row_id}}"
            hx-target="#osc-midi-capture-status-{{row_id}}"
            hx-swap="innerHTML">
        Capture again
    </button>
    % # OOB fragments – each replaces the matching trigger-form
    % # input by id with the broker's classified event.
    <select id="osc-midi-type-{{row_id}}" name="trigger.midi_type" hx-swap-oob="true">
        % for t in valid_midi_types:
            <option value="{{t}}" {{'selected' if t == poll.get('type') else ''}}>{{pretty_label(t)}}</option>
        % end
    </select>
    <select id="osc-midi-patch-{{row_id}}" name="trigger.patch_id" hx-swap-oob="true">
        % _captured_patch = poll.get('patch_id') or 0
        <option value="0" {{'selected' if not _captured_patch else ''}}>(any)</option>
        % # Surface a "missing" option if the captured patch id isn't in
        % # the current list (deleted between arm and capture) so the
        % # value still round-trips on save.
        % _capture_patch_ids = set(p['id'] for p in midi_patches)
        % if _captured_patch and _captured_patch not in _capture_patch_ids:
            <option value="{{_captured_patch}}" selected>{{_captured_patch}} (missing)</option>
        % end
        % for patch in midi_patches:
            <option value="{{patch['id']}}" {{'selected' if patch['id'] == _captured_patch else ''}}>{{patch['label']}}</option>
        % end
    </select>
    <select id="osc-midi-channel-{{row_id}}" name="trigger.midi_channel" hx-swap-oob="true">
        % _captured_ch = poll.get('channel')
        <option value="" {{'selected' if _captured_ch is None else ''}}>Any</option>
        % for ch in range(1, 17):
            <option value="{{ch}}" {{'selected' if ch == _captured_ch else ''}}>{{ch}}</option>
        % end
    </select>
    <input id="osc-midi-number-{{row_id}}" type="number" name="trigger.midi_number"
           value="{{'' if poll.get('number') is None else poll.get('number')}}"
           min="0" max="127" step="1" placeholder="Any" hx-swap-oob="true">
    <input id="osc-midi-value-{{row_id}}" type="number" name="trigger.midi_value"
           value="{{'' if poll.get('value') is None else poll.get('value')}}"
           min="0" max="127" step="1" placeholder="Any" hx-swap-oob="true">
% elif _status == 'timeout':
    <span class="status-pill status-pill-warn" aria-live="polite">Timed out – no MIDI received</span>
    <button type="button" class="button-secondary"
            hx-post="/section/osc/midi/learn/arm/{{row_id}}"
            hx-target="#osc-midi-capture-status-{{row_id}}"
            hx-swap="innerHTML">
        Retry
    </button>
% elif _status == 'unavailable':
    <span class="status-pill status-pill-warn" aria-live="polite">MIDI subsystem not running</span>
    <span class="field-note">Open the Input → MIDI page to wire a backend, then come back here.</span>
% elif _status == 'cancelled':
    % # Another row armed Capture while this one was still polling.
    % # Single-slot broker superseded this session; restore
    % # Capture button and explain.
    <span class="status-pill status-pill-warn" aria-live="polite">Cancelled – another row started capturing</span>
    <button type="button" class="button-secondary"
            hx-post="/section/osc/midi/learn/arm/{{row_id}}"
            hx-target="#osc-midi-capture-status-{{row_id}}"
            hx-swap="innerHTML">
        Capture
    </button>
% else:
    <button type="button" class="button-secondary"
            hx-post="/section/osc/midi/learn/arm/{{row_id}}"
            hx-target="#osc-midi-capture-status-{{row_id}}"
            hx-swap="innerHTML">
        Capture
    </button>
    <span class="field-note">
        Press a key, turn a knob, or move a fader. Capture
        populates the fields above with the next incoming MIDI
        message (10-second window).
    </span>
% end
