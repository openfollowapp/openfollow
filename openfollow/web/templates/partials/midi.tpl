% # MIDI page partial. Rendered inside the
% # Input tab. Two sub-sections,
% # each its own form so saves are independent: Devices, Virtual
% # Faders.
% #
% # Provider data threaded from the route handler:
% # - ``discovered_devices``: list[dict] of currently-connected MIDI
% # ports (identifier / port_name / product / serial).
% # - ``valid_fader_midi_types``: the configuration's tuple constant
% # used to populate the fader source-type dropdown.
<div id="midi-section" class="section {{'saved' if defined('saved') and saved else ''}}" data-fold-key="midi" data-help="midi">
 <div class="section-head">
 <h2>{{_('MIDI')}}</h2>
 <span class="section-note">{{_("USB MIDI device aliases and virtual fader sources. The OSC Transmitters trigger forms reference what's configured here.")}}</span>
 </div>

 % # ----------------------------------------------------------------
 % # MIDI Patches: Each patch is a
 % # stable, integer-ID'd slot the operator assigns a connected
 % # device to. The patch *id* is the foreign key the rest of the
 % # system (fader sources, OSC binding triggers) references – not
 % # the port name or the optional alias. "Add new
 % # MIDI Patch" appends a row; each row saves / deletes
 % # independently. The substrate's ``apply_config`` opens / closes
 % # the listener port for each patch on the next hot-reload tick.
 % #
 % # ``discovered_devices`` is the list of currently-connected ports
 % # (identifier / port_name / product / serial). A patch whose
 % # bound device is unplugged keeps its binding (shown as "(not
 % # connected)") and re-submitting the row preserves it.
 % # ----------------------------------------------------------------
 <div class="group">
 <h3 class="group-title">{{_('MIDI Patches')}}</h3>
 % if config.midi.patches:
 <div class="row">
 <table class="grid-table">
 <thead>
 <tr>
 <th style="width:3rem">{{_('ID')}}</th>
 <th>{{_('Alias')}}</th>
 <th>{{_('Device')}}</th>
 <th></th>
 </tr>
 </thead>
 <tbody>
 % # ``connected_ids`` lets us tell whether a patch's
 % # bound device is currently plugged in, so a
 % # disconnected binding can be shown (and preserved)
 % # rather than silently dropped on the next save.
 % connected_ids = set(d['identifier'] for d in discovered_devices)
 % for patch in config.midi.patches:
 <tr>
 <td><span class="patch-id">{{patch.id}}</span></td>
 <td>
 <input type="text" form="patch-{{patch.id}}-form" name="alias" value="{{patch.alias}}" placeholder="{{_('(optional)')}}" maxlength="64">
 </td>
 <td>
 <select form="patch-{{patch.id}}-form" name="device">
 <option value="" {{'selected' if not patch.identifier else ''}}>{{_('– none –')}}</option>
 % for device in discovered_devices:
 <option value="{{device['identifier']}}" {{'selected' if device['identifier'] == patch.identifier else ''}}>{{device['port_name']}}{{' – ' + device['serial'] if device['serial'] else ''}}</option>
 % end
 % # Disconnected binding: the device this patch
 % # points at isn't currently connected. Offer a
 % # selected option carrying the patch's own
 % # identifier so a save round-trips the binding.
 % if patch.identifier and patch.identifier not in connected_ids:
 <option value="{{patch.identifier}}" selected>{{patch.port_name or patch.identifier}} {{_('(not connected)')}}</option>
 % end
 </select>
 </td>
 <td class="actions-cell">
 <button type="submit" form="patch-{{patch.id}}-form" class="save-btn small">{{_('Save')}}</button>
 <button type="button" class="danger small"
 hx-post="/section/midi/patches/{{patch.id}}/delete"
 hx-target="#midi-section"
 hx-swap="outerHTML"
 hx-confirm="{{_('Delete patch')}} {{patch.id}}{{' (' + patch.alias + ')' if patch.alias else ''}}?">{{_('Delete')}}</button>
 </td>
 </tr>
 % end
 </tbody>
 </table>
 % # Per-row form elements live outside the table (HTML5
 % # ``form="id"`` association keeps markup valid (browser
 % # reparents <form> straddling <td> and silently breaks Save).
 % for patch in config.midi.patches:
 <form id="patch-{{patch.id}}-form"
 hx-post="/section/midi/patches/{{patch.id}}"
 hx-target="#midi-section"
 hx-swap="outerHTML"></form>
 % end
 </div>
 % else:
 <div class="row">
 <p class="field-note-msg">{{_('No MIDI patches yet. Add one, give it an alias, and assign a connected device.')}}</p>
 </div>
 % end
 <div class="actions">
 <button type="button" class="save-btn"
 hx-post="/section/midi/patches/add"
 hx-target="#midi-section"
 hx-swap="outerHTML">{{_('Add new MIDI Patch')}}</button>
 </div>
 </div>

 % # ----------------------------------------------------------------
 % # Virtual Faders – visual mixer-style strip row + detail panel.
 % # Each strip is a vertical fader graphic the operator can click
 % # to load that fader's editable config into the detail panel
 % # below. Live values poll every 100 ms via HTMX out-of-band
 % # swaps: the strip's fill bar + handle + numeric value are id-
 % # keyed so the poll response targets each one without
 % # re-rendering the click handler / selection state.
 % # ----------------------------------------------------------------
 <div class="group">
 <h3 class="group-title">{{_('Virtual Faders')}}</h3>
 % # ``selected_fader`` ships from the route handler – defaults
 % # to 1 on initial render, but a save handler can pass the
 % # just-saved fader's index so re-rendering after a per-fader
 % # save doesn't snap selection back to fader 1. The
 % # ``defined()`` guard keeps the legacy ``index.tpl`` include
 % # working without having to thread the kwarg through every
 % # caller – only the per-fader save path overrides the default.
 % selected_fader = selected_fader if defined('selected_fader') else 1
 % # ARIA: the strips were originally marked up as ``role="tab"``
 % # / ``role="tablist"`` but the related ``role="tabpanel"`` /
 % # Treated as plain toggle buttons with aria-pressed (more accurate
 % # than tabs): buttons swap one shared content panel.
 <div class="midi-fader-strips" role="group" aria-label="{{_('Virtual faders')}}">
 % for idx, fader in enumerate(config.virtual_faders.faders, start=1):
 % # Selection indicator: ``selected_fader`` controls
 % # which strip starts selected. The browser-side
 % # click handler toggles ``data-selected``; HTMX
 % # swaps the detail panel via the ``hx-get`` below.
 % fill_pct = '%g' % (max(0.0, min(1.0, fader.default_value)) * 100)
 % display_name = fader.name or 'Fader %d' % idx
 % is_selected = (idx == selected_fader)
 % # Tint the strip with the fader's assigned colour. Driven
 % # via a CSS var (not inline background) so the higher-
 % # specificity hover / [data-selected] rules still win. The
 % # black default reads as "no colour chosen", so emit
 % # ``transparent`` for it – an unconfigured strip keeps its
 % # prior untinted look instead of a 25%-black wash.
 % _fader_color = getattr(fader, 'color', None) or '#000000'
 % fader_tint = (_fader_color + '40') if _fader_color != '#000000' else 'transparent'
 <button type="button" class="midi-fader-strip"
 style="--fader-tint:{{fader_tint}}"
 aria-pressed="{{'true' if is_selected else 'false'}}"
 aria-label="{{display_name}}"
 data-fader-idx="{{idx}}"
 data-selected="{{'true' if is_selected else 'false'}}"
 hx-get="/section/midi/faders/{{idx}}/detail"
 hx-target="#midi-fader-detail-panel"
 hx-swap="innerHTML"
 onclick="document.querySelectorAll('.midi-fader-strip').forEach(function(s){s.dataset.selected = (s === this) ? 'true' : 'false'; s.setAttribute('aria-pressed', s === this ? 'true' : 'false');}.bind(this));">
 <span class="midi-fader-strip-num">{{idx}}</span>
 <div class="midi-fader-strip-track">
 <div id="midi-fader-strip-fill-{{idx}}"
 class="midi-fader-strip-fill"
 style="height:{{fill_pct}}%"></div>
 <div id="midi-fader-strip-handle-{{idx}}"
 class="midi-fader-strip-handle"
 style="bottom:{{fill_pct}}%"></div>
 </div>
 <span class="midi-fader-strip-name">{{display_name}}</span>
 <span id="midi-fader-strip-value-{{idx}}" class="midi-fader-strip-value">{{'%.2f' % fader.default_value}}</span>
 </button>
 % end
 </div>
 </div>

 % # Detail panel – server-rendered, swapped on strip click. The
 % # initial render uses ``selected_fader`` so a section re-render
 % # After per-fader save, show just-saved fader's detail
 % # (not always snap back to fader 1).
 <div id="midi-fader-detail-panel" class="group">
 % include('partials/midi_fader_detail.tpl', \
 % config=config, fader_index=selected_fader, \
 % fader=config.virtual_faders.faders[selected_fader - 1], \
 % midi_patches=midi_patches, \
 % valid_fader_midi_types=valid_fader_midi_types)
 </div>

 % # Polling driver for the live fader values. One request every
 % # 100 ms; the response is a flat list of OOB ``<div>`` /
 % # ``<span>`` snippets keyed by the per-strip ids above, so the
 % # fill bar + handle + value all update from one round-trip
 % # without disturbing the click handler or the selection
 % # state. ``hx-swap="none"`` on the driver itself because the
 % # OOB swaps in the response are how the update actually lands.
 <div hx-get="/section/midi/faders/values"
 hx-trigger="every 100ms"
 hx-swap="none"
 style="display:none"></div>

 % # ----------------------------------------------------------------
 % # Marker Faders – READ-ONLY live viz. One strip per
 % # controlled marker, driven by whichever gamepad currently
 % # controls that marker. These aren't editable here: they're
 % # auto-provisioned from ``controlled_marker_ids`` and moved by
 % # the stick, so this is purely a readout that mirrors the
 % # Virtual Faders strip graphic. Live values poll every 100 ms
 % # via OOB swaps keyed by marker id. ``defined()`` guard keeps
 % # the legacy ``index.tpl`` include working without threading the
 % # kwarg through it – that path renders an empty strip set and the
 % # poll fills it in.
 % marker_fader_values = marker_fader_values if defined('marker_fader_values') else []
 <div class="group">
 <h3 class="group-title">{{_('Marker Faders')}}</h3>
 <span class="section-note">{{_("Read-only. One fader per controlled marker, driven by the gamepad that controls it. Send a marker's value with the OSC")}} <code>[markerfader]</code> {{_('placeholder.')}}</span>
 % if marker_fader_values:
 <div class="midi-fader-strips" role="group" aria-label="{{_('Marker faders')}}">
 % for entry in marker_fader_values:
 % mid = entry['marker_id']
 % fill_pct = '%g' % (max(0.0, min(1.0, entry['value'])) * 100)
 % display_name = entry['name'] or 'M%d' % mid
 % # : tint the strip with the marker's catalog
 % # colour so each read-only strip ties back to its marker on
 % # the overlay. ``+ '40'`` is a ~25% alpha (colour is a
 % # #rrggbb hex) so the strip's light text stays legible.
 % strip_color = (entry.get('color') or '#ff8000') + '40'
 <div class="midi-fader-strip midi-fader-strip--readonly"
 style="background:{{strip_color}}"
 aria-label="{{display_name}}"
 data-marker-id="{{mid}}">
 <span class="midi-fader-strip-num">{{mid}}</span>
 <div class="midi-fader-strip-track">
 <div id="marker-fader-strip-fill-{{mid}}"
 class="midi-fader-strip-fill"
 style="height:{{fill_pct}}%"></div>
 <div id="marker-fader-strip-handle-{{mid}}"
 class="midi-fader-strip-handle"
 style="bottom:{{fill_pct}}%"></div>
 </div>
 <span class="midi-fader-strip-name">{{display_name}}</span>
 <span id="marker-fader-strip-value-{{mid}}" class="midi-fader-strip-value">{{'%.2f' % entry['value']}}</span>
 </div>
 % end
 </div>
 % else:
 <p class="field-note-msg">{{_('No controlled markers yet. Add markers under Controlled markers and assign a gamepad to see their faders here.')}}</p>
 % end

 % # Polling driver for the live marker-fader values. One request
 % # every 100 ms; the response is OOB snippets keyed by marker id
 % # so each strip's fill + handle + value update from one round-
 % # trip. ``hx-swap="none"`` because the OOB swaps do the work.
 <div hx-get="/section/midi/marker-faders/values"
 hx-trigger="every 100ms"
 hx-swap="none"
 style="display:none"></div>
 </div>

</div>
