% # Live diagnostics cards fragment – see partials/diagnostics.tpl for why only
% # this region polls. Threaded context:
% # - ``cards``: dict of card values (see partials/diagnostics.tpl header)
% # - ``log_unavailable_warning``: str (empty when journalctl works)
% if log_unavailable_warning:
<div class="notice warning" role="alert">⚠ {{log_unavailable_warning}}</div>
% end

<div id="diagnostics-cards" class="stats-columns">
    <div class="stat-panel">
        <div class="stat-panel-head">
            <h3 class="stat-panel-title">Web server</h3>
            % port_chip = 'ok' if cards['web_port_match'] else 'warn'
            <span class="stat-chip {{port_chip}}">{{'OK' if cards['web_port_match'] else 'fallback'}}</span>
        </div>
        <dl class="metric-list">
            <div class="metric-row">
                <dt class="metric-label">Configured</dt>
                <dd class="metric-value">{{cards['web_port_configured']}}</dd>
            </div>
            <div class="metric-row">
                <dt class="metric-label">Serving on</dt>
                <dd class="metric-value">{{cards['web_port_display']}}</dd>
            </div>
            <div class="metric-row">
                <dt class="metric-label">Uptime</dt>
                <dd class="metric-value">{{cards['uptime_human']}}</dd>
            </div>
        </dl>
    </div>

    <div class="stat-panel">
        <div class="stat-panel-head">
            <h3 class="stat-panel-title">Beacon sender</h3>
            <span class="stat-chip {{cards['sender_chip']}}">{{cards['sender_status']}}</span>
        </div>
        <dl class="metric-list">
            <div class="metric-row">
                <dt class="metric-label">Last sent</dt>
                <dd class="metric-value">{{cards['sender_last_send']}}</dd>
            </div>
            <div class="metric-row">
                <dt class="metric-label">Errors</dt>
                <dd class="metric-value">{{cards['sender_errors']}}</dd>
            </div>
            <div class="metric-row">
                <dt class="metric-label">Sent count</dt>
                <dd class="metric-value">{{cards['sender_send_count']}}</dd>
            </div>
        </dl>
    </div>

    <div class="stat-panel">
        <div class="stat-panel-head">
            <h3 class="stat-panel-title">Beacon receiver</h3>
            <span class="stat-chip {{cards['receiver_chip']}}">{{cards['receiver_status']}}</span>
        </div>
        <dl class="metric-list">
            <div class="metric-row">
                <dt class="metric-label">Peers seen</dt>
                <dd class="metric-value">{{cards['peer_count']}}</dd>
            </div>
            <div class="metric-row">
                <dt class="metric-label">Last packet</dt>
                <dd class="metric-value">{{cards['receiver_last_recv']}}</dd>
            </div>
            <div class="metric-row">
                <dt class="metric-label">Packets total</dt>
                <dd class="metric-value">{{cards['receiver_packet_count']}}</dd>
            </div>
        </dl>
    </div>

    <div class="stat-panel">
        <div class="stat-panel-head">
            <h3 class="stat-panel-title">Logs</h3>
            <span class="stat-chip {{cards['log_chip']}}">{{cards['log_source_label']}}</span>
        </div>
        <p class="stat-help">{{cards['log_source_note']}}</p>
    </div>
</div>
