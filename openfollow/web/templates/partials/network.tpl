%# Network Interface configuration.
%#
%# The card is the list of every interface on the station, each a collapsible
%# row carrying its own editor – the same shape as OSC Transmitters. Expansion
%# is the browser's to remember (``data-adv-key``), so the 5s poll refreshes
%# addresses without closing the row an operator is reading.
%#
%# The poll swaps ``#net-iface-list`` alone, not the whole card: everything
%# else here is the operator's own state – a half-typed Add VLAN entry, the
%# button that revealed it, the banner reporting what their last action did –
%# and a refresh of the adapter addresses has no business clearing any of it.
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
     hx-get="/section/network/status" hx-trigger="every 5s"
     hx-select="#net-iface-list" hx-target="#net-iface-list" hx-swap="outerHTML"
% end
     >
    % if _banner:
    <div class="network-banner network-banner-{{_banner.get('kind', 'info')}}">{{_banner.get('text', '')}}</div>
    % end

    % if not _net.get("available"):
    %# Wrapped in the swap target like the rows are. The poll selects
    %# ``#net-iface-list`` out of whatever comes back, so a render that omitted
    %# it would swap in nothing, delete the live list, and leave every later
    %# poll aimed at an element that no longer exists - the card would stay
    %# empty until a page reload, having been merely unreachable for one tick.
    <div class="group">
        <div id="net-iface-list" class="net-iface-list">
            <p class="muted">Network configuration is unavailable – no network adapter is configured on this host (or this build has none wired), so there is nothing to edit.</p>
        </div>
    </div>
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
        %# Rendered in every state - no rows, and no backend either: this is
        %# what the poll selects and swaps, so a render that omitted it would
        %# empty the live list instead of replacing it.
        <div id="net-iface-list" class="net-iface-list">
            % if not _rows:
            <p class="muted">No network interfaces detected.</p>
            % else:
            % for row in _rows:
            % _name = row.get("name", "")
            % _addr = row.get("address", "")
            % _prefix = row.get("prefix")
            %# What the operator typed on a rejected apply, for the fields
            %# only: the summary above them reports the adapter's own state.
            % _entered = row.get("entered") or {}
            % _rmethod = _entered.get("method") or row.get("method") or "dhcp"
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
                    <span class="ia-badge session" title="This station is answering your browser at {{_session_addr}}, an address on this interface">This session</span>
                    % end
                    % if _vlan_id is not None:
                    <span class="ia-badge vlan">VLAN {{_vlan_id}}</span>
                    % end
                    <span class="net-iface-addr {{'' if _addr else 'muted'}}">{{(_addr + ('/' + str(_prefix) if _prefix else '')) if _addr else '(no address)'}}</span>
                    <span class="net-iface-method-badge">{{row.get('method_label', '')}}</span>
                </summary>

                <form class="net-iface-form"
                % if _row_edit:
                      hx-post="/section/network/apply" hx-target="#network-interface"
                      hx-swap="innerHTML" hx-trigger="submit"
                    %# The blind reload is for an apply that severs this very
                    %# connection, so it is armed only on the row answering it.
                    %# On any other row the apply changes nothing about how the
                    %# browser got here, and a slow one would otherwise time out
                    %# and navigate to an address the operator cannot reach.
                    % if _name and _name == _session:
                      hx-on:submit="netScheduleReload(this)"
                    % end
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
                    %# Reached over IPv6, or at an address that is none of this
                    %# host's, there is no row to put the notice on - so every
                    %# editor carries the caution instead. Saying nothing is the
                    %# one outcome that leaves the operator unwarned.
                    % elif _row_edit and not _session:
                    <div class="notice warning" role="status">
                        Which interface is answering your browser can't be determined here,
                        so this may be the one carrying this web session. Applying would then
                        drop it; the station stays reachable by name and from the on-screen
                        <em>Settings &rsaquo; Network</em> menu.
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
                                   value="{{_entered.get('address', row.get('address', ''))}}" placeholder="192.168.1.50" {{_dis}}>
                            <label class="net-static-only" for="net-subnet-{{_name}}">Subnet mask</label>
                            <input class="net-static-only" id="net-subnet-{{_name}}" type="text" name="subnet_mask"
                                   value="{{_entered.get('subnet_mask', row.get('subnet_mask', ''))}}" placeholder="255.255.255.0" {{_dis}}>
                            <label class="net-static-only" for="net-router-{{_name}}">Router (optional)</label>
                            <input class="net-static-only" id="net-router-{{_name}}" type="text" name="router"
                                   value="{{_entered.get('router', row.get('router', ''))}}" placeholder="192.168.1.1" {{_dis}}>
                        </div>
                    </div>

                    <div class="group">
                        <h4 class="group-title">DNS</h4>
                        <div class="network-grid">
                            % _dns = _entered.get("dns") or row.get("dns") or []
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
                                hx-confirm="Delete {{_name}}? Any function pinned to it stops sending until it is reassigned.">Delete VLAN</button>
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
            % end
        </div>

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
        %# Its own form: the card is a div, so ``closest form`` has nothing
        %# else to find, and Create submits it rather than the browser falling
        %# through to a native GET that silently creates nothing.
        <form class="ia-vlan-add" {{'' if _vopen else 'hidden'}}
              hx-post="/section/network/vlan/create" hx-target="#network-interface"
              hx-swap="innerHTML" hx-trigger="submit">
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
                <button type="submit" class="save-btn">Create</button>
                <button type="button" class="ghost-btn"
                        onclick="var b=this.closest('.group'); b.querySelector('.ia-vlan-add').hidden = true; b.querySelector('.ia-legend-actions button').hidden = false;">Cancel</button>
            </div>
        </form>
        % end
    </div>
    % end
</div>
