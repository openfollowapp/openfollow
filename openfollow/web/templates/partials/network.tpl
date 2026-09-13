%# Network Interface configuration.
%#
%# The card is the list of every interface on the station, each a collapsible
%# row carrying its own editor – the same shape as OSC Transmitters. Expansion
%# is the browser's to remember (``data-adv-key``), so the 5s poll refreshes
%# addresses without closing the row an operator is reading.
%#
%# Editing is per row: an expanded row reads out its settings with an Edit
%# button, and Edit swaps that one row into Apply / Renew / Cancel. Each row is
%# its own form, so the interface it writes to is the one named in its summary.
%# ``editing_iface`` names the row being edited; every other row stays
%# read-only, and a card with none live-polls its addresses.
%#
%# Rendered into the #network-interface region (heading + fold live in
%# general.tpl), swapped in place on toggle / apply / renew.
%#
%# Tolerant of missing context: callers without ``net`` get the unavailable
%# state instead of a NameError.
% _net = net if defined('net') else {"available": False, "writable": False, "editable": False}
% _writable = _net.get("writable")
% _editable = bool(_net.get("editable") and _writable)
% _banner = _net.get("banner")
% _rows = _net.get("iface_rows", [])
% _session = _net.get("session_iface", "")
% _session_addr = _net.get("session_address", "")
% _editing = _net.get("editing_iface", "")
% _polls = _net.get("available") and not _editable
%# Offered whenever the host is writable: creating a link is its own confirmed
%# action, not something a row's Edit button gates. On a read-only or
%# non-NetworkManager stack the controls are omitted entirely.
% _vlan_parents = _net.get("vlan_parents", [])
% _vlan_add = bool(_writable and _net.get("supports_vlans") and _vlan_parents)
%# A refused Create comes back with what was entered, so the block reopens
%# rather than making the operator retype it.
% _vform = _net.get("vlan_form") or {}
% _vopen = bool(_vform)
<div id="network-config-section" class="network-config"
% if _polls:
     hx-get="/section/network/status" hx-trigger="every 5s [netPollAllowed()]"
     hx-target="#network-interface" hx-swap="innerHTML"
