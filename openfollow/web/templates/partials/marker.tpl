% from openfollow.units import UnitSystem, format_length, metric_echo, unit_suffix_length
% _us = UnitSystem(config.ui.unit_system)
% _imp = _us is UnitSystem.IMPERIAL
% _len = unit_suffix_length(_us)
%# Two independent top-level sections – same sibling-forms pattern as
%# general.tpl and the surrounding movement.tpl / trigger_zones.tpl on
%# the Markers & Zones tab. Each section is its own box; folding is
%# per-section.
%#
%#   1. Marker Control & Visibility – standalone <section> (NOT a form).
%#      Catalog + per-station selection write out-of-band via JS API
%#      calls (/api/markers/catalog/<id>, /api/markers/selection), so
%#      there's nothing to submit and no Save button. The wrapping
%#      <section> intentionally has no ancestor form, which is also
%#      why the polling div no longer inherits ``hx-target`` – but
%#      ``hx-target="this"`` stays as defence in depth in case the
%#      structure shifts again.
%#
%#   2. Marker Visuals – <form id="marker-section"> with the Body /
%#      Crosshair / Z Display / Drop Line / Ground Circle / Color
%#      Palette groups. Saves through /section/marker like every
%#      other dataclass-driven section.

%# ------------------------------------------------------------------
%# Section 1: Marker Control & Visibility
%# ------------------------------------------------------------------
<section id="marker-control-visibility-section" class="section" data-fold-key="marker-control-visibility" data-help="marker-control-visibility" data-fold-default="expanded">
    <div class="section-head">
        <h2>{{_('Marker Control & Visibility')}}</h2>
        <span class="section-note">{{_('Shared catalog + this station\'s selection')}}</span>
    </div>
    <div id="marker-catalog-root"
         hx-get="/api/markers/catalog"
         hx-trigger="load, every 1500ms"
         hx-target="this"
         hx-swap="none">
        {{_('Loading…')}}
    </div>
</section>

