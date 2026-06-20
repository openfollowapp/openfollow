% from openfollow.web.labels import pretty_label
% # Trigger-specific input fields, swapped into Triggers tab
% # when kind dropdown changes.
% trigger = row.trigger
% kind_field = getattr(trigger, 'kind', 'stream')
<div class="trigger-fields" data-trigger-kind="{{kind}}">
% if kind == "stream":
    % current_rate = getattr(trigger, 'rate_hz', 30) if kind_field == 'stream' else 30
    % current_mode = getattr(trigger, 'mode', 'always') if kind_field == 'stream' else 'always'
    % current_min_change = getattr(trigger, 'min_change_m', 0.05) if kind_field == 'stream' else 0.05
    <div class="field">
        <label>Rate (Hz)</label>
        <select name="trigger.rate_hz">
            % for r in valid_rates:
                <option value="{{r}}" {{'selected' if r == current_rate else ''}}>{{r}} Hz</option>
            % end
        </select>
    </div>
    <!--
        Operator-feedback follow-up: send-always vs send-only-on-change.
        Rate above still controls the *sample* rate – the runtime polls
        the marker on that cadence. ``mode`` decides whether each
        evaluated tick actually goes on the wire. ``min_change_m`` is the
        per-axis threshold (max(|dx|, |dy|, |dz|) >= threshold to fire).
        The gate watches the row's *default marker* – message content
        is independent of which marker drives the gate. Rows with no
        default marker configured fire on every tick (no signal to
        compare). The helper text below makes that explicit.
    -->
    <div class="field">
        <label>Send</label>
        <select name="trigger.mode" data-osc-stream-mode-select>
            <option value="always" {{'selected' if current_mode == 'always' else ''}}>Send always</option>
            <option value="on_change" {{'selected' if current_mode == 'on_change' else ''}}>Send only on change</option>
        </select>
    </div>
    <!--
        Hide the threshold input when mode is ``always`` – the value
        on the wire is meaningless for that mode and showing it would
        invite "what does this do?" confusion. The select's
        ``data-osc-stream-mode-select`` hook in ``base.tpl`` toggles
        the wrapper's ``hidden`` attribute on change.
    -->
    <div class="field" data-osc-stream-min-change-wrap {{!'hidden' if current_mode != 'on_change' else ''}}>
        <label>Min change (m)</label>
        <input type="number" name="trigger.min_change_m"
               value="{{'%g' % current_min_change}}"
               step="0.01" min="0">
    </div>
    <div class="field span-3">
        <span class="field-note">
            Rate controls the sample rate. <strong>Send only on change</strong>
            skips the wire send when this row's <em>default marker</em>
            hasn't moved at least <em>min change</em> along any axis
            since the last send. The default marker is always the gate
            signal – message content (including
            <code>[x:N]</code> placeholders) is independent.
            To gate on a marker that isn't in the message, set it as
            this row's default marker. Rows with no default marker
            configured fire on every tick regardless of mode.
        </span>
    </div>
% elif kind == "hotkey":
    % current_key = getattr(trigger, 'key', '') if kind_field == 'hotkey' else ''
    % current_mods = set(getattr(trigger, 'modifiers', ()) if kind_field == 'hotkey' else ())
    % current_edge = getattr(trigger, 'edge', 'press') if kind_field == 'hotkey' else 'press'
    <div class="field">
        <label>Key</label>
        <select name="trigger.key">
            <option value="" {{'selected' if not current_key else ''}}>(none)</option>
            % for k in valid_keys:
                % if k:
                <option value="{{k}}" {{'selected' if k == current_key else ''}}>{{pretty_label(k)}}</option>
                % end
            % end
        </select>
    </div>
    <div class="field">
        <label>Modifiers</label>
        <div class="checkbox-group">
            % for m in valid_modifiers:
                <label class="inline-checkbox"><input type="checkbox" name="trigger.modifiers" value="{{m}}" {{'checked' if m in current_mods else ''}}> {{pretty_label(m)}}</label>
            % end
        </div>
    </div>
    <div class="field">
        <label>Edge</label>
        <select name="trigger.edge">
            % for e in valid_edges:
                <option value="{{e}}" {{'selected' if e == current_edge else ''}}>{{pretty_label(e)}}</option>
            % end
        </select>
    </div>
