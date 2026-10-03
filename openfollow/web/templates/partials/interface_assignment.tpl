%# Network Interface Assignment – which network each function uses.
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
        <h2>Network Interface Assignment</h2>
    </div>

    %# Poll until the server answers again, then reload. Unlike every other
    %# restart notice this one may come back at a DIFFERENT address, so it
    %# names the one to try when the reload cannot reach this one.
    % if defined('restarting') and restarting:
    <div class="restart-notice"
         hx-get="/api/info"
         hx-trigger="every 2s"
         hx-swap="none"
         hx-on::after-request="if(event.detail.successful) window.location.reload()">App is restarting&hellip;
        If this page does not come back, the web UI has moved - the station's
        Network screen lists the address that reaches it.</div>
    % end
    % if defined('restart_refused') and restart_refused:
    <div class="notice error" role="alert">An update is running, so OpenFollow was not restarted.
        <div class="notice-sub">Restart it once the update has finished.</div></div>
    % end

    %# The web UI is the surface this panel is edited from, so a pin that
    %# misses is a lockout rather than a silent plane. Both notices name the
    %# way back: the URL that will work, and the on-screen escape.
    % _wadv = web_bind_advisory if defined('web_bind_advisory') and web_bind_advisory else {}
    % if _wadv.get('banner'):
    <div class="notice warning" role="status">{{_wadv['banner']}}</div>
    % end
    % _wnotice = web_bind_notice if defined('web_bind_notice') else ''
    % if _wnotice:
    <div class="notice warning" role="status">{{_wnotice}}</div>
    % end

    <table class="data-table ia-assign">
        <thead>
            <tr><th>Function</th><th>Interface</th><th>Address</th></tr>
        </thead>
        <tbody>
            % for row in rows:
            <tr class="{{'ia-readonly' if not row['editable'] else ''}} {{'experimental-feature' if row.get('experimental') else ''}}">
                <th scope="row">{{row['label']}}</th>
                % if row['editable']:
                <td>
                    %# Interface names are stable across DHCP renewals; the
                    %# option list (and its blank-option wording) comes from the
                    %# shared route so every picker stays consistent.
                    %#
                    %# ``current`` is the saved value only on load. A later reload
                    %# (``refreshIfacePickers``) sends what the picker shows, so an
                    %# unsaved choice survives it.
                    % _options_url = f"/network/interfaces/by_name?blank={row['blank']}"
                    <select name="{{row['key']}}" aria-label="{{row['label']}} interface"
                            data-options-url="{{_options_url}}"
                            hx-get="{{_options_url}}&current={{row['value']}}"
                            hx-trigger="load"
                            hx-target="this" hx-swap="innerHTML">
                        <option value="{{row['value']}}">{{row['value'] or '-- Loading... --'}}</option>
                    </select>
                </td>
                % else:
                <td class="muted">{{row.get('note', '')}}</td>
                % end
                <td class="ia-addr {{'muted' if not row['editable'] else ''}}">
                    % include('partials/interface_assignment_address.tpl', row=row, oob=False)
                </td>
            </tr>
            % end
        </tbody>
    </table>

    %# Keeps the Address column current without touching the pickers, and has
    %# them reload their options once the interface list reads differently.
    % if not (defined('restarting') and restarting):
    <span id="ia-options-fp" data-fp="{{options_fingerprint}}" hidden></span>
    <div hidden hx-get="/section/interface_assignment/status" hx-trigger="every 5s"
         hx-vals='js:{seen: document.getElementById("ia-options-fp").dataset.fp}'
         hx-target="this" hx-swap="none"></div>
    % end

    <div class="actions">
        <button type="submit" class="save-btn">Save</button>
        %# Pinning the web UI moves the listening socket, which the running
        %# server cannot do under itself while serving this request.
        % if defined('web_bind_restart') and web_bind_restart:
        <button type="button" class="danger"
                hx-post="/section/interface_assignment/restart"
                hx-confirm="Outputs pause until OpenFollow is back. The web UI may then answer at a different address."
                data-confirm-title="Restart OpenFollow?" data-confirm-label="Restart" data-confirm-danger>Restart OpenFollow</button>
        % end
        <button type="button" id="refresh-iface-assignment" class="secondary"
                hx-get="/section/interface_assignment"
                hx-target="#interface-assignment-section" hx-swap="outerHTML">Scan</button>
    </div>
</form>