% end
     >
    % if _banner:
    <div class="network-banner network-banner-{{_banner.get('kind', 'info')}}">{{_banner.get('text', '')}}</div>
    % end

    % if not _net.get("available"):
    <p class="muted">Network configuration is unavailable – no network adapter is configured on this host (or this build has none wired), so there is nothing to edit.</p>
    % else:

    %# A writable host needs no card-level mode: each row says whether it is
    %# being edited. A host that can't be written from the web has to say so
    %# once, or its disabled fields read as a fault.
    % if not _writable:
    <div class="net-mode-bar readonly">
        <span class="net-mode-pill readonly">Read only</span>
        <span class="net-mode-text">Network settings can't be changed from the web on this station. Use the on-screen Settings menu, or see openfollow.app for troubleshooting and how to enable web editing.</span>
    </div>
    % end

    <div class="group">
        % if not _rows:
        <p class="muted">No network interfaces detected.</p>
        % else:
        <div class="net-iface-list">
            % for row in _rows:
            % _name = row.get("name", "")
            % _addr = row.get("address", "")
            % _prefix = row.get("prefix")
            % _rmethod = row.get("method") or "dhcp"
            % _vlan_id = row.get("vlan_id")
            % _row_edit = bool(_editable and _name and _name == _editing)
            % _dis = '' if _row_edit else 'disabled'
            %# The row being edited – and the row an apply reported on – opens
            %# itself and outranks the remembered state.
            % _force = 'open data-adv-force-open="1"' if _name and _name == _editing else ''
            <details class="net-iface-row" data-adv-key="net-iface-{{_name}}"
                     data-mode="{{'edit' if _row_edit else 'view'}}" data-method="{{_rmethod}}" {{!_force}}>
                <summary class="net-iface-summary">
                    <span class="ia-dot {{'up' if _addr else 'down'}}" aria-hidden="true"></span>
                    <span class="net-iface-name"><code>{{_name}}</code></span>
                    % if _name and _name == _session:
                    %# Guards the operator against editing the adapter whose
                    %# address is answering their own session.
                    <span class="stat-chip info" title="This station is answering your browser at {{_session_addr}}, an address on this interface">This session</span>
                    % end
                    % if _vlan_id is not None:
                    <span class="stat-chip">VLAN {{_vlan_id}}</span>
                    % end
                    <span class="net-iface-addr {{'' if _addr else 'muted'}}">{{(_addr + ('/' + str(_prefix) if _prefix else '')) if _addr else '(no address)'}}</span>
                    <span class="net-iface-method-badge">{{row.get('method_label', '')}}</span>
                </summary>

                <form class="net-iface-form"
                % if _row_edit:
                      hx-post="/section/network/apply" hx-target="#network-interface"
                      hx-swap="innerHTML" hx-trigger="submit"
                      hx-on:submit="netScheduleReload(this)"
                % end
                      >
                    <input type="hidden" name="iface" value="{{_name}}">

                    %# Names the address rather than claiming the operator is on
                    %# this network: the station answers for any of its addresses
                    %# on whatever interface the request rode in on, so this can
                    %# be a VLAN reached from the untagged LAN. Either way,
                    %# editing it drops the session.
                    % if _name and _name == _session:
                    <div class="notice warning" role="status">
                        <strong>This station is answering your browser at {{_session_addr}}.</strong>
                        That address belongs to this interface, so changing its addressing
                        will drop this web session. A static or manual address reloads the UI
                        at the new one automatically; otherwise the station stays reachable by
                        name and from the on-screen <em>Settings &rsaquo; Network</em> menu.
                    </div>
                    % end

                    <div class="network-grid">
                        <label for="net-method-{{_name}}">Method</label>
                        <select id="net-method-{{_name}}" class="net-method-select" name="method" {{_dis}}>
                            <option value="dhcp" {{'selected' if _rmethod == 'dhcp' else ''}}>DHCP (automatic)</option>
                            <option value="dhcp_manual" {{'selected' if _rmethod == 'dhcp_manual' else ''}}>DHCP with manual address</option>
                            <option value="static" {{'selected' if _rmethod == 'static' else ''}}>Static</option>
                        </select>
                    </div>

                    %# View mode reads out every address field whatever the
                    %# method; Edit mode shows only the ones that method lets
                    %# you set, and disables the rest so they can't post.
                    <div class="group net-addressing">
                        <h4 class="group-title">Addressing</h4>
                        <div class="network-grid">
                            <label for="net-address-{{_name}}">IP address</label>
                            <input id="net-address-{{_name}}" type="text" name="address"
                                   value="{{row.get('address', '')}}" placeholder="192.168.1.50" {{_dis}}>
                            <label class="net-static-only" for="net-subnet-{{_name}}">Subnet mask</label>
                            <input class="net-static-only" id="net-subnet-{{_name}}" type="text" name="subnet_mask"
                                   value="{{row.get('subnet_mask', '')}}" placeholder="255.255.255.0" {{_dis}}>
                            <label class="net-static-only" for="net-router-{{_name}}">Router (optional)</label>
                            <input class="net-static-only" id="net-router-{{_name}}" type="text" name="router"
                                   value="{{row.get('router', '')}}" placeholder="192.168.1.1" {{_dis}}>
                        </div>
                    </div>

                    <div class="group">
                        <h4 class="group-title">DNS</h4>
                        <div class="network-grid">
                            % _dns = row.get("dns") or []
                            % for i in range(3):
                            <label for="net-dns{{i + 1}}-{{_name}}">Server {{i + 1}}</label>
                            <input id="net-dns{{i + 1}}-{{_name}}" type="text" name="dns{{i + 1}}"
                                   value="{{_dns[i] if i < len(_dns) else ''}}"
                                   placeholder="1.1.1.1" {{_dis}}>
                            % end
                        </div>
                    </div>

                    %# Renew acts on what this group reports, so it lives here
                    %# rather than beside Apply. Static has no lease, and the
                    %# group carries the class that hides it for that method.
                    % _lease = row.get("lease_display")
                    % if _lease or _row_edit:
                    <div class="group net-dhcp-only">
                        <h4 class="group-title">Lease</h4>
                        <div class="network-grid">
                            <label>Remaining</label>
                            <span class="network-grid-value net-lease-value">{{_lease or '–'}}
                                % if _row_edit:
                                <button type="button" class="secondary small"
                                        hx-post="/section/network/renew" hx-target="#network-interface"
                                        hx-swap="innerHTML" hx-include="closest form">Renew DHCP lease</button>
                                % end
                            </span>
                        </div>
                    </div>
                    % end

                    % if _row_edit:
                    <div class="actions">
                        <button type="submit" class="save-btn">Apply</button>
                        %# Delete lives inside the row's own form, so it can
                        %# only ever be aimed at the interface named above it.
                        % if _vlan_id is not None:
                        <button type="button" class="danger"
                                hx-post="/section/network/vlan/delete" hx-target="#network-interface"
                                hx-swap="innerHTML" hx-include="closest form"
                                hx-confirm="Any function pinned to {{_name}} stops sending until it is reassigned."
                                data-confirm-title="Delete {{_name}}?" data-confirm-label="Delete" data-confirm-danger>Delete VLAN</button>
                        % end
                        %# Drops the card back to all-rows-read-only. The row
                        %# itself stays expanded - that is the browser's state,
                        %# not the server's.
                        <button type="button" class="ghost-btn"
                                hx-get="/section/network/status"
                                hx-target="#network-interface"
                                hx-swap="innerHTML">Cancel</button>
                    </div>
                    % elif _writable:
                    %# Editing one row at a time: opening this one closes any
                    %# other row's editor, so two adapters can never be
                    %# half-edited against each other.
                    <div class="actions">
                        <button type="button" class="secondary"
                                hx-get="/section/network/edit/{{_name}}"
                                hx-target="#network-interface"
                                hx-swap="innerHTML">Edit</button>
                    </div>
                    % end
                </form>
            </details>
            % end
        </div>
        % end

        <div class="ia-legend">
            <span><span class="ia-dot up"></span> up with an address</span>
            <span><span class="ia-dot down"></span> no address</span>
            <span class="ia-legend-actions">
                % if _vlan_add:
                <button type="button" class="secondary small" {{'hidden' if _vopen else ''}}
                        onclick="this.closest('.group').querySelector('.ia-vlan-add').hidden = false; this.hidden = true;">+ Add VLAN</button>
                % end
                %# Keeps the edited row in the path, so re-reading the
                %# adapter list doesn't discard what the operator has typed.
                <button type="button" class="secondary small"
                        hx-get="{{('/section/network/edit/' + _editing) if (_editable and _editing) else '/section/network/status'}}?scan=1"
                        hx-target="#network-interface" hx-swap="innerHTML">Scan</button>
            </span>
        </div>

        % if _vlan_add:
        <form class="ia-vlan-add" {{'' if _vopen else 'hidden'}}>
            <h4 class="group-title">Add VLAN</h4>
            <div class="network-grid">
                <label for="net-vlan-parent">Parent interface</label>
                <select id="net-vlan-parent" name="vlan_parent">
                    % for _parent in _vlan_parents:
                    <option value="{{_parent}}" {{'selected' if _parent == _vform.get('parent') else ''}}>{{_parent}}</option>
                    % end
                </select>

                <label for="net-vlan-id">VLAN ID</label>
                <input type="number" id="net-vlan-id" name="vlan_id" min="1" max="4094" step="1"
                       placeholder="10" value="{{_vform.get('vlan_id', '')}}">
            </div>
            <div class="actions">
                <button type="button" class="save-btn"
                        hx-post="/section/network/vlan/create" hx-target="#network-interface"
                        hx-swap="innerHTML" hx-include="closest form">Create</button>
                <button type="button" class="ghost-btn"
                        onclick="var b=this.closest('.group'); b.querySelector('.ia-vlan-add').hidden = true; b.querySelector('.ia-legend-actions button').hidden = false;">Cancel</button>
            </div>
        </form>
        % end
        % end
    </div>
    % end
</div>
