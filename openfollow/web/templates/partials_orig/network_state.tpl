%# Read-only network interface state (refreshed on 5s HTMX poll to avoid
%# clobbering concurrent PIN input). Tolerant of missing context: renders
%# unavailable banner if network_state is undefined.
% _ns = network_state if defined('network_state') else None
% if not _ns or not _ns.get("interfaces"):
<p class="muted">Network state unavailable – no detected interface or adapter on this host.</p>
% else:
<div class="network-state-card">
    <div class="network-state-banner">
        Configure network settings from the device on-screen menu.
    </div>

    <div class="group">
        <h4 class="group-title">Connection</h4>
        <dl class="network-state-grid">
            <dt>Interface</dt>
            <dd>{{_ns.get("active_interface") or "–"}}</dd>
            <dt>Method</dt>
            <dd>{{_ns.get("method") or "–"}}</dd>
            % if _ns.get("backend"):
            <dt>Backend</dt>
            <dd>{{_ns.get("backend")}}{{' (read-only)' if not _ns.get("writable") else ''}}</dd>
            % end
        </dl>
    </div>

    <div class="group">
        <h4 class="group-title">Addressing</h4>
        <dl class="network-state-grid">
            <dt>IP address</dt>
            <dd>{{_ns.get("address") or "–"}}</dd>
            <dt>Subnet mask</dt>
            <dd>{{_ns.get("subnet_mask") or "–"}}</dd>
            <dt>Router</dt>
            <dd>{{_ns.get("router") or "–"}}</dd>
        </dl>
    </div>

    <div class="group">
        <h4 class="group-title">DNS</h4>
        % _dns = _ns.get("dns") or []
        % if _dns:
        <dl class="network-state-grid">
            % for i, server in enumerate(_dns):
            <dt>Server {{i + 1}}</dt>
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
        <h4 class="group-title">Lease</h4>
        <dl class="network-state-grid">
            <dt>Remaining</dt>
            <dd>{{_lease_label}}</dd>
        </dl>
    </div>
    % end
</div>
% end
