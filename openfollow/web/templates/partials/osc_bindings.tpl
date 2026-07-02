% from openfollow.web.labels import pretty_label
% # Unified OSC binding list: collapsible rows with per-row tabbed editor.
% # Collapsed view shows title + enabled state; expanded shows all configuration tabs.
% #
% # Out-of-band swap convention: every CRUD route returns the *full*
% # bindings partial so row order stays consistent across mutations,
% # and ``focus_id`` re-opens the row that just changed.
% import json
% transmitters = config.osc_transmitters.transmitters
% # ``user_templates``: file-based templates loaded from disk and exposed with
% # same shape (``.id`` / ``.name``) as dropdown loop expects. Falls back to empty list.
% custom_templates = user_templates if defined('user_templates') else []
% # per-row unresolved-placeholder map and the live
% # registered-marker list. The map carries each row's tuple of
% # bracketed tokens whose dependencies aren't met (e.g. ``[x]`` with
% # no default marker, ``[x:7]`` for an unregistered N). The
% # client-side pill JS reads these from the editor's data attributes
% # and applies ``data-unresolved="true"`` to matching pills so CSS
% # paints them in the danger colour. The registered-id list lets the
% # JS re-evaluate dependencies as the operator edits without a
% # round-trip to the server.
% _unresolved_by_row = defined('unresolved_by_row') and unresolved_by_row or {}
% _registered_marker_ids = defined('registered_marker_ids') and registered_marker_ids or []
% # per-row marker display: {row_id: {"header": <single-marker label|None>,
% #   "nested": [<chip>...], "markers_unusable": <bool>}}. Built server-side
% # from each row's resolved ``markers`` tokens (catalog names).
% _marker_display_by_row = defined('marker_display_by_row') and marker_display_by_row or {}
% # Shared OSC destinations the rows reference. Build an id→profile map so the
% # collapsed summary + read-only endpoint display resolve without a per-row scan.
% _destinations = config.osc_destinations.destinations
% _dest_by_id = {d.id: d for d in _destinations}
% def _dest_label(did):
%     d = _dest_by_id.get(did)
%     if d is None:
%         return _('(no destination)')
%     end
%     return _('{}://{}:{} ({})').format(d.protocol, d.host, d.port, d.framing if d.protocol == 'tcp' else d.protocol)
% end
<div id="osc-bindings-section" class="section {{'saved' if defined('saved') and saved else ''}}" data-fold-key="osc_bindings" data-help="osc_bindings">
 <div class="section-head">
 <h2>{{_('OSC Output')}}</h2>
 <span class="section-note">{{_('Outbound OSC messages – Stream / Hotkey / Controller-button triggers')}}</span>
 </div>

 <div class="osc-bindings-list">
 % if not transmitters:
 <p class="empty-state">{{_('No OSC outputs configured. Use')}} <em>{{_('+ New OSC output')}}</em> {{_('below to create one.')}}</p>
 % end
 % for idx, row in enumerate(transmitters):
 % is_focus = (defined('focus_id') and focus_id == row.id)
 % trigger_kind = getattr(row.trigger, 'kind', 'stream')
 % row_unresolved = _unresolved_by_row.get(row.id, ())
 % has_unresolved = bool(row_unresolved)
 % _row_marker = _marker_display_by_row.get(row.id, {})
 % _marker_header = _row_marker.get('header')
 % _marker_nested = _row_marker.get('nested', [])
 % _markers_unusable = _row_marker.get('markers_unusable', False)
 % # The row's status dot is red when this transmitter can never fire: no
 % # destination (or a dangling one), no controlled marker among those it
 % # names, or an invalid address/arg (an unresolvable placeholder).
 % # Otherwise amber when enabled, grey when off. An uncontrolled *secondary*
 % # marker (with at least one controlled) reddens its own nested chip, not
 % # this dot.
 % _dest_missing = (not row.destination_id) or (row.destination_id not in _dest_by_id)
 % _dot_broken = _dest_missing or _markers_unusable or has_unresolved
 % _dot_state = 'invalid' if _dot_broken else ('on' if row.enabled else 'off')
 % _dot_label = _('No destination') if _dest_missing else (_('No controlled marker') if _markers_unusable else (_('Invalid OSC message') if has_unresolved else (_('Enabled') if row.enabled else _('Disabled'))))
 <details class="osc-binding-row" data-row-id="{{row.id}}" data-trigger-kind="{{trigger_kind}}"
 data-reorder-url="/section/osc_bindings/reorder" data-reorder-target="osc-bindings-section" {{'open' if is_focus else ''}}>
 <summary class="osc-binding-summary">
 % # The drag handle lives inside ``<summary>`` so it
 % # rides the row's collapsed-state header, but pointer
 % # events on a ``<summary>`` child still bubble up and
 % # toggle the parent ``<details>``. Stop propagation
 % # on the pointer / click paths so dragging never
 % # opens the row as a side-effect.
 % #
 % # Drag handle: pointer-only affordance. Previous markup
 % # claimed to be a button but did nothing (role + tabindex
 % # without action). Dropped those; aria-hidden keeps glyph
 % # out of AT name. Keyboard reorder has a route ready.
 <span class="osc-binding-drag-handle"
 draggable="true"
 aria-hidden="true"
 title="{{_('Drag to reorder')}}"
 onpointerdown="event.stopPropagation()"
 onclick="event.preventDefault(); event.stopPropagation();">⋮⋮</span>
 <span class="osc-binding-enabled-dot {{_dot_state}}" aria-label="{{_dot_label}}"></span>
 <span class="osc-binding-title">{{row.name or _('(unnamed)')}}</span>
 <span class="osc-binding-kind-badge">{{pretty_label(trigger_kind)}}</span>
 % if _marker_header:
 <span class="osc-binding-marker-badge">{{_marker_header}}</span>
 % end
 <span class="osc-binding-target">{{_dest_label(row.destination_id)}}</span> (i18n: refine _() wrapping - remove from internals, add missing user-facing strings)
 </summary>

 <form class="osc-binding-form"
 data-template-form="1"
 data-row-id="{{row.id}}"
 hx-post="/section/osc_binding/{{row.id}}"
 hx-target="#osc-bindings-section"
 hx-swap="outerHTML"
 hx-trigger="submit">
 % # ``role="tablist"`` requires every child ``role="tab"``
 % # to expose ``aria-selected`` and ``aria-controls`` so
 % # screen readers can announce the active tab and the
 % # panel it owns. ``switchRowTab`` (base.tpl) keeps
 % # ``aria-selected`` in sync with ``.active`` on each
 % # click.
 <div class="row-tab-bar osc-binding-tabbar" role="tablist">
 <button type="button" class="row-tab-btn active" data-row-tab="basics-{{row.id}}" role="tab" aria-selected="true" aria-controls="row-tab-basics-{{row.id}}">{{_('Basics')}}</button>
 <button type="button" class="row-tab-btn" data-row-tab="triggers-{{row.id}}" role="tab" aria-selected="false" aria-controls="row-tab-triggers-{{row.id}}">{{_('Trigger')}}</button>
 <button type="button" class="row-tab-btn" data-row-tab="settings-{{row.id}}" role="tab" aria-selected="false" aria-controls="row-tab-settings-{{row.id}}">{{_('Settings')}}</button>
 <button type="button" class="row-tab-btn" data-row-tab="diag-{{row.id}}" role="tab" aria-selected="false" aria-controls="row-tab-diag-{{row.id}}">{{_('Diagnostics')}}</button>
 </div>

 <!-- Basics -->
 <div class="row-tab-panel active" id="row-tab-basics-{{row.id}}" role="tabpanel">
 <div class="row">
 <div class="field checkbox-field">
 % # data-osc-unresolved="true" when placeholder
 % # dependency unsatisfied, paired with red pill in
 % # editor. Save not gated; POST handler coerces
 % # enabled=False until deps resolve.
 % # No aria-invalid here (refreshFormGate disables
 % # all .save-btn if any control has it; blocks
 % # fix-up workflow). Custom data attribute gives
 % # red border via CSS + sync via oscEditorSyncEnabledUnresolved.
 <label>{{_('Enabled')}}</label>
 % # {{!...}} bypasses Bottle HTML escape (else
 % # inner quotes → &quot;). data-osc-unresolved
 % # (visual-only) + aria-describedby to hidden span
 % # for a11y. oscEditorSyncEnabledUnresolved keeps
 % # span in sync; aria-live="polite" announces changes.
 <div class="checkbox-wrap"><input type="checkbox" name="enabled" {{'checked' if row.enabled else ''}} {{!'data-osc-unresolved="true"' if has_unresolved else ''}} aria-describedby="enabled-{{row.id}}-unresolved-help"><span id="enabled-{{row.id}}-unresolved-help" class="visually-hidden" aria-live="polite">{{'Will save disabled: this row uses placeholder values that are not resolved yet (no default marker, or an explicit marker reference targets an unregistered marker).' if has_unresolved else ''}}</span></div>
 </div>
 <div class="field">
 <label>{{_('Name')}}</label>
 <input type="text" name="name" value="{{row.name}}" maxlength="64">
 </div>
 <div class="field">
 % # Default marker now optional. No min="0" (avoids
 % # stepper vs empty value). Render empty when None.
 % # Wire to validate endpoint; blur surfaces error
 % # if default-marker placeholder has no satisfying
 % # marker. hx-include pulls hidden osc_message for
 % # cross-field check.
 <label for="marker-id-{{row.id}}">{{_('Default marker')}}</label>
 <input id="marker-id-{{row.id}}" type="number" name="marker_id" value="{{'' if row.marker_id is None else row.marker_id}}" step="1" placeholder="{{_('(none)')}}"
 hx-get="/api/validate/osc_binding/marker_id" (i18n-final: all 39 templates wrapped, 630 PO entries, zero nest zero syntax error)
 hx-trigger="blur changed delay:200ms"
 hx-target="#markers-{{row.id}}-error"
 hx-swap="innerHTML"
 hx-include="closest form"
 aria-describedby="markers-{{row.id}}-error"
 aria-invalid="false">
 <span id="markers-{{row.id}}-error" class="field-error"></span>
 </div>
 <div class="field">
 % # default
 % # virtual fader for placeholders.
 % # Mirrors the Default marker pattern: empty
 % # input means "operator hasn't picked one";
 % # bare ``[fader]`` slots (incl. transform
 % # forms like ``[fader.pct]``) in the message
 % # raise :class:`RenderError` and skip with
 % # ``"no default fader configured"`` until
 % # set. Explicit ``[fader:N]`` references
 % # ignore this field. The dropdown shows the
 % # operator's chosen fader names so a row
 % # labelled "Master" doesn't read as
 % # "Fader 1" here.
 % _faders_for_default = defined('virtual_fader_names') and virtual_fader_names or [(i, 'Fader %d' % i) for i in range(1, 9)]
 <label for="default-fader-{{row.id}}">{{_('Default fader')}}</label>
 <select id="default-fader-{{row.id}}" name="default_fader">
 <option value="" {{'selected' if row.default_fader is None else ''}}>{{_('(none)')}}</option>
 % for idx, fader_name in _faders_for_default:
 <option value="{{idx}}" {{'selected' if idx == row.default_fader else ''}}>{{fader_name}}</option>
 % end
 </select>
 </div>
 </div>
 <div class="row">
 <div class="field">
 <label for="destination-id-{{row.id}}">{{_('Destination')}}</label>
 <select id="destination-id-{{row.id}}" name="destination_id"
 hx-get="/api/validate/osc_binding/destination_id"
 hx-trigger="blur changed delay:200ms"
 hx-target="#destination-id-{{row.id}}-error"
 hx-swap="innerHTML"
 hx-include="closest form"
 aria-describedby="destination-id-{{row.id}}-error"
 aria-invalid="false">
 <option value="" {{'selected' if not row.destination_id else ''}}>{{_('(none – row will not send)')}}</option>
 % # A row pointing at a deleted destination keeps its dangling id: show it as
 % # a selected (disabled) option so the dropdown reflects the stored state
 % # instead of silently falling back to "(none)".
 % if row.destination_id and row.destination_id not in _dest_by_id:
 <option value="{{row.destination_id}}" selected disabled>{{_('(missing destination)')}}</option>
 % end
 % for d in _destinations:
 <option value="{{d.id}}" {{'selected' if d.id == row.destination_id else ''}}>{{d.name or _('(unnamed)')}} – {{d.protocol}}://{{d.host}}:{{d.port}}</option>
 % end
 </select>
 <span id="destination-id-{{row.id}}-error" class="field-error"></span> (i18n: refine _() wrapping - remove from internals, add missing user-facing strings)
 </div>
 </div>
 </div>

 <!-- Trigger -->
 % # ``data-osc-trigger-type-select`` is the hook the
 % # base.tpl JS uses to mirror the chosen type into
 % # the collapsed-row badge + ``data-trigger-kind`` on
 % # the parent ``<details>`` immediately on change –
 % # so the operator sees the new label before the
 % # save round-trip lands.
 <div class="row-tab-panel" id="row-tab-triggers-{{row.id}}" role="tabpanel">
 <div class="row">
 <div class="field">
 <label>{{_('Trigger type')}}</label>
 <select name="trigger.type"
 data-osc-trigger-type-select
 hx-get="/section/osc_binding/{{row.id}}/trigger_form"
 hx-trigger="change"
 hx-target="#trigger-fields-{{row.id}}"
 hx-swap="innerHTML"
 hx-include="this">
 % for k in valid_kinds:
 % # Every kind selectable. MIDI/fader kinds
 % # wired end-to-end (placeholders + dispatch + forms).
 <option value="{{k}}" {{'selected' if k == trigger_kind else ''}}>{{pretty_label(k)}}</option>
 % end
 </select>
 </div>
 </div>
 <div id="trigger-fields-{{row.id}}" class="row">
 % include('partials/osc_binding_trigger_form.tpl', row=row, kind=trigger_kind, valid_rates=valid_rates, valid_edges=valid_edges, valid_modifiers=valid_modifiers, valid_keys=valid_keys, valid_buttons=valid_buttons, valid_midi_types=valid_midi_types, virtual_fader_names=virtual_fader_names, midi_patches=midi_patches)
 </div>
 </div>

 <!-- Settings -->
 % # Combined osc_message field (address + args). Visible
 % # editor: contenteditable div with [name] placeholders
 % # as inline pill spans (JS renders from base.tpl).
 % # Hidden input mirrors plain-text for submission.
 % # Args with whitespace wrapped in quotes for round-trip
 % # via join_osc_message (preserves "My Cue" as one arg).
 % from openfollow.osc.parser import join_osc_message
 % message_value = join_osc_message(row.address, row.args)
 <div class="row-tab-panel" id="row-tab-settings-{{row.id}}" role="tabpanel">
 <div class="row">
 <div class="field grow">
 % # <label for="..."> doesn't associate with
 % # contenteditable <div>. Wire accessible name
 % # via aria-labelledby on editor. Bare <label>
 % # (no for=) still works for screen reader.
 <label id="osc-message-{{row.id}}-label">{{_('OSC message')}}</label>
 % # data-osc-unresolved-placeholders: JSON-encoded
 % # list of unmet dependencies (server-side).
 % # Pill JS applies data-unresolved="true" for CSS.
 % # data-osc-registered-marker-ids for re-eval as
 % # operator edits markers. data-osc-placeholder-names:
 % # full set so client mirror doesn't drift vs server.
 <div id="osc-message-{{row.id}}"
 class="osc-message-editor"
 contenteditable="true"
 spellcheck="false"
 data-osc-message-editor="{{row.id}}"
 data-osc-message-placeholder="/eos/set/patch/[markerid]/augment3d/position [x] [z] [y] 0 0 0"
 data-osc-unresolved-placeholders="{{json.dumps(list(row_unresolved))}}"
 data-osc-placeholder-names="{{json.dumps(list(placeholders))}}"
 data-osc-registered-marker-ids="{{json.dumps(list(_registered_marker_ids))}}"
 data-osc-row-markers="{{', '.join(row.markers)}}"
 data-osc-grid-max-height="{{config.grid.max_height}}"
 hx-get="/api/validate/osc_binding/osc_message"
 hx-trigger="osc-validate"
 hx-target="#osc-message-{{row.id}}-error"
 hx-swap="innerHTML"
 hx-vals='js:{"osc_message": (document.getElementById("osc-message-{{row.id}}-hidden") || {}).value, "markers": (document.getElementById("markers-{{row.id}}") || {}).value}'
 aria-labelledby="osc-message-{{row.id}}-label"
 aria-describedby="osc-message-{{row.id}}-error"
 aria-multiline="false"
 role="textbox">{{message_value}}</div>
 <input type="hidden"
 name="osc_message"
 id="osc-message-{{row.id}}-hidden"
 value="{{message_value}}">
 <span id="osc-message-{{row.id}}-error" class="field-error"></span>
 <span class="field-note">{{_('First token is the OSC address; the rest are arguments. Click a placeholder below to insert it. Add')}} <code>:N</code> {{_('to target a specific marker or fader – e.g.')}} <code>[x:2]</code>, <code>[fader:3]</code> {{_('– and chain')}} <code>.transform</code> {{_('filters:')}} <code>.inv</code>, <code>.frac</code>, <code>.pct</code>, <code>.int:min-max</code>, <code>.scale:min-max</code> {{_('(e.g.')}} <code>[fader.pct]</code>, <code>[markerfader:3.int:0-100]</code>, <code>[fader.scale:-60-12]</code>{{_('). See')}} <strong>{{_('Help (?)')}}</strong> {{_('for the full grammar and more examples.')}} <strong>{{_('Click any pill to edit it.')}}</strong></span>
 </div>
 </div>
 <div class="row placeholder-buttons">
 % for ph in placeholders:
 <button type="button"
 class="placeholder-chip"
 data-osc-placeholder="[{{ph}}]"
 data-osc-target="#osc-message-{{row.id}}">[{{ph}}]</button>
 % end
 </div>
 % # curated transform examples – one-click
 % # canonical forms for the common recipes (fractional
 % # positions; the grandMA percent ask; the controller
 % # reference). Operators can also type any ``.transform``
 % # chain or a ``:cN`` reference by hand.
 <div class="row placeholder-buttons">
 % for ex in ('[x.frac]', '[y.frac]', '[fader.pct]', '[markerfader.pct]', '[markerid:c1]'):
 <button type="button"
 class="placeholder-chip"
 data-osc-placeholder="{{ex}}"
 data-osc-target="#osc-message-{{row.id}}">{{ex}}</button>
 % end
 </div>
 </div>

 <!-- Diagnostics -->
 % #
 % # Operator-feedback follow-up: redesign of the
 % # cramped row-of-buttons-+-raw-JSON layout. Three
 % # stacked panels (Live status / Preview / Test send),
 % # each with a labelled header + dedicated body area
 % # for formatted output. Raw JSON used to dominate
 % # the tab; the structured renderers in ``base.tpl``
 % # turn the same backend data into address / args
 % # tables + healthy / pps / last-error / recent
 % # events rows.
 % #
 % # ``data-osc-diag-panel="{{row.id}}"`` is the hook
 % # the tab-activation listener uses to know when to
 % # auto-fetch the live status. Buttons send via
 % # vanilla ``fetch`` rather than HTMX so the
 % # structured renderer is the single writer to each
 % # body – no ``hx-swap="none"`` workaround.
 <div class="row-tab-panel diag-panel"
 id="row-tab-diag-{{row.id}}"
 role="tabpanel"
 data-osc-diag-panel="{{row.id}}">
 <div class="diag-grid">
 <!-- Live status panel -->
 <div class="stat-panel diag-card" data-osc-diag-card="status">
 <div class="stat-panel-head">
 <h4 class="stat-panel-title">{{_('Live status')}}</h4>
 <button type="button" class="diag-action"
 data-osc-diag-refresh="status"
 data-row-id="{{row.id}}"
 title="{{_('Refresh')}}">↻</button>
 </div>
 <div class="diag-body" data-osc-diag-status-body="{{row.id}}">
 <p class="modal-empty">{{_('Loading…')}}</p>
 </div>
 </div>

 <!-- Preview panel -->
 <div class="stat-panel diag-card" data-osc-diag-card="preview">
 <div class="stat-panel-head">
 <h4 class="stat-panel-title">{{_('Preview')}}</h4>
 <button type="button" class="diag-action"
 data-osc-diag-action="preview"
 data-row-id="{{row.id}}">{{_('Refresh')}}</button>
 </div>
 <p class="stat-help">{{_("Render the message that would be sent right now using the current marker position. Doesn\'t fire on the wire.")}}</p>
 <div class="diag-body" data-osc-diag-preview-body="{{row.id}}">
 <p class="modal-empty">{{_('Click')}} <em>{{_('Refresh')}}</em> {{_('to render the message.')}}</p>
 </div>
 </div>

 <!-- Test send panel -->
 <div class="stat-panel diag-card" data-osc-diag-card="test">
 <div class="stat-panel-head">
 <h4 class="stat-panel-title">{{_('Test send')}}</h4>
 <button type="button" class="diag-action primary"
 data-osc-diag-action="test"
 data-row-id="{{row.id}}">{{_('Send test packet')}}</button>
 </div>
 <p class="stat-help">{{_("Force one packet to the configured destination. Bypasses the row\'s Enabled flag so a disabled row can still be probed before flipping it on.")}}</p>
 <div class="diag-body" data-osc-diag-test-body="{{row.id}}">
 <p class="modal-empty">{{_('No test packet sent yet.')}}</p>
 </div>
 </div>
 </div>
 </div>

 % # Re-order by dragging the row's ``⋮⋮`` handle in the
 % # collapsed-state summary (see ``.osc-binding-drag-handle``
 % # below). The bulk-reorder POST goes to
 % # ``/section/osc_bindings/reorder`` from the JS in
 % # base.tpl. The legacy ``/move`` route stays available
 % # for API/keyboard-shortcut callers.
 <div class="actions osc-binding-actions">
 <button type="submit" class="save-btn">{{_('Save')}}</button>
 <!-- Per-row Discard button. Restores row from disk.
 data-discard-btn + data-template-deps gate with
 inverted polarity (disabled when clean).
 hx-target + hx-select scope to this row only;
 other open rows stay untouched. ?focus= keeps
 row open. -->
 <button type="button" class="secondary"
 data-discard-btn
 data-template-deps='form.osc-binding-form[data-row-id="{{row.id}}"]'
 data-row-id="{{row.id}}"
 disabled
 title="{{_('No unsaved changes.')}}"
 hx-get="/section/osc_bindings?focus={{row.id}}"
 hx-target='details.osc-binding-row[data-row-id="{{row.id}}"]'
 hx-select='details.osc-binding-row[data-row-id="{{row.id}}"]'
 hx-swap="outerHTML"
 hx-confirm="{{_('Discard unsaved changes to this row?')}}">{{_('Discard')}}</button>
 <!-- Per-row "Save as template…" button. Reads row's
 name + live OSC message (hidden mirror) and POSTs
 to /api/templates/osc_output/save. File lands as
 .openfollowtemplate under templates/user/ and
 appears in OSC Outputs dropdown next render.
 data-row-id scopes JS to this row. -->
 <button type="button" class="secondary"
 data-osc-save-template-btn
 data-template-save
 data-template-deps='form.osc-binding-form[data-row-id="{{row.id}}"]'
 data-row-id="{{row.id}}">{{_('Save as template…')}}</button>
 <button type="button" class="secondary"
 hx-post="/section/osc_binding/{{row.id}}/duplicate"
 hx-target="#osc-bindings-section"
 hx-swap="outerHTML">{{_('Duplicate')}}</button>
 <button type="button" class="danger"
 hx-post="/section/osc_binding/{{row.id}}/delete"
 hx-target="#osc-bindings-section"
 hx-swap="outerHTML"
 hx-confirm="{{_('Delete this OSC output?')}}">{{_('Delete')}}</button>
 </div>
 </form>
 </details>
 % # Secondary markers from this row's ``markers`` field, shown as
 % # read-only chips nested under the primary row. They share the row's
 % # destination / message / trigger; edit the set via "Default markers".
 % # Each carries the same status dot as a transmitter row: red when the id
 % # isn't controlled by this station (its send is dropped at runtime).
 % if _marker_nested:
 <div class="osc-binding-nested" aria-label="Additional markers">
 % for _entry in _marker_nested:
 <span class="osc-binding-nested-row{{'' if _entry['controlled'] else ' is-invalid'}}"{{!'' if _entry['controlled'] else ' title=\"Not controlled by this station (ignored)\"'}}>
 <span class="osc-binding-enabled-dot {{'on' if _entry['controlled'] else 'invalid'}}" aria-label="{{'Controlled' if _entry['controlled'] else 'Not controlled'}}"></span>
 <span>{{_entry['label']}}</span>
 </span>
 % end
 </div>
 % end
 % end
 </div>

 % # Template choice at toolbar (not per-row). New OSC output creates
 % # row pre-populated with template's name, address, args. Template id
 % # as form field for POST handler add_osc_binding.
 <form class="actions osc-bindings-toolbar"
 hx-post="/section/osc_bindings/add"
 hx-target="#osc-bindings-section"
 hx-swap="outerHTML"
 hx-trigger="submit">
 <label class="inline-label" for="osc-bindings-new-template">{{_('Template')}}</label>
 % # The dropdown is grouped via ``<optgroup>`` so the operator
 % # can tell at a glance which entries ship with the repo
 % # (system) vs. ones they (or the install) saved (user).
 % # ``optgroup label`` is unselectable by design – perfect for
 % # the divider semantics requested by ops.
 <select id="osc-bindings-new-template" name="template_id">
 <option value="">{{_('empty')}}</option>
 % if builtin_templates:
 <optgroup label="{{_('Default Templates')}}">
 % # Bundled system templates sourced from disk (same
 % # loader as user templates); carry select_value.
 % for tpl in builtin_templates:
 <option value="{{tpl.select_value}}">{{tpl.name}}</option>
 % end
 </optgroup>
 % end
 % if custom_templates:
 <optgroup label="{{_('Custom Templates')}}">
 % # Use file-based select_value (file:<filename>), not
 % # envelope id (ids not unique, would leave duplicates
 % # unselectable).
 % for tpl in custom_templates:
 <option value="{{tpl.select_value}}">{{tpl.name}}</option>
 % end
 </optgroup>
 % end
 </select>
 <button type="submit" class="save-btn">{{_('+ New OSC output')}}</button>
 </form>
</div>
