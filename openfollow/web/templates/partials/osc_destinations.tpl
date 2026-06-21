% # Shared OSC destination profiles: collapsible rows with a per-row form.
% # Transmitters and zones reference a destination by id; editing one here
% # repoints every consumer live. Every CRUD route returns this full partial
% # so row order stays consistent; ``focus_id`` re-opens the changed row.
% destinations = config.osc_destinations.destinations
<div id="osc-destinations-section" class="section {{'saved' if defined('saved') and saved else ''}}" data-fold-key="osc_destinations" data-help="osc_destinations">
 <div class="section-head">
 <h2>OSC Destinations</h2>
 <span class="section-note">Reusable connections (host, port, transport) referenced by transmitters and zones</span>
 </div>

 <div class="osc-destinations-list">
 % if not destinations:
 <p class="empty-state">No OSC destinations configured. Use <em>+ New destination</em> below to create one.</p>
 % end
 % for idx, dest in enumerate(destinations):
 % is_focus = (defined('focus_id') and focus_id == dest.id)
 % proto_label = '{}://{}:{} ({})'.format(dest.protocol, dest.host, dest.port, dest.framing if dest.protocol == 'tcp' else dest.protocol)
 <details class="osc-destination-row" data-row-id="{{dest.id}}" {{'open' if is_focus else ''}}>
 <summary class="osc-destination-summary">
 <span class="osc-destination-title">{{dest.name or '(unnamed)'}}</span>
 <span class="osc-destination-target">{{proto_label}}</span>
 </summary>

 <form class="osc-destination-form"
 data-row-id="{{dest.id}}"
 hx-post="/section/osc_destination/{{dest.id}}"
 hx-target="#osc-destinations-section"
 hx-swap="outerHTML"
 hx-trigger="submit">
 <div class="row">
 <div class="field">
 <label>Name</label>
 <input type="text" name="name" value="{{dest.name}}" maxlength="64"
 hx-get="/api/validate/osc_destination/name"
 hx-trigger="blur changed delay:200ms"
 hx-target="#dest-name-{{dest.id}}-error"
 hx-swap="innerHTML"
 hx-include="closest form"
 aria-describedby="dest-name-{{dest.id}}-error"
 aria-invalid="false">
 <span id="dest-name-{{dest.id}}-error" class="field-error"></span>
 </div>
 </div>
 <div class="row">
 <div class="field">
 <label>Host</label>
 <input type="text" name="host" value="{{dest.host}}" maxlength="255"
 hx-get="/api/validate/osc_destination/host"
 hx-trigger="blur changed delay:200ms"
 hx-target="#dest-host-{{dest.id}}-error"
 hx-swap="innerHTML"
 hx-include="closest form"
 aria-describedby="dest-host-{{dest.id}}-error"
 aria-invalid="false">
 <span id="dest-host-{{dest.id}}-error" class="field-error"></span>
 </div>
 <div class="field">
 <label>Port</label>
 <input type="number" name="port" value="{{dest.port}}" min="1" max="65535" step="1"
 hx-get="/api/validate/osc_destination/port"
 hx-trigger="blur changed delay:200ms"
 hx-target="#dest-port-{{dest.id}}-error"
 hx-swap="innerHTML"
 hx-include="closest form"
 aria-describedby="dest-port-{{dest.id}}-error"
 aria-invalid="false">
 <span id="dest-port-{{dest.id}}-error" class="field-error"></span>
 </div>
 <div class="field">
 <label>Protocol</label>
 <select name="protocol" data-osc-protocol-select>
 % for p in valid_protocols:
 <option value="{{p}}" {{'selected' if p == dest.protocol else ''}}>{{p.upper()}}</option>
 % end
 </select>
 </div>
 % # TCP framing selector; always rendered for round-trip, hidden by JS for non-TCP.
 <div class="field" data-osc-framing-field {{'style="display:none"' if dest.protocol != 'tcp' else ''}}>
 <label>Framing</label>
 <select name="framing">
 % for f in valid_framings:
 <option value="{{f}}" {{'selected' if f == dest.framing else ''}}>{{'SLIP (RFC 1055)' if f == 'slip' else 'Length-prefix (OSC 1.0)'}}</option>
 % end
 </select>
 </div>
 </div>
 <div class="row-actions">
 <button type="submit" class="save-btn">Save</button>
 <button type="button" class="secondary-btn"
 hx-post="/section/osc_destination/{{dest.id}}/duplicate"
 hx-target="#osc-destinations-section" hx-swap="outerHTML">Duplicate</button>
 <button type="button" class="secondary-btn"
 hx-post="/section/osc_destination/{{dest.id}}/move" hx-vals='{"direction":"up"}'
 hx-target="#osc-destinations-section" hx-swap="outerHTML">↑</button>
 <button type="button" class="secondary-btn"
 hx-post="/section/osc_destination/{{dest.id}}/move" hx-vals='{"direction":"down"}'
 hx-target="#osc-destinations-section" hx-swap="outerHTML">↓</button>
 <button type="button" class="danger-btn"
 hx-post="/section/osc_destination/{{dest.id}}/delete"
 hx-target="#osc-destinations-section" hx-swap="outerHTML"
 hx-confirm="Delete this destination? Transmitters and zones referencing it will stop sending until repointed.">Delete</button>
 </div>
 </form>
 </details>
 % end
 </div>

 <div class="section-actions">
 <button type="button" class="add-btn"
 hx-post="/section/osc_destinations/add"
 hx-target="#osc-destinations-section" hx-swap="outerHTML">+ New destination</button>
 </div>
</div>
