% import json
% NOTES = {'buttons_unrecognised': ('slot-note', 'Buttons not recognised'), 'button_map_other_model': ('slot-note', 'Button map is for another model'), 'cannot_identify': ('slot-note-fact', "Can't identify")}
% items = (controllers or {}).get('items', [])
% capture = (controllers or {}).get('pin_capture') or {}
% armed = capture.get('slot')
% result = capture.get('result')
% if not items:
<p class="slot-empty">No controller connected.</p>
% else:
<table class="slot-table data-table">
    <thead>
        <tr>
            <th scope="col">Slot</th>
            <th scope="col">Controller</th>
            <th scope="col">Connection</th>
            <th scope="col">Marker</th>
            <th scope="col" aria-label="Actions"></th>
        </tr>
    </thead>
    <tbody>
    % for c in items:
    % idx = int(c.get('controller_index', 0))
    % state = c.get('state', 'connected')
    % since = c.get('seconds_since_input')
    % in_use = state == 'connected' and since is not None and since < 1.5
    % ref = json.dumps({"ref": c.get("slot_ref", "")})
    % notes = [NOTES[n] for n in c.get('notes') or [] if n in NOTES]
        <tr class="slot-row slot-{{state}}">
            <th scope="row">C{{idx + 1}}</th>
            <td>{{c.get('name') or '-'}}\\
            % if state == 'connected':
<span class="slot-activity{{' is-active' if in_use else ''}}" role="img" aria-label="{{'In use' if in_use else 'Idle'}}"></span>\\
            % end
            % if notes:
<div class="slot-notes">\\
                % for cls, label in notes:
<span class="{{cls}}">{{label}}</span>\\
                % end
</div>\\
            % end
</td>
            <td>
            % if state == 'empty':
                <span class="slot-kind"><span class="slot-state">Empty</span></span>
            % else:
                <span class="slot-kind">{{'3D Mouse' if c.get('kind') == 'mouse3d' else 'Gamepad'}}<span class="slot-state{{' missing' if state == 'missing' else ''}}">{{state.capitalize()}}</span></span>
                % if c.get('pinned'):
                <span class="slot-port">Pinned to {{c.get('port_label') or ''}}</span>
                % else:
                <span class="slot-port">{{('on ' + str(c.get('port_label') or '')) if c.get('port_key') else 'no stable port'}}</span>
                % end
            % end
            </td>
            % if c.get('marker_id') is not None:
            <td><span class="slot-marker"><span class="slot-marker-dot" style="--marker-color: {{c.get('marker_color', '')}}"></span>{{c.get('marker_label') or c['marker_id']}}\\
            % if c.get('parked'):
<span class="park-badge"><span aria-hidden="true">P</span><span class="visually-hidden">Parked</span></span>\\
            % end
</span></td>
            % else:
            <td>-</td>
            % end
            <td class="row-actions">
            % if armed == idx:
                <span class="slot-waiting">Press a button · {{int(-(-float(capture.get('seconds_left') or 0) // 1))}} s</span>
                <button type="button" class="secondary small" hx-post="/section/controller_slots/cancel-pin/{{idx}}" hx-target="#controller-slots-content" hx-swap="innerHTML">Cancel</button>
            % else:
                % if state == 'connected':
                <button type="button" class="secondary small" hx-post="/section/controller_slots/identify/{{idx}}" hx-vals='{{ref}}' hx-target="#controller-slots-content" hx-swap="innerHTML">Identify</button>
                % elif state == 'missing':
                <button type="button" class="danger small" hx-post="/section/controller_slots/forget/{{idx}}" hx-vals='{{ref}}' hx-target="#controller-slots-content" hx-swap="innerHTML">Forget</button>
                % end
                % if c.get('pinned'):
                <button type="button" class="secondary small" hx-post="/section/controller_slots/unpin/{{idx}}" hx-vals='{{ref}}' hx-target="#controller-slots-content" hx-swap="innerHTML">Unpin</button>
                % end
                % if armed is None:
                <button type="button" class="secondary small" hx-post="/section/controller_slots/pin/{{idx}}" hx-vals='{{ref}}' hx-target="#controller-slots-content" hx-swap="innerHTML">Pin here</button>
                % end
            % end
            </td>
        </tr>
    % end
    </tbody>
</table>
% end
% if result:
%   slot_name = 'C' + str(int(result.get('slot', 0)) + 1)
%   outcome = result.get('outcome')
%   if outcome == 'pinned':
<p class="slot-result ok">{{slot_name}} pinned to {{result.get('detail') or ''}}.</p>
%   elif outcome == 'no_port':
<p class="slot-result error">{{result.get('detail') or 'That controller'}} has no stable port and can't be pinned.</p>
%   elif outcome == 'no_slot':
<p class="slot-result error">That controller holds no slot, so it can't be pinned.</p>
%   elif outcome == 'timeout':
<p class="slot-result">No button was pressed. {{slot_name}} was not pinned.</p>
%   end
% end
