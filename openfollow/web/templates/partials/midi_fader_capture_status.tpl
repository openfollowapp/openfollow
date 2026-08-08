% from openfollow.web.labels import pretty_label
% # Fader Learn capture status. State machine: armed/waiting → listening poll;
% # captured → banner + Learn-again button + OOB fragments for fader fields;
% # timeout/unavailable/cancelled → status banner + Retry/Learn button;
% # idle → fresh Learn button. OOB selects re-render full option lists
% # (whole-element swap) to avoid wiping other choices.
% _status = poll.get('status', 'idle')
% if _status in ('armed', 'waiting'):
    <span class="status-pill status-pill-info" aria-live="polite">{{_('Listening for MIDI…')}}</span>
    <span class="field-note">{{_('Move the fader or turn the knob you want to assign.')}}</span>
    <div hx-get="/section/midi/faders/{{fader_index}}/learn/poll"
         hx-trigger="every 250ms"
         hx-target="#midi-fader-learn-status-{{fader_index}}"
         hx-swap="innerHTML"></div>
% elif _status == 'captured':
    <span class="status-pill status-pill-ok" aria-live="polite">{{_('Captured')}}</span>
    <button type="button" class="button-secondary"
            hx-post="/section/midi/faders/{{fader_index}}/learn/arm"
            hx-target="#midi-fader-learn-status-{{fader_index}}"
            hx-swap="innerHTML">
        {{_('Learn again')}}
    </button>
    % # OOB fragments – overwrite the fader detail source fields by id.
    % _cap_patch = poll.get('patch_id') or 0
    <select id="midi-fader-source-patch-{{fader_index}}" name="source_patch" hx-swap-oob="true">
        <option value="0" {{'selected' if not _cap_patch else ''}}>{{_('(any patch)')}}</option>
        % # Surface a "missing" option if the captured patch id isn't in
        % # the current list (the captured device isn't bound to a patch,
        % # or the patch was deleted) so the value still round-trips.
        % _cap_patch_ids = set(p['id'] for p in midi_patches)
        % if _cap_patch and _cap_patch not in _cap_patch_ids:
            <option value="{{_cap_patch}}" selected>{{_cap_patch}} {{_('(missing)')}}</option>
        % end
        % for patch in midi_patches:
            <option value="{{patch['id']}}" {{'selected' if patch['id'] == _cap_patch else ''}}>{{patch['label']}}</option>
        % end
    </select>
    <select id="midi-fader-source-type-{{fader_index}}" name="source_midi_type" hx-swap-oob="true">
        % # Surface an "unsupported" option if the captured type isn't a valid
        % # fader source type (e.g. the operator hit a pad → note_on). Without
        % # it no option is marked selected and the browser silently defaults
        % # to the first (control_change) – a wrong mapping with no feedback.
        % # Mirrors the missing-patch handling above: the captured type
        % # round-trips, is flagged, and the operator can correct it.
        % _cap_type = poll.get('type')
        % if _cap_type and _cap_type not in valid_fader_midi_types:
            <option value="{{_cap_type}}" selected>{{pretty_label(_cap_type)}} {{_('(unsupported)')}}</option>
        % end
        % for mtype in valid_fader_midi_types:
            <option value="{{mtype}}" {{'selected' if mtype == _cap_type else ''}}>{{pretty_label(mtype)}}</option>
        % end
    </select>
    <input id="midi-fader-source-channel-{{fader_index}}" type="number" name="source_midi_channel"
           value="{{poll.get('channel') or 0}}"
           min="0" max="16" step="1" placeholder="{{_('0=any')}}" hx-swap-oob="true">
    <input id="midi-fader-source-number-{{fader_index}}" type="number" name="source_midi_number"
           value="{{0 if poll.get('number') is None else poll.get('number')}}"
           min="0" max="127" step="1" hx-swap-oob="true">
% elif _status == 'timeout':
    <span class="status-pill status-pill-warn" aria-live="polite">{{_('Timed out – no MIDI received')}}</span>
    <button type="button" class="button-secondary"
            hx-post="/section/midi/faders/{{fader_index}}/learn/arm"
            hx-target="#midi-fader-learn-status-{{fader_index}}"
            hx-swap="innerHTML">
        {{_('Retry')}}
    </button>
% elif _status == 'unavailable':
    <span class="status-pill status-pill-warn" aria-live="polite">{{_('MIDI subsystem not running')}}</span>
    <span class="field-note">{{_('Add a patch with a connected device in the MIDI Patches section above, then try again.')}}</span>
% elif _status == 'cancelled':
    <span class="status-pill status-pill-warn" aria-live="polite">{{_('Cancelled – another capture started')}}</span>
    <button type="button" class="button-secondary"
            hx-post="/section/midi/faders/{{fader_index}}/learn/arm"
            hx-target="#midi-fader-learn-status-{{fader_index}}"
            hx-swap="innerHTML">
        {{_('Learn')}}
    </button>
% else:
    <button type="button" class="button-secondary"
            hx-post="/section/midi/faders/{{fader_index}}/learn/arm"
            hx-target="#midi-fader-learn-status-{{fader_index}}"
            hx-swap="innerHTML">
        {{_('Learn')}}
    </button>
    <span class="field-note">
        {{_('Move the fader / turn the knob you want to drive this fader; Learn fills the fields above from the next incoming MIDI message (10-second window).')}}
    </span>
% end
