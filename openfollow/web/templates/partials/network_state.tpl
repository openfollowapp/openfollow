%# Read-only network interface state (refreshed on 5s HTMX poll to avoid
%# clobbering concurrent PIN input). Tolerant of missing context: renders
%# unavailable banner if network_state is undefined.
% _ns = network_state if defined('network_state') else None
% if not _ns or not _ns.get("interfaces"):
<p class="muted">{{_('Network state unavailable – no detected interface or adapter on this host.')}}</p>
% else:
<div class="network-state-card">
    <div class="network-state-banner">
        {{_('Configure network settings from the device on-screen menu.')}}
    </div>

    <div class="group">
        <h4 class="group-title">{{_('Connection')}}</h4>
        <dl class="network-state-grid">
            <dt>{{_('Interface')}}</dt>
            <dd>{{_ns.get("active_interface") or "–"}}</dd>
            <dt>{{_('Method')}}</dt>
            <dd>{{_ns.get("method") or "–"}}</dd>
            % if _ns.get("backend"):
            <dt>{{_('Backend')}}</dt>
            <dd>{{_ns.get("backend")}}{{_(' (read-only)') if not _ns.get("writable") else ''}}</dd>
            % end
        </dl>
    </div>

    <div class="group">
        <h4 class="group-title">{{_('Addressing')}}</h4>
        <dl class="network-state-grid">
            <dt>{{_('IP address')}}</dt>
            <dd>{{_ns.get("address") or "–"}}</dd>
            <dt>{{_('Subnet mask')}}</dt>
            <dd>{{_ns.get("subnet_mask") or "–"}}</dd>
            <dt>{{_('Router')}}</dt>
            <dd>{{_ns.get("router") or "–"}}</dd>
        </dl>
    </div>

    <div class="group">
        <h4 class="group-title">{{_('DNS')}}</h4>
        % _dns = _ns.get("dns") or []
        % if _dns:
        <dl class="network-state-grid">
            % for i, server in enumerate(_dns):
            <dt>{{_('Server')}} {{i + 1}}</dt>
            <dd>{{server}}</dd>
            % end
        </dl>
        % else:
        <p class="muted">–</p>
        % end
    </div>

    % _lease_label = _ns.get("lease_remaining_display")
    % if _lease_label:
    <div class="group">
        <h4 class="group-title">{{_('Lease')}}</h4>
        <dl class="network-state-grid">
            <dt>{{_('Remaining')}}</dt>
            <dd>{{_lease_label}}</dd>
        </dl>
    </div>
    % end
</div>
% end