%# ------------------------------------------------------------------
%# Section 2: Marker Visuals
%# ------------------------------------------------------------------
<form id="marker-section" class="section {{'saved' if defined('saved') and saved else ''}}" data-fold-key="marker-visuals" data-help="marker-visuals" data-fold-default="expanded"
      hx-post="/section/marker" hx-target="#marker-section" hx-swap="outerHTML" hx-trigger="submit">
    <div class="section-head">
        <h2>{{_('Marker Visuals')}}</h2>
        <span class="section-note">{{_('On-screen appearance of each marker')}}</span>
    </div>

    <div class="group">
        <h3 class="group-title">{{_('Body')}}</h3>
        <div class="fields-grid">
            <div class="field checkbox-field">
                <label>{{_('Show Ball')}}</label>
                <div class="checkbox-wrap"><input type="checkbox" name="ball_visible" {{'checked' if config.marker.ball_visible else ''}}></div>
            </div>
            <div class="field">
                <label>{{_('Ball Size')}} ({{_len}})</label>
                <input id="marker-ball-size" type="{{'text' if _imp else 'number'}}" name="ball_size" value="{{format_length(config.marker.ball_size, _us) if _imp else config.marker.ball_size}}" min="0" step="any"
                       hx-get="/api/validate/marker/ball_size" hx-trigger="blur changed delay:200ms"
                       hx-target="#marker-ball-size-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="marker-ball-size-error" aria-invalid="false">
                <span id="marker-ball-size-error" class="field-error"></span>
                % if _imp:
                <small class="metric-echo">{{_('Stored:')}} {{metric_echo(config.marker.ball_size)}}</small>
                % end
            </div>
            <div class="field">
                <label>{{_('Opacity')}} (0–1)</label>
                <input id="marker-transparency" type="number" name="transparency" value="{{config.marker.transparency}}" min="0" max="1" step="any"
                       hx-get="/api/validate/marker/transparency" hx-trigger="blur changed delay:200ms"
                       hx-target="#marker-transparency-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="marker-transparency-error" aria-invalid="false">
                <span id="marker-transparency-error" class="field-error"></span>
            </div>
        </div>
    </div>

    <div class="group">
        <h3 class="group-title">{{_('Crosshair')}}</h3>
        <div class="fields-grid">
            <div class="field checkbox-field">
                <label>{{_('Show Crosshair')}}</label>
                <div class="checkbox-wrap"><input type="checkbox" name="crosshair_visible" {{'checked' if config.marker.crosshair_visible else ''}}></div>
            </div>
            <div class="field">
                <label>{{_('Crosshair Size')}} ({{_len}})</label>
                <input id="marker-crosshair-size" type="{{'text' if _imp else 'number'}}" name="crosshair_size" value="{{format_length(config.marker.crosshair_size, _us) if _imp else config.marker.crosshair_size}}" min="0" step="any"
                       hx-get="/api/validate/marker/crosshair_size" hx-trigger="blur changed delay:200ms"
                       hx-target="#marker-crosshair-size-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="marker-crosshair-size-error" aria-invalid="false">
                <span id="marker-crosshair-size-error" class="field-error"></span>
                % if _imp:
                <small class="metric-echo">{{_('Stored:')}} {{metric_echo(config.marker.crosshair_size)}}</small>
                % end
            </div>
            <div class="field">
                <label>{{_('Crosshair Thickness')}} (px)</label>
                <input id="marker-crosshair-thickness" type="number" name="crosshair_thickness" value="{{config.marker.crosshair_thickness}}" min="1" max="10"
                       hx-get="/api/validate/marker/crosshair_thickness" hx-trigger="blur changed delay:200ms"
                       hx-target="#marker-crosshair-thickness-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="marker-crosshair-thickness-error" aria-invalid="false">
                <span id="marker-crosshair-thickness-error" class="field-error"></span>
            </div>
            <div class="field">
                <label>{{_('Crosshair Color')}}</label>
                %# Native picker replaced by circle-swatch greys-variant.
                %# Hidden input carries form value; color-picker.js auto-attaches
                %# via data-color-picker. Inline validator dropped (picker's hex
                %# input enforces ^#?[0-9A-Fa-f]{6}$ client-side; form handler
                %# validates server-side).
                <button id="marker-crosshair-color" type="button" class="color-swatch-trigger"
                        data-color-picker="greys" data-value="{{config.marker.crosshair_color}}"
                        aria-label="{{_('Crosshair colour')}}"></button>
                <input type="hidden" name="crosshair_color" value="{{config.marker.crosshair_color}}">
            </div>
        </div>
    </div>

    <div class="group">
        <h3 class="group-title">{{_('Z Display')}}</h3>
        <div class="fields-grid">
            <div class="field checkbox-field">
                <label>{{_('Z from Stage Level')}}</label>
                <div class="checkbox-wrap"><input type="checkbox" name="z_display_from_stage" {{'checked' if config.marker.z_display_from_stage else ''}}></div>
            </div>
        </div>
    </div>

    <div class="group">
        <h3 class="group-title">{{_('Drop Line')}}</h3>
        <div class="fields-grid">
            <div class="field checkbox-field">
                <label>{{_('Drop Line')}}</label>
                <div class="checkbox-wrap"><input type="checkbox" name="drop_line" {{'checked' if config.marker.drop_line else ''}}></div>
            </div>
            <div class="field">
                <label>{{_('Drop Line Thickness')}} (px)</label>
                <input id="marker-drop-line-thickness" type="number" name="drop_line_thickness" value="{{config.marker.drop_line_thickness}}" min="1" max="20"
                       hx-get="/api/validate/marker/drop_line_thickness" hx-trigger="blur changed delay:200ms"
                       hx-target="#marker-drop-line-thickness-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="marker-drop-line-thickness-error" aria-invalid="false">
                <span id="marker-drop-line-thickness-error" class="field-error"></span>
            </div>
        </div>
    </div>

    <div class="group">
        <h3 class="group-title">{{_('Ground Circle')}}</h3>
        <div class="fields-grid">
            <div class="field checkbox-field">
                <label>{{_('Ground Circle')}}</label>
                <div class="checkbox-wrap"><input type="checkbox" name="ground_circle" {{'checked' if config.marker.ground_circle else ''}}></div>
            </div>
            <div class="field">
                <label>{{_('Circle Size')}} ({{_len}})</label>
                <input id="marker-ground-circle-size" type="{{'text' if _imp else 'number'}}" name="ground_circle_size" value="{{format_length(config.marker.ground_circle_size, _us) if _imp else config.marker.ground_circle_size}}" min="0" step="any"
                       hx-get="/api/validate/marker/ground_circle_size" hx-trigger="blur changed delay:200ms"
                       hx-target="#marker-ground-circle-size-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="marker-ground-circle-size-error" aria-invalid="false">
                <span id="marker-ground-circle-size-error" class="field-error"></span>
                % if _imp:
                <small class="metric-echo">{{_('Stored:')}} {{metric_echo(config.marker.ground_circle_size)}}</small>
                % end
            </div>
            <div class="field checkbox-field">
                <label>{{_('Filled')}}</label>
                <div class="checkbox-wrap"><input type="checkbox" name="ground_circle_filled" {{'checked' if config.marker.ground_circle_filled else ''}}></div>
            </div>
        </div>
    </div>

    <div class="actions">
        <button type="submit" class="save-btn">{{_('Save')}}</button>
    </div>
