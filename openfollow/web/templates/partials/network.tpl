%# Network Interface configuration.
%#
%# The card opens on the list of every interface on the station – the layout
%# view the old single ``Interface`` picker had nowhere to show. ``Configure``
%# expands that interface's settings under its own row, so which adapter is
%# being edited is never ambiguous.
%#
%# Two modes, as before: VIEW (editable=false) disables the fields and
%# live-polls every 5s; EDIT adds Apply / Renew / Cancel. The poll carries the
%# expanded interface in its path – dropping it would re-render the card on the
%# active interface every 5s and collapse the row the operator is reading.
%#
%# Rendered into the #network-interface region (heading + fold live in
%# general.tpl), swapped in place on toggle / apply / renew.
%#
%# Tolerant of missing context: callers without ``net`` get the unavailable
%# state instead of a NameError.
% _net = net if defined('net') else {"available": False, "writable": False, "editable": False}
% _writable = _net.get("writable")
% _editable = bool(_net.get("editable") and _writable)
% _method = _net.get("method", "dhcp")
% _dis = '' if _editable else 'disabled'
% _banner = _net.get("banner")
% _rows = _net.get("iface_rows", [])
% _session = _net.get("session_iface", "")
% _editing = _net.get("editing_iface", "")
% _polls = _net.get("available") and not _editable
%# Creating a link is an Edit-mode action on a backend that owns links; on a
%# read-only or non-NetworkManager stack the card renders exactly as before.
% _vlan_parents = _net.get("vlan_parents", [])
% _vlan_add = bool(_editable and _net.get("supports_vlans") and _vlan_parents)
<form id="network-config-section" class="network-config"
% if _editable:
      hx-post="/section/network/apply" hx-target="#network-interface"
      hx-swap="innerHTML" hx-trigger="submit"
      hx-on:submit="netScheduleReload(this)"
% elif _polls:
      hx-get="/section/network/status{{'/' + _editing if _editing else ''}}" hx-trigger="every 5s"
      hx-target="#network-interface" hx-swap="innerHTML"
