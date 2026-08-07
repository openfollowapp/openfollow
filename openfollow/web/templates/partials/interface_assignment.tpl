%# Interface Assignment – which network each function uses.
%#
%# Storage stays per-section (each pin lives on the sub-config that owns the
%# protocol); this panel is only the editing surface, so the protocol sections
%# show a read-only pointer here instead of their own picker.
%#
%# Rows come from ``build_interface_assignment_rows``: ``editable`` rows render
%# an interface picker, the rest render their ``note`` (they follow the station
%# pin and have no independent setting).
% rows = assignment_rows if defined('assignment_rows') else []
<form id="interface-assignment-section" class="section {{'saved' if defined('saved') and saved else ''}}"
      data-fold-key="general-interface-assignment" data-help="general-interface-assignment"
      data-fold-default="expanded"
      hx-post="/section/interface_assignment" hx-target="#interface-assignment-section"
      hx-swap="outerHTML" hx-trigger="submit">
    <div class="section-head">
        <h2>Interface Assignment</h2>
        <span class="section-note">Which network each function uses</span>
    </div>

    <table class="ia-table ia-assign">
        <thead>
            <tr><th>Function</th><th>Interface</th><th>Address</th></tr>
        </thead>
        <tbody>
            % for row in rows:
            <tr class="{{'ia-readonly' if not row['editable'] else ''}}">
                <th scope="row">{{row['label']}}</th>
                % if row['editable']:
                <td>
                    %# Interface names are stable across DHCP renewals; the
                    %# option list (and its blank-option wording) comes from the
                    %# shared route so every picker stays consistent.
                    %#
                    %# ``load`` only: ``current`` is baked in at render time, so
                    %# re-fetching on Scan would re-mark the SAVED value as
                    %# selected and silently discard an unsaved choice. Scan
                    %# re-renders the whole panel instead, which refreshes both
                    %# the option lists and the resolved addresses.
                    <select name="{{row['key']}}" aria-label="{{row['label']}} interface"
                            hx-get="/network/interfaces/by_name?blank={{row['blank']}}&current={{row['value']}}"
                            hx-trigger="load"
                            hx-target="this" hx-swap="innerHTML">
                        <option value="{{row['value']}}">{{row['value'] or '-- Loading... --'}}</option>
                    </select>
                </td>
                % else:
                <td class="muted">{{row.get('note', '')}}</td>
                % end
                <td class="ia-addr {{'muted' if not row['editable'] else ''}}">{{row['address'] or '--'}}</td>
            </tr>
            % end
        </tbody>
    </table>

    <div class="actions">
        <button type="submit" class="save-btn">Save</button>
        <button type="button" id="refresh-iface-assignment" class="secondary"
                hx-get="/section/interface_assignment"
                hx-target="#interface-assignment-section" hx-swap="outerHTML">Scan</button>
    </div>
</form>
