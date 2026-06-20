% from openfollow.web.labels import pretty_label
% # Single-fader detail form. Loaded when operator clicks a strip.
% # POST to /section/midi/faders/<idx> updates only that one fader
% # (others keep their persisted values). Variables: config (AppConfig),
% # fader_index (1..N), fader (VirtualFaderConfig), midi_patches (patch
% # dropdown options), valid_fader_midi_types (source dropdown).
% display_name = fader.name or 'Fader %d' % fader_index
<form class="midi-fader-detail"
      hx-post="/section/midi/faders/{{fader_index}}"
      hx-target="#midi-section"
      hx-swap="outerHTML">
    <h4 class="group-title">{{display_name}} <span class="midi-fader-detail-subtitle">– Fader {{fader_index}} settings</span></h4>

    <div class="row mf-ident-row">
        <div class="field mf-name">
            <label>Display name</label>
            <input type="text" name="name" value="{{fader.name}}" placeholder="Fader {{fader_index}}" maxlength="32" autofocus>
            <div class="field-note">Shown on the operator screen when "Show on Operator Screen" is on, and in trigger forms that reference this fader.</div>
        </div>
        <div class="field mf-default">
            <label>Default value</label>
            <input type="number" name="default_value" value="{{'%g' % fader.default_value}}" min="0" max="1" step="0.01">
            <div class="field-note">0.00 to 1.00. Sets the value at startup.</div>
        </div>
        % # Colour – the shared circle-swatch picker (color-picker.js
        % # auto-attaches via ``data-color-picker`` and syncs the sibling
        % # hidden input on change). Persisted on the fader so the strip
        % # tint + future HUD/OSC layers can read it.
        <div class="field mf-color-field">
            <label>Colour</label>
            <button type="button" class="color-swatch-trigger" data-color-picker="full" data-value="{{fader.color}}" aria-label="Fader colour"></button>
            <input type="hidden" name="color" value="{{fader.color}}">
        </div>
        <div class="field checkbox-field">
            <label>Show on Operator Screen</label>
            <div class="checkbox-wrap"><input type="checkbox" name="show_on_display" {{'checked' if fader.show_on_display else ''}}></div>
        </div>
    </div>

    % # Source – every fader accepts a MIDI source (or none). The
    % # gamepad no longer drives a fixed fader (it drives per-marker
    % # faders now), so there is no special-cased Fader 1; ``source_kind``
    % # is coerced to a valid choice by ``VirtualFaderConfig.__post_init__``.
    % # Row 1 – Source kind · Patch · Type. Source kind always shows;
    % # Patch + Type carry ``data-midi-source-detail`` so the kind toggle
    % # (base.tpl JS) hides just those two when "No source" is chosen,
    % # leaving Source kind in place.
    <div class="row mf-source-row" data-midi-source-kind-row>
        <div class="field mf-kind">
            <label>Source Type</label>
            <select name="source_kind" data-midi-source-kind-select>
                <option value="" {{'selected' if not fader.source_kind else ''}}>No source</option>
                <option value="midi" {{'selected' if fader.source_kind == 'midi' else ''}}>MIDI</option>
            </select>
        </div>
        <div class="field mf-patch" data-midi-source-detail {{'style="display:none"' if fader.source_kind != 'midi' else ''}}>
            <label>Patch</label>
            % # ``id`` per field is the OOB-swap target for the Learn flow
            % # below – on capture the poll route emits hx-swap-oob
            % # fragments that overwrite each of these by id with the
            % # captured message, mirroring the OSC trigger Capture flow.
            <select id="midi-fader-source-patch-{{fader_index}}" name="source_patch">
                <option value="0" {{'selected' if not fader.source_patch else ''}}>(any patch)</option>
                % for patch in midi_patches:
                <option value="{{patch['id']}}" {{'selected' if patch['id'] == fader.source_patch else ''}}>{{patch['label']}}</option>
                % end
            </select>
        </div>
        <div class="field mf-type" data-midi-source-detail {{'style="display:none"' if fader.source_kind != 'midi' else ''}}>
            <label>Message Type</label>
            % # Raw enum value stays in ``value`` (the backend matches on
            % # it); only the visible label is prettified. Folds into a
            % # shared label map alongside VALID_FADER_MIDI_TYPES later.
            <select id="midi-fader-source-type-{{fader_index}}" name="source_midi_type">
                % for mtype in valid_fader_midi_types:
                <option value="{{mtype}}" {{'selected' if mtype == fader.source_midi_type else ''}}>{{pretty_label(mtype)}}</option>
                % end
            </select>
        </div>
    </div>

    % # Row 2 – Channel · CC/Note · Learn. The whole row carries
    % # ``data-midi-source-detail`` so the kind toggle hides it wholesale
    % # when there's no MIDI source. Learn arms the shared single-shot
    % # MIDI capture broker under the ``fader:<idx>`` row id, then polls
    % # the status slot; on capture the status partial emits OOB
    % # fragments that fill the patch / type / channel / number fields
    % # above (same pure-HTMX pattern as the OSC trigger Capture button).
    <div class="row mf-source-row" data-midi-source-detail {{'style="display:none"' if fader.source_kind != 'midi' else ''}}>
        <div class="field mf-channel">
            <label>Channel</label>
            <input id="midi-fader-source-channel-{{fader_index}}" type="number" name="source_midi_channel" value="{{fader.source_midi_channel}}" min="0" max="16" step="1" placeholder="0=any">
            <div class="field-note">0 = any. 1–16 matches one specific channel.</div>
        </div>
        <div class="field mf-cc">
            <label>CC / Note number</label>
            <input id="midi-fader-source-number-{{fader_index}}" type="number" name="source_midi_number" value="{{fader.source_midi_number}}" min="0" max="127" step="1">
            <div class="field-note">Ignored for <code>channel_pressure</code>.</div>
        </div>
        <div class="field mf-learn">
            <label>&nbsp;</label>
            <div id="midi-fader-learn-status-{{fader_index}}" class="mf-learn-status">
                <button type="button" class="button-secondary"
                        hx-post="/section/midi/faders/{{fader_index}}/learn/arm"
                        hx-target="#midi-fader-learn-status-{{fader_index}}"
                        hx-swap="innerHTML">
                    Learn
                </button>
                <span class="field-note">
                    Move the fader / turn the knob to assign it
                    (10-second window).
                </span>
            </div>
        </div>
    </div>

    <div class="actions">
        <button type="submit" class="save-btn">Save fader</button>
    </div>
</form>