% end
      >
    % if _banner:
    <div class="network-banner network-banner-{{_banner.get('kind', 'info')}}">{{_banner.get('text', '')}}</div>
    % end

    % if not _net.get("available"):
    <p class="muted">Network configuration is unavailable – no network adapter is configured on this host (or this build has none wired), so there is nothing to edit.</p>
    % else:

    %# Mode bar: names the current mode at the top of the form. The view
    %# switch is a text-link, not a button, so it reads as a mode toggle
    %# rather than a Save action.
    % if _editable:
    <div class="net-mode-bar edit">
        <span class="net-mode-pill edit">Edit mode</span>
        <span class="net-mode-text">⚠ Applying network changes may disconnect this web session. A static / manual address reloads the UI at the new address automatically; otherwise reconnect manually.</span>
    </div>
    % elif _writable:
    <div class="net-mode-bar view">
        <span class="net-mode-pill view">View mode</span>
        <span class="net-mode-text">Network settings are protected from change by mistake.</span>
        <button type="button" class="secondary small net-mode-switch"
                hx-get="/section/network/edit" hx-target="#network-interface"
                hx-swap="innerHTML">Switch to edit view</button>
    </div>
    % else:
    <div class="net-mode-bar readonly">
        <span class="net-mode-pill readonly">Read only</span>
        <span class="net-mode-text">Network settings can't be changed from the web on this station. Use the on-screen Settings menu, or see openfollow.app for troubleshooting and how to enable web editing.</span>
    </div>
    % end

    %# The interface being configured still travels with the form so /apply
    %# and /renew receive it exactly as before – it just comes from the row
    %# the operator opened rather than from a dropdown.
    <input type="hidden" name="iface" value="{{_net.get('active_interface', '')}}">

    <div class="group">
        <h4 class="group-title">Interfaces on this station</h4>
        % if not _rows:
        <p class="muted">No network interfaces detected.</p>
        % else:
        <table class="ia-table ia-nics">
            <thead>
                <tr><th></th><th>Interface</th><th>Address</th><th>Method</th><th></th></tr>
            </thead>
            <tbody>
                % for row in _rows:
                % _name = row.get("name", "")
                % _addr = row.get("address", "")
                % _prefix = row.get("prefix")
                % _open = bool(_name) and _name == _editing
                <tr class="{{'is-configuring' if _open else ''}}">
                    <td><span class="ia-dot {{'up' if _addr else 'down'}}"></span></td>
                    <td>
                        <code>{{_name}}</code>
                        % if _name and _name == _session:
                        %# Guards the operator against editing the adapter
                        %# their own session is arriving on.
                        <span class="ia-badge session" title="Your browser reached this station over this interface">This session</span>
                        % end
                        % if row.get("vlan_id") is not None:
                        <span class="ia-badge vlan">VLAN {{row.get("vlan_id")}}</span>
                        % end
                    </td>
                    <td class="{{'' if _addr else 'muted'}}">{{(_addr + ('/' + str(_prefix) if _prefix else '')) if _addr else '(no address)'}}</td>
                    <td class="muted">{{row.get('method_label', '')}}</td>
                    <td class="ia-actions">
                        %# The open row is the one being configured, so it
                        %# needs no button – picking another row moves the
                        %# expansion there.
                        % if not _open:
                        <button type="button" class="secondary small"
                                hx-get="{{('/section/network/edit/' if _editable else '/section/network/status/') + _name}}"
                                hx-target="#network-interface" hx-swap="innerHTML">Configure</button>
                        % end
                    </td>
                </tr>

                % if _open:
                <tr class="ia-editor-row"><td colspan="5"><div class="ia-editor">
                    <div class="ia-editor-head">
                        <h4 class="group-title">Configure <code>{{_name}}</code>
                            % if row.get("vlan_id") is not None:
                            <span class="ia-badge vlan">VLAN {{row.get("vlan_id")}}</span>
                            % end
                        </h4>
                    </div>

                    % if _name == _session:
                    <div class="notice warning" role="status">
                        <strong>You are connected over this interface.</strong>
                        Changing its address will drop this web session. The station stays
                        reachable by name and from the on-screen <em>Settings &rsaquo; Network</em> menu.
                    </div>
                    % end

                    <div class="network-grid">
                        <label for="net-method">Method</label>
                        <select id="net-method" name="method" {{_dis}}
                                % if _editable:
                                hx-post="/section/network" hx-target="#network-config-section"
                                hx-swap="outerHTML" hx-trigger="change" hx-include="closest form"
                                % end
                                >
                            <option value="dhcp" {{'selected' if _method == 'dhcp' else ''}}>DHCP (automatic)</option>
                            <option value="dhcp_manual" {{'selected' if _method == 'dhcp_manual' else ''}}>DHCP with manual address</option>
                            <option value="static" {{'selected' if _method == 'static' else ''}}>Static</option>
                        </select>
                    </div>

                    %# Addressing. Read-only view shows current IP/subnet/router
                    %# (including the DHCP lease address); the edit form shows
                    %# only the fields the chosen method lets you set.
                    % if (not _editable) or _method in ("static", "dhcp_manual"):
                    <div class="group">
                        <h4 class="group-title">Addressing</h4>
                        <div class="network-grid">
                            <label for="net-address">IP address</label>
                            <input id="net-address" type="text" name="address" value="{{_net.get('address', '')}}"
                                   placeholder="192.168.1.50" {{_dis}}>
                            % if (not _editable) or _method == "static":
                            <label for="net-subnet">Subnet mask</label>
                            <input id="net-subnet" type="text" name="subnet_mask"
                                   value="{{_net.get('subnet_mask', '')}}"
                                   placeholder="255.255.255.0" {{_dis}}>
                            <label for="net-router">Router (optional)</label>
                            <input id="net-router" type="text" name="router" value="{{_net.get('router', '')}}"
                                   placeholder="192.168.1.1" {{_dis}}>
                            % end
                        </div>
                    </div>
                    % end

                    <div class="group">
                        <h4 class="group-title">DNS</h4>
                        <div class="network-grid">
                            % _dns = _net.get("dns", [])
                            % for i in range(3):
                            <label for="net-dns{{i + 1}}">Server {{i + 1}}</label>
                            <input id="net-dns{{i + 1}}" type="text" name="dns{{i + 1}}"
                                   value="{{_dns[i] if i < len(_dns) else ''}}"
                                   placeholder="1.1.1.1" {{_dis}}>
                            % end
                        </div>
                    </div>

                    % _lease = _net.get("lease_display")
                    % if _lease:
                    <div class="group">
                        <h4 class="group-title">Lease</h4>
                        <div class="network-grid">
                            <label>Remaining</label>
                            <span class="network-grid-value">{{_lease}}</span>
                        </div>
                    </div>
                    % end

                    % if _editable:
                    <div class="actions">
                        <button type="submit" class="save-btn">Apply</button>
                        %# Renew only for DHCP; static has no lease.
                        % if _method in ("dhcp", "dhcp_manual"):
                        <button type="button" class="secondary"
                                hx-post="/section/network/renew" hx-target="#network-interface"
                                hx-swap="innerHTML" hx-include="closest form">Renew DHCP lease</button>
                        % end
                        %# Delete lives inside the open editor, so it can only
                        %# ever be aimed at the interface named in the header.
                        % if row.get("vlan_id") is not None:
                        <button type="button" class="danger"
                                hx-post="/section/network/vlan/delete" hx-target="#network-interface"
                                hx-swap="innerHTML" hx-include="closest form"
                                hx-confirm="Delete {{_name}}? Any function pinned to it stops sending until it is reassigned.">Delete VLAN</button>
                        % end
                        %# Back to View mode on the same row. Targeting /edit
                        %# would re-render the editor, leaving no way out of
                        %# Edit mode short of a page reload.
                        <button type="button" class="ghost-btn"
                                hx-get="/section/network/status{{'/' + _editing if _editing else ''}}"
                                hx-target="#network-interface"
                                hx-swap="innerHTML">Cancel</button>
                    </div>
                    % end
                </div></td></tr>
                % end
                % end
            </tbody>
        </table>

        <div class="ia-legend">
            <span><span class="ia-dot up"></span> up with an address</span>
            <span><span class="ia-dot down"></span> no address</span>
            <span class="ia-legend-actions">
                % if _vlan_add:
                <button type="button" class="secondary small"
                        onclick="this.closest('.group').querySelector('.ia-vlan-add').hidden = false; this.hidden = true;">+ Add VLAN</button>
                % end
                %# Keeps the open interface in the path so re-reading the
                %# adapter list doesn't move the expansion (and, in Edit mode,
                %# doesn't discard what the operator has typed).
                <button type="button" class="secondary small"
                        hx-get="{{'/section/network/edit' if _editable else '/section/network/status'}}{{'/' + _editing if _editing else ''}}?scan=1"
                        hx-target="#network-interface" hx-swap="innerHTML">Scan</button>
            </span>
        </div>

        % if _vlan_add:
        <div class="ia-vlan-add" hidden>
            <h4 class="group-title">Add VLAN</h4>
            <div class="network-grid">
                <label for="net-vlan-parent">Parent interface</label>
                <select id="net-vlan-parent" name="vlan_parent">
                    % for _parent in _vlan_parents:
                    <option value="{{_parent}}">{{_parent}}</option>
                    % end
                </select>

                <label for="net-vlan-id">VLAN ID</label>
                <input type="number" id="net-vlan-id" name="vlan_id" min="1" max="4094" step="1" placeholder="10">
            </div>
            <div class="actions">
                <button type="button" class="save-btn"
                        hx-post="/section/network/vlan/create" hx-target="#network-interface"
                        hx-swap="innerHTML" hx-include="closest form">Create</button>
                <button type="button" class="ghost-btn"
                        onclick="var b=this.closest('.group'); b.querySelector('.ia-vlan-add').hidden = true; b.querySelector('.ia-legend-actions button').hidden = false;">Cancel</button>
            </div>
        </div>
        % end
        % end
    </div>
    % end
</form>