</form>

<script>
// Catalog rendering – runs once per page lifetime. The marker partial
// can be re-swapped via the form's hx-post response; we install the
// htmx:afterRequest listener on ``document.body`` ONCE (guarded by
// ``window.__markerCatalogInit``) so each form re-render doesn't add
// a duplicate listener that would double-render the catalog every
// 1.5 s. The listener re-resolves ``#marker-catalog-root`` dynamically
// on each fire so a fresh root from the latest swap still receives
// updates.
//
// Rendering is diff-based. Skeleton (group wrappers, headers, add-row,
// selection-flash) built ONCE via ensureSkeleton; subsequent polls walk
// entries by id and create/update/remove only changed rows. Per-row
// event listeners attached at row-creation, outlive next 1.5s tick.
// Delete / selection checkboxes no longer churn 40× per minute.
// The earlier ``isEditingTextInput`` blanket guard is replaced by a
// per-cell focus check inside ``setIfChanged`` so a peer's rename
// can land on row N while the operator is typing in row M.
(function() {
    if (window.__markerCatalogInit) return;
    window.__markerCatalogInit = true;

    // Default-pick for the add-row colour: walk the canonical palette
    // (window.OPENFOLLOW_PALETTE.auto_pick_order, seeded by base.tpl
    // from openfollow.palette) and return first unused hex. Shared
    // constant (was duplicated ``MARKER_COLOR_PALETTE``) with Python
    // seeders and zone editor via ``window.OpenFollow.nextUnusedColor``.
    function pickMarkerColor(entries) {
        return window.OpenFollow.nextUnusedColor(
            entries.map(function(e) { return e.color; })
        );
    }

    // ---- Colour swatch helper
    // Swatch trigger is <button> (not <input>) with value in dataset.value
    // and colour in style.background. Skip refresh when picker is open or
    // trigger is focused. Compare case-insensitively (palette uppercase vs
    // catalog lowercase).
    function setSwatchIfChanged(el, value) {
        if (!el) return;
        if (document.activeElement === el) return;
        if (document.querySelector('.color-picker-popover')) return;
        const v = value || '#000000';
        if ((el.dataset.value || '').toLowerCase() === v.toLowerCase()) return;
        el.dataset.value = v;
        el.style.background = v;
    }

    function escapeHTML(s) {
        return String(s).replace(/[&<>"']/g, function(c) {
            return ({
                '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
            })[c];
        });
    }

    function controlledByLabel(entry, thisStation, peers) {
        const owners = [];
        if (thisStation.controlled_ids.indexOf(entry.id) !== -1) {
            owners.push({name: '{{_('this station (')}}' + escapeHTML(thisStation.station_name) + ')', self: true});
        }
        for (const p of peers) {
            if (p.controlled_ids.indexOf(entry.id) !== -1) {
                owners.push({name: escapeHTML(p.station_name || p.station_id), self: false});
            }
        }
        if (owners.length === 0) return '{{_('–')}}';
        return owners.map(o => o.self ? '<strong>' + o.name + '</strong>' : o.name).join(' + ');
    }

    function viewedByLabel(entry, thisStation, peers) {
        const viewers = [];
        if (thisStation.viewer_ids.indexOf(entry.id) !== -1) {
            viewers.push({name: '{{_('this station')}}', self: true});
        }
        for (const p of peers) {
            if (p.viewer_ids.indexOf(entry.id) !== -1) {
                viewers.push({name: escapeHTML(p.station_name || p.station_id), self: false});
            }
        }
        if (viewers.length === 0) return '{{_('–')}}';
        return viewers.map(v => v.self ? '<strong>' + v.name + '</strong>' : v.name).join(' + ');
    }

    function controlConflict(entry, thisStation, peers) {
        let count = thisStation.controlled_ids.indexOf(entry.id) !== -1 ? 1 : 0;
        for (const p of peers) {
            if (p.controlled_ids.indexOf(entry.id) !== -1) count++;
        }
        return count > 1;
    }

    // ---- DOM helpers: write only when value differs AND not focused.
    // Skipping the write when ``document.activeElement === el`` keeps
    // mid-edit text inputs from being clobbered by the poll; the
    // operator's typing wins until they blur. (Colour fields are no
    // longer native inputs – see ``setSwatchIfChanged`` below.)
    function setInputIfChanged(el, value) {
        if (document.activeElement === el) return;
        if (el.value !== value) el.value = value;
    }

    function setTextIfChanged(el, text) {
        if (el.textContent !== text) el.textContent = text;
    }

    // Cache the last-set HTML string on the element so we don't pay
    // the cost of reading ``el.innerHTML`` (which the browser
    // re-serializes and may renormalize attribute quoting / ordering /
    // entity encoding, producing a string that's semantically equal
    // but byte-different from what we set). A mismatched compare here
    // would rewrite every poll, destroying the cell's child nodes
    // (visible as flicker on adjacent inputs).
    function setHTMLIfChanged(el, html) {
        if (el._lastHTML === html) return;
        el.innerHTML = html;
        el._lastHTML = html;
    }

    function setCheckedIfChanged(el, checked) {
        if (document.activeElement === el) return;
        if (el.checked !== checked) el.checked = checked;
    }

    // ---- Selection POST (out-of-band write, same wire as before).
    function postSelection(root) {
        const controlled = [];
        const viewer = [];
        root.querySelectorAll('[data-selection-field="control"]').forEach(function(cb) {
            if (cb.checked) controlled.push(parseInt(cb.getAttribute('data-marker-id'), 10));
        });
        root.querySelectorAll('[data-selection-field="view"]').forEach(function(cb) {
            if (cb.checked) viewer.push(parseInt(cb.getAttribute('data-marker-id'), 10));
        });
        fetch('/api/markers/selection', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({controlled_ids: controlled, viewer_ids: viewer})
        }).then(function(resp) {
            const flash = root.querySelector('#selection-saved-flash');
            if (!flash) return;
            flash.textContent = resp.ok ? '{{_('✓ saved')}}' : '{{_('⚠ save failed')}}';
            flash.classList.add('show');
            setTimeout(function() { flash.classList.remove('show'); }, 1200);
        });
    }

    // ---- Row factories: build the row once, attach listeners once.
    function createCatalogRow(id) {
        const tr = document.createElement('tr');
        tr.setAttribute('data-marker-id', String(id));
        // Colour cell is swatch <button>, not native picker. Save handler
        // reads dataset.value; picker wired once here (persists across
        // polls as diff renderer reuses row node).
        tr.innerHTML =
            '<td data-cell="id"></td>' +
            '<td><input type="text" data-field="name"></td>' +
            '<td><button type="button" class="color-swatch-trigger" data-field="color" ' +
                'data-color-picker="full" aria-label="{{_('Marker colour')}}"></button></td>' +
            '<td class="cell-soft" data-cell="controlled-by"></td>' +
            '<td class="cell-soft" data-cell="viewed-by"></td>' +
            '<td>' +
                '<span style="display:inline-flex;align-items:center;gap:0.4rem;">' +
                '<button type="button" class="save-btn small" data-action="save">{{_('Save')}}</button>' +
                '<button type="button" class="danger small" data-action="delete">{{_('Delete')}}</button>' +
                '</span>' +
            '</td>';
        window.OpenFollow.attachColorPicker(tr.querySelector('[data-field="color"]'), {
            mode: 'full',
        });
        tr.querySelector('[data-action="save"]').addEventListener('click', function() {
            const name = tr.querySelector('[data-field="name"]').value;
            const color = tr.querySelector('[data-field="color"]').dataset.value;
            fetch('/api/markers/catalog/' + id, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name: name, color: color})
            });
        });
        tr.querySelector('[data-action="delete"]').addEventListener('click', function() {
            if (!confirm('{{_('Delete marker ')}}' + id + '?')) return;
            fetch('/api/markers/catalog/' + id, {method: 'DELETE'});
        });
        return tr;
    }

    function updateCatalogRow(tr, entry, thisStation, peers) {
        const conflict = controlConflict(entry, thisStation, peers);
        tr.classList.toggle('conflict', conflict);
        setTextIfChanged(tr.querySelector('[data-cell="id"]'), String(entry.id));
        setInputIfChanged(tr.querySelector('[data-field="name"]'), entry.name || '');
        setSwatchIfChanged(tr.querySelector('[data-field="color"]'), entry.color || '');
        const ctrlHTML = controlledByLabel(entry, thisStation, peers) +
            (conflict ? ' <span title="{{_('More than one station claims control')}}" class="conflict-flag">⚠</span>' : '');
        setHTMLIfChanged(tr.querySelector('[data-cell="controlled-by"]'), ctrlHTML);
        setHTMLIfChanged(tr.querySelector('[data-cell="viewed-by"]'), viewedByLabel(entry, thisStation, peers));
    }

    function createSelectionRow(id, root) {
        const tr = document.createElement('tr');
        tr.setAttribute('data-marker-id', String(id));
        tr.innerHTML =
            '<td data-cell="id"></td>' +
            '<td data-cell="name"></td>' +
            '<td><input type="checkbox" data-selection-field="control" data-marker-id="' + id + '"></td>' +
            '<td><input type="checkbox" data-selection-field="view" data-marker-id="' + id + '"></td>';
        // Checkboxes auto-save on change so the 1.5 s catalog poll
        // can't clobber the operator's in-progress selection.
        tr.querySelectorAll('[data-selection-field]').forEach(function(cb) {
            cb.addEventListener('change', function() { postSelection(root); });
        });
        return tr;
    }

    function updateSelectionRow(tr, entry, thisStation) {
        setTextIfChanged(tr.querySelector('[data-cell="id"]'), String(entry.id));
        setTextIfChanged(tr.querySelector('[data-cell="name"]'), entry.name || '');
        setCheckedIfChanged(
            tr.querySelector('[data-selection-field="control"]'),
            thisStation.controlled_ids.indexOf(entry.id) !== -1,
        );
        setCheckedIfChanged(
            tr.querySelector('[data-selection-field="view"]'),
            thisStation.viewer_ids.indexOf(entry.id) !== -1,
        );
    }

    function createAddRow() {
        const tr = document.createElement('tr');
        tr.className = 'add-row';
        // Colour cell is swatch <button>. Add handler reads
        // dataset.value; picker wired once here.
        // Add-row status span reports duplicate / success / error.
        tr.innerHTML =
            '<td><input type="number" min="1" id="new-marker-id" style="width: 4rem;"></td>' +
            '<td><input type="text" id="new-marker-name"></td>' +
            '<td><button type="button" class="color-swatch-trigger" id="new-marker-color" ' +
                'data-color-picker="full" aria-label="{{_('New marker colour')}}"></button></td>' +
            '<td colspan="2"><span id="add-marker-feedback" class="add-feedback" aria-live="polite"></span></td>' +
            '<td><button type="button" class="save-btn" id="add-marker-btn">{{_('Add')}}</button></td>';
        const idIn = tr.querySelector('#new-marker-id');
        const nameIn = tr.querySelector('#new-marker-name');
        const colorTrigger = tr.querySelector('#new-marker-color');
        const feedback = tr.querySelector('#add-marker-feedback');
        window.OpenFollow.attachColorPicker(colorTrigger, { mode: 'full' });

        // state: 'ok' | 'error' | null
        function flash(msg, state) {
            feedback.textContent = msg;
            feedback.classList.toggle('ok', state === 'ok');
            feedback.classList.toggle('error', state === 'error');
        }

        function submit() {
            const id = parseInt(idIn.value, 10);
            if (!id || id < 1) {
                flash('{{_('Enter an id ≥ 1')}}', 'error');
                idIn.focus();
                return;
            }
            // A live id has a catalog row -> block as duplicate. A tombstoned
            // id has no row -> falls through to the PUT, which upsert resurrects.
            const dup = tr.parentNode &&
                tr.parentNode.querySelector('[data-marker-id="' + id + '"]');
            if (dup) {
                flash('{{_('Marker ')}}' + id + '{{_(' already exists – edit it above')}}', 'error');
                return;
            }
            const name = nameIn.value || ('{{_('Marker ')}}' + id);
            const color = colorTrigger.dataset.value;
            flash('{{_('Adding…')}}', null);
            fetch('/api/markers/catalog/' + id, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name: name, color: color})
            }).then(function(resp) {
                if (!resp.ok) throw new Error('http ' + resp.status);
                flash('{{_('✓ Added marker ')}}' + id, 'ok');
                // Clear name and id so updateAddRow re-suggests the next-free id.
                nameIn.value = '';
                idIn.value = '';
                const root = document.getElementById('marker-catalog-root');
                if (root) {
                    fetch('/api/markers/catalog')
                        .then(function(r) { return r.json(); })
                        .then(function(d) { applyData(root, d); })
                        .catch(function() {});
                }
            }).catch(function() {
                flash('{{_('⚠ Could not add marker ')}}' + id, 'error');
            });
        }

        tr.querySelector('#add-marker-btn').addEventListener('click', submit);
        // Enter in either text field submits.
        [idIn, nameIn].forEach(function(el) {
            el.addEventListener('keydown', function(evt) {
                if (evt.key === 'Enter') { evt.preventDefault(); submit(); }
            });
        });
        // Editing either field clears a stale status message.
        [idIn, nameIn].forEach(function(el) {
            el.addEventListener('input', function() { flash('', null); });
        });
        return tr;
    }

    function updateAddRow(addRow, entries, nextFreeId) {
        const computedNextId = nextFreeId ||
            (entries.length ? entries[entries.length - 1].id + 1 : 1);
        const idIn = addRow.querySelector('#new-marker-id');
        const nameIn = addRow.querySelector('#new-marker-name');
        const colorTrigger = addRow.querySelector('#new-marker-color');
        // Seed the id field with next_free_id only while blank and unfocused,
        // so a typed value is not overwritten by the poll.
        if (document.activeElement !== idIn && idIn.value.trim() === '') {
            idIn.value = String(computedNextId);
        }
        if (document.activeElement !== nameIn) {
            // Name placeholder follows the typed id, else next_free_id.
            const typedId = parseInt(idIn.value, 10);
            const placeholder = '{{_('Marker ')}}' + (typedId >= 1 ? typedId : computedNextId);
            if (nameIn.placeholder !== placeholder) nameIn.placeholder = placeholder;
        }
        setSwatchIfChanged(colorTrigger, pickMarkerColor(entries));
    }

    // ---- Skeleton: built once per root. The wrapping group divs,
    // table headers, station-name span, save-flash span, and the
    // sticky add-row at the bottom of the catalog body never change
    // shape – only their inner data does, which is handled below.
    function ensureSkeleton(root) {
        if (root.dataset.skeletonReady === 'true') return;
        root.innerHTML =
            '<div class="group">' +
                '<h3 class="group-title">{{_('Shared catalog')}}</h3>' +
                '<p class="cell-soft">{{_('Synced across all stations on the LAN.')}}</p>' +
                '<table class="marker-catalog-table">' +
                    '<thead><tr>' +
                        '<th>{{_('ID')}}</th><th>{{_('Name')}}</th><th>{{_('Color')}}</th>' +
                        '<th>{{_('Controlled by')}}</th><th>{{_('Viewed by')}}</th><th></th>' +
                    '</tr></thead>' +
                    '<tbody data-role="catalog-body"></tbody>' +
                '</table>' +
            '</div>' +
            '<div class="group">' +
                `<h3 class="group-title">{{_('This station\'s selection')}} ` +
                    '<span class="section-note">(<span data-role="station-name"></span>) ' +
                        '<span id="selection-saved-flash" class="saved-flash" aria-live="polite"></span>' +
                    '</span>' +
                '</h3>' +
                '<table class="marker-selection-table">' +
                    '<thead><tr>' +
                        '<th>{{_('ID')}}</th><th>{{_('Name')}}</th><th>{{_('Control')}}</th><th>{{_('View')}}</th>' +
                    '</tr></thead>' +
                    '<tbody data-role="selection-body"></tbody>' +
                '</table>' +
            '</div>';
        const catalogBody = root.querySelector('[data-role="catalog-body"]');
        catalogBody.appendChild(createAddRow());
        root.dataset.skeletonReady = 'true';
    }

    // ---- Keyed list reconciliation. Walks ``entries`` in order,
    // reuses existing rows (so handlers + focused inputs survive),
    // creates rows for new ids, and removes rows for tombstoned ids.
    // ``terminalRow`` (the add-row) stays anchored at the end.
    function reconcileBody(tbody, entries, makeRow, updateRow, terminalRow) {
        const existing = new Map();
        for (const tr of Array.from(tbody.children)) {
            if (tr === terminalRow) continue;
            const id = tr.getAttribute('data-marker-id');
            if (id !== null) existing.set(id, tr);
        }
        let cursor = tbody.firstChild;
        for (const e of entries) {
            const key = String(e.id);
            let row = existing.get(key);
            if (row) {
                existing.delete(key);
            } else {
                row = makeRow(e.id);
            }
            updateRow(row, e);
            if (cursor !== row) tbody.insertBefore(row, cursor);
            cursor = row.nextSibling;
        }
        for (const stale of existing.values()) stale.remove();
        if (terminalRow && tbody.lastChild !== terminalRow) {
            tbody.appendChild(terminalRow);
        }
    }

    function applyData(root, data) {
        ensureSkeleton(root);
        const entries = data.entries || [];
        const thisStation = data.this_station ||
            {station_name: '', controlled_ids: [], viewer_ids: []};
        const peers = data.peer_selections || [];

        setTextIfChanged(
            root.querySelector('[data-role="station-name"]'),
            thisStation.station_name || '',
        );

        const catalogBody = root.querySelector('[data-role="catalog-body"]');
        const addRow = catalogBody.querySelector('.add-row');
        reconcileBody(
            catalogBody, entries,
            function(id) { return createCatalogRow(id); },
            function(tr, e) { updateCatalogRow(tr, e, thisStation, peers); },
            addRow,
        );
        updateAddRow(addRow, entries, data.next_free_id);

        const selectionBody = root.querySelector('[data-role="selection-body"]');
        reconcileBody(
            selectionBody, entries,
            function(id) { return createSelectionRow(id, root); },
            function(tr, e) { updateSelectionRow(tr, e, thisStation); },
            null,
        );
    }

    document.body.addEventListener('htmx:afterRequest', function(evt) {
        // Re-resolve root each fire – after a marker form save htmx
        // swaps the outer form's outerHTML, replacing the catalog root
        // with a fresh element that the original IIFE never saw. The
        // ``data-skeleton-ready`` flag travels with the element, so a
        // fresh root (without the flag) re-builds the skeleton on its
        // first poll.
        const root = document.getElementById('marker-catalog-root');
        if (!root || evt.detail.elt !== root) return;
        try {
            const data = JSON.parse(evt.detail.xhr.responseText);
            applyData(root, data);
        } catch (e) { /* ignore */ }
    });
})();
</script>

