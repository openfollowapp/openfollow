%# Network Interface configuration. One layout, two modes (read-only and
%# editable) both use compact label:value grid. VIEW (editable=false): disabled
%# fields, top "Switch to edit view" mode bar, live-polls every 5s.
%#   * EDIT (``editable`` true): the same fields are enabled, with
%#     Apply / Renew / Cancel.
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
<form id="network-config-section" class="network-config"
% if _editable:
      hx-post="/section/network/apply" hx-target="#network-interface"
      hx-swap="innerHTML" hx-trigger="submit"
      hx-on:submit="netScheduleReload(this)"
% elif _net.get("available"):
      hx-get="/section/network/status" hx-trigger="every 5s"
      hx-target="#network-interface" hx-swap="innerHTML"
% end
      >
    % if _banner:
    <div class="network-banner network-banner-{{_banner.get('kind', 'info')}}">{{_banner.get('text', '')}}</div>
    % end

    % if not _net.get("available"):
    <p class="muted">{{_('Network configuration is unavailable – no network adapter is configured on this host (or this build has none wired), so there is nothing to edit.')}}</p>
    % else:

    %# Mode bar: names the current mode at the top of the form. The view
    %# switch is a text-link, not a button, so it reads as a mode toggle
    %# rather than a Save action.
    % if _editable:
    <div class="net-mode-bar edit">
        <span class="net-mode-pill edit">{{_('Edit mode')}}</span>
        <span class="net-mode-text">{{_('⚠ Applying network changes may disconnect this web session. A static / manual address reloads the UI at the new address automatically; otherwise reconnect manually.')}}</span>
    </div>
    % elif _writable:
    <div class="net-mode-bar view">
        <span class="net-mode-pill view">{{_('View mode')}}</span>
        <span class="net-mode-text">{{_('Network settings are protected from change by mistake.')}}</span>
        <button type="button" class="secondary small net-mode-switch"
                hx-get="/section/network/edit" hx-target="#network-interface"
                hx-swap="innerHTML">{{_('Switch to edit view')}}</button>
    </div>
    % else:
    <div class="net-mode-bar readonly">
        <span class="net-mode-pill readonly">{{_('Read only')}}</span>
        <span class="net-mode-text">{{_("Network settings can't be changed from the web on this station. Use the on-screen Settings menu, or see openfollow.app for troubleshooting and how to enable web editing.")}}</span>
    </div>
    % end

    <div class="group">
        <h4 class="group-title">{{_('Connection')}}</h4>
        <div class="network-grid">
            <label for="net-iface">{{_('Interface')}}</label>
            <select id="net-iface" name="iface" {{_dis}}
                    % if _editable:
                    hx-post="/section/network" hx-target="#network-config-section"
                    hx-swap="outerHTML" hx-trigger="change" hx-include="closest form"
                    % end
                    >
                % for name in _net.get("interfaces", []):
                <option value="{{name}}" {{'selected' if name == _net.get('active_interface') else ''}}>{{name}}</option>
                % end
            </select>
            <label for="net-method">{{_('Method')}}</label>
            <select id="net-method" name="method" {{_dis}}
                    % if _editable:
                    hx-post="/section/network" hx-target="#network-config-section"
                    hx-swap="outerHTML" hx-trigger="change" hx-include="closest form"
                    % end
                    >
                <option value="dhcp" {{'selected' if _method == 'dhcp' else ''}}>{{_('DHCP (automatic)')}}</option>
                <option value="dhcp_manual" {{'selected' if _method == 'dhcp_manual' else ''}}>{{_('DHCP with manual address')}}</option>
                <option value="static" {{'selected' if _method == 'static' else ''}}>{{_('Static')}}</option>
            </select>
        </div>
    </div>

    %# Addressing. Read-only view shows current IP/subnet/router (including DHCP
    %# lease address). EDIT form shows only operator-settable fields per method.
    % if (not _editable) or _method in ("static", "dhcp_manual"):
    <div class="group">
        <h4 class="group-title">{{_('Addressing')}}</h4>
        <div class="network-grid">
            <label for="net-address">{{_('IP address')}}</label>
            <input id="net-address" type="text" name="address" value="{{_net.get('address', '')}}"
                   placeholder="192.168.1.50" {{_dis}}>
            % if (not _editable) or _method == "static":
            <label for="net-subnet">{{_('Subnet mask')}}</label>
            <input id="net-subnet" type="text" name="subnet_mask"
                   value="{{_net.get('subnet_mask', '')}}"
                   placeholder="255.255.255.0" {{_dis}}>
            <label for="net-router">{{_('Router (optional)')}}</label>
            <input id="net-router" type="text" name="router" value="{{_net.get('router', '')}}"
                   placeholder="192.168.1.1" {{_dis}}>
            % end
        </div>
    </div>
    % end

    <div class="group">
        <h4 class="group-title">{{_('DNS')}}</h4>
        <div class="network-grid">
            % _dns = _net.get("dns", [])
            % for i in range(3):
            <label for="net-dns{{i + 1}}">{{_('Server')}} {{i + 1}}</label>
            <input id="net-dns{{i + 1}}" type="text" name="dns{{i + 1}}"
                   value="{{_dns[i] if i < len(_dns) else ''}}"
                   placeholder="1.1.1.1" {{_dis}}>
            % end
        </div>
    </div>

    % _lease = _net.get("lease_display")
    % if _lease:
    <div class="group">
        <h4 class="group-title">{{_('Lease')}}</h4>
        <div class="network-grid">
            <label>{{_('Remaining')}}</label>
            <span class="network-grid-value">{{_lease}}</span>
        </div>
    </div>
    % end

    % if _editable:
    <div class="actions">
        <button type="submit" class="save-btn">{{_('Apply')}}</button>
        %# Renew only for DHCP; static has no lease.
        % if _method in ("dhcp", "dhcp_manual"):
        <button type="button" class="secondary"
                hx-post="/section/network/renew" hx-target="#network-interface"
                hx-swap="innerHTML" hx-include="closest form">{{_('Renew DHCP lease')}}</button>
        % end
        <button type="button" class="ghost-btn"
                hx-get="/section/network/status" hx-target="#network-interface"
                hx-swap="innerHTML">{{_('Cancel')}}</button>
    </div>
    % end
    % end
</form>