% elif kind == "controller_button":
    % current_btn = getattr(trigger, 'button', '') if kind_field == 'controller_button' else ''
    % current_edge = getattr(trigger, 'edge', 'press') if kind_field == 'controller_button' else 'press'
    <div class="field">
        <label>Button</label>
        <select name="trigger.button">
            <option value="" {{'selected' if not current_btn else ''}}>(none)</option>
            % for b in valid_buttons:
                % if b:
                <option value="{{b}}" {{'selected' if b == current_btn else ''}}>{{pretty_label(b)}}</option>
                % end
            % end
        </select>
    </div>
    <div class="field">
        <label>Edge</label>
        <select name="trigger.edge">
            % for e in valid_edges:
                <option value="{{e}}" {{'selected' if e == current_edge else ''}}>{{pretty_label(e)}}</option>
            % end
        </select>
    </div>
% elif kind == "midi_message":
    % # MIDI message trigger. Wire field is trigger.midi_type (not trigger.type,
    % # which is the kind discriminator). Save path reads both and repacks.
    % current_midi_type = getattr(trigger, 'type', 'note_on') if kind_field == 'midi_message' else 'note_on'
    % current_patch = getattr(trigger, 'patch_id', 0) if kind_field == 'midi_message' else 0
    % current_channel = getattr(trigger, 'channel', None) if kind_field == 'midi_message' else None
    % current_number = getattr(trigger, 'number', None) if kind_field == 'midi_message' else None
    % current_value = getattr(trigger, 'value', None) if kind_field == 'midi_message' else None
    % # ``valid_midi_types`` ships from the route handler; ``midi_patches``
    % # are the operator's :class:`MidiPatch` entries (id + label) from the
    % # MIDI page. The trigger references a patch by its integer id (#169);
    % # ``0`` = any patch.
    % _patches = defined('midi_patches') and midi_patches or []
    % _patch_ids = set(p['id'] for p in _patches)
    % # ``id`` per field is the OOB-swap target for the Capture
    % # flow below – when the broker classifies an incoming event
    % # the route handler emits a flat list of ``hx-swap-oob``
    % # snippets that overwrite each field's value-bearing element
    % # by id. Without per-row ids two simultaneous Capture flows
    % # on different rows would clobber each other.
    <div class="field">
        <label>Type</label>
        <select id="osc-midi-type-{{row.id}}" name="trigger.midi_type">
            % for t in valid_midi_types:
                <option value="{{t}}" {{'selected' if t == current_midi_type else ''}}>{{pretty_label(t)}}</option>
            % end
        </select>
    </div>
    <div class="field">
        <label>Patch</label>
        <select id="osc-midi-patch-{{row.id}}" name="trigger.patch_id">
            <option value="0" {{'selected' if not current_patch else ''}}>(any)</option>
            % # If stored patch was deleted (or hand-edited), surface a
            % # "missing" option so value round-trips (else silently
            % # collapses to browser's first pick).
            % if current_patch and current_patch not in _patch_ids:
                <option value="{{current_patch}}" selected>{{current_patch}} (missing)</option>
            % end
            % for patch in _patches:
                <option value="{{patch['id']}}" {{'selected' if patch['id'] == current_patch else ''}}>{{patch['label']}}</option>
            % end
        </select>
    </div>
    <div class="field">
        <label>Channel</label>
        <select id="osc-midi-channel-{{row.id}}" name="trigger.midi_channel">
            <option value="" {{'selected' if current_channel is None else ''}}>Any</option>
            % for ch in range(1, 17):
                <option value="{{ch}}" {{'selected' if ch == current_channel else ''}}>{{ch}}</option>
            % end
        </select>
    </div>
    <div class="field">
        <label>Number</label>
        % # Empty input means "Any" (matches the ``int | None`` shape
        % # on :class:`MidiMessageTrigger`). ``program_change`` /
        % # ``channel_pressure`` carry no per-message number – the
        % # config layer normalises ``number`` to ``None`` for those
        % # types regardless of what's typed here, so the field stays
        % # editable for consistency but the value is dropped on save.
        <input id="osc-midi-number-{{row.id}}" type="number" name="trigger.midi_number"
               value="{{'' if current_number is None else current_number}}"
               min="0" max="127" step="1" placeholder="Any">
    </div>
    <div class="field">
        <label>Value</label>
        <input id="osc-midi-value-{{row.id}}" type="number" name="trigger.midi_value"
               value="{{'' if current_value is None else current_value}}"
               min="0" max="127" step="1" placeholder="Any">
    </div>
    % # Capture flow – pure HTMX. Click arms the broker via the
    % # row-scoped section route, which returns the initial
    % # "waiting…" partial. The partial includes an inline poll
    % # Driver fetches state every 250ms; on captured, partial drops
    % # driver and emits OOB snippets. On timeout, show retry + Capture
    % # button. Pure-HTMX pattern (replaces broken JS hook).
    <div id="osc-midi-capture-status-{{row.id}}" class="field span-2">
        <button type="button" class="button-secondary"
                hx-post="/section/osc/midi/learn/arm/{{row.id}}"
                hx-target="#osc-midi-capture-status-{{row.id}}"
                hx-swap="innerHTML">
            Capture
        </button>
        <span class="field-note">
            Press a key, turn a knob, or move a fader. Capture
            populates the fields above with the next incoming MIDI
            message (10-second window).
        </span>
    </div>
