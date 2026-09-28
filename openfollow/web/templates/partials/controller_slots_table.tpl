% import json
% items = (controllers or {}).get('items', [])
% if not items:
<p class="slot-empty">No controller connected.</p>
% else:
<table class="slot-table">
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
        <tr class="slot-row slot-{{state}}">
            <th scope="row">C{{idx + 1}}</th>
            <td>{{c.get('name') or '-'}}\\
            % if state == 'connected':
<span class="slot-activity{{' is-active' if in_use else ''}}" role="img" aria-label="{{'In use' if in_use else 'Idle'}}"></span>\\
            % end
</td>
            <td>
                <span class="slot-kind">{{'3D Mouse' if c.get('kind') == 'mouse3d' else 'Gamepad'}}<span class="slot-state{{' missing' if state == 'missing' else ''}}">{{state.capitalize()}}</span></span>
                <span class="slot-port">{{('on ' + str(c.get('port_label') or '')) if c.get('port_key') else 'no stable port'}}</span>
            </td>
            % if c.get('marker_id') is not None:
            <td><span class="slot-marker"><span class="slot-marker-dot" style="--marker-color: {{c.get('marker_color', '')}}"></span>{{c.get('marker_label') or c['marker_id']}}</span></td>
            % else:
            <td>-</td>
            % end
            <td class="slot-actions">
            % if state == 'connected':
                <button type="button" class="secondary" hx-post="/section/controller_slots/identify/{{idx}}" hx-vals='{{ref}}' hx-target="#controller-slots-content" hx-swap="innerHTML">Identify</button>
            % elif state == 'missing':
                <button type="button" class="danger" hx-post="/section/controller_slots/forget/{{idx}}" hx-vals='{{ref}}' hx-target="#controller-slots-content" hx-swap="innerHTML">Forget</button>
            % end
            </td>
        </tr>
    % end
    </tbody>
</table>
% end