<style>
.marker-catalog-table, .marker-selection-table {
    width: 100%;
    border-collapse: collapse;
    margin-top: 0.5rem;
}
.marker-catalog-table th, .marker-catalog-table td,
.marker-selection-table th, .marker-selection-table td {
    padding: 0.4rem 0.6rem;
    border-bottom: 1px solid rgba(255,255,255,0.08);
    text-align: left;
}
.marker-catalog-table tr.conflict {
    background: rgba(255,0,0,0.08);
    outline: 1px solid rgba(255,0,0,0.5);
}
.marker-catalog-table .conflict-flag {
    color: #f55;
    font-weight: bold;
}
.marker-catalog-table .cell-soft, .saved-flash {
    color: rgba(255,255,255,0.75);
    font-size: 0.92em;
}
/* Add-row status line: neutral, green ok, red error. */
.marker-catalog-table .add-feedback {
    font-size: 0.92em;
    color: rgba(255,255,255,0.75);
}
.marker-catalog-table .add-feedback.ok { color: #6cf07a; font-weight: bold; }
.marker-catalog-table .add-feedback.error { color: #f55; }
.marker-catalog-table input[type="text"] {
    width: 100%;
    box-sizing: border-box;
}
.saved-flash {
    color: #6cf07a;
    font-weight: bold;
    opacity: 0;
    transition: opacity 0.25s ease-in;
    margin-left: 0.5rem;
}
.saved-flash.show {
    opacity: 1;
    transition: opacity 0.1s ease-in;
}
</style>