% elif kind == "fader_on_change":
    % # Fader-on-change trigger. Combined dropdown lists indexed virtual
    % # faders + per-marker gamepad faders. Values prefixed (index:n / marker:id)
    % # so parser knows which was picked. Names show operator's labels
    % # (e.g. "Master" / "Marker 3 (Diva)").
    % current_fader = getattr(trigger, 'fader', 1) if kind_field == 'fader_on_change' else 1
    % current_marker = getattr(trigger, 'marker_id', 0) if kind_field == 'fader_on_change' else 0
    % current_rate = getattr(trigger, 'rate_hz', 30) if kind_field == 'fader_on_change' else 30
    % # The currently-selected source token, matched against each option.
    % current_source = ('marker:%d' % current_marker) if current_marker >= 1 else ('index:%d' % current_fader)
    % # ``virtual_fader_names`` / ``marker_fader_names`` are lists of
    % # (id, display_name) pairs threaded from the route handler. The
    % # indexed list falls back to "Fader N" for legacy callers; the
    % # marker list is empty when no markers are controlled.
    % _faders = defined('virtual_fader_names') and virtual_fader_names or [(i, 'Fader %d' % i) for i in range(1, 9)]
    % _markers = defined('marker_fader_names') and marker_fader_names or []
    % _marker_ids = [m[0] for m in _markers]
    <div class="field">
        <label>Fader</label>
        <select name="trigger.fader_source">
            <optgroup label="Virtual faders">
                % for idx, fader_name in _faders:
                    <option value="index:{{idx}}" {{'selected' if current_source == 'index:%d' % idx else ''}}>{{fader_name}}</option>
                % end
            </optgroup>
            % if _markers or current_marker >= 1:
            <optgroup label="Marker faders">
                % for mid, marker_name in _markers:
                    <option value="marker:{{mid}}" {{'selected' if current_source == 'marker:%d' % mid else ''}}>{{marker_name}}</option>
                % end
                % # The row's marker is no longer controlled (deprovisioned)
                % # – keep it selectable so editing the row doesn't silently
                % # switch the source to an indexed fader.
                % if current_marker >= 1 and current_marker not in _marker_ids:
                    <option value="marker:{{current_marker}}" selected>Marker {{current_marker}} (not controlled)</option>
                % end
            </optgroup>
            % end
        </select>
    </div>
    <div class="field">
        <label>Rate (Hz)</label>
        <select name="trigger.rate_hz">
            % for r in valid_rates:
                <option value="{{r}}" {{'selected' if r == current_rate else ''}}>{{r}} Hz</option>
            % end
        </select>
    </div>
    <div class="field span-2">
        <span class="field-note">
            Throttles fader-driven sends to at most this rate. A
            sweeping MIDI fader or a moving gamepad (marker) fader
            produces dozens of changes per second; the throttle keeps
            the wire output stable regardless.
        </span>
    </div>
% end
</div>
