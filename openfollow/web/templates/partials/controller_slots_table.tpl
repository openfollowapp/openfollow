% items = (controllers or {}).get('items', [])
% if not items:
<p class="slot-empty">No controller connected.</p>
% else:
<table class="slot-table">
    <thead>
        <tr>
            <th scope="col">Slot</th>
            <th scope="col">Controller</th>
            <th scope="col">Kind</th>
            <th scope="col">Port</th>
            <th scope="col">State</th>
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
        <tr class="slot-row slot-{{state}}">
            <th scope="row">C{{idx + 1}}</th>
            <td>{{c.get('name') or '-'}}</td>
            <td>{{'3D Mouse' if c.get('kind') == 'mouse3d' else 'Gamepad'}}</td>
            <td>{{c.get('port_label') or 'no stable port'}}</td>
            <td>
            % if state == 'connected':
                <span class="slot-activity{{' is-active' if in_use else ''}}" role="img" aria-label="{{'In use' if in_use else 'Idle'}}"></span> Connected
            % elif state == 'missing':
                <strong class="slot-missing-label">Missing</strong>
            % else:
                <span class="slot-reserved-label">Reserved</span>
            % end
            </td>
            <td>{{c['marker_id'] if c.get('marker_id') is not None else '-'}}</td>
            <td class="slot-actions">
            % if state != 'reserved':
                <button type="button" class="secondary" hx-post="/section/controller_slots/identify/{{idx}}" hx-target="#controller-slots-content" hx-swap="innerHTML">Identify</button>
            % end
            % if state == 'missing':
                <button type="button" class="secondary" hx-post="/section/controller_slots/forget/{{idx}}" hx-target="#controller-slots-content" hx-swap="innerHTML">Forget</button>
            % end
            </td>
        </tr>
    % end
    </tbody>
</table>
% end
