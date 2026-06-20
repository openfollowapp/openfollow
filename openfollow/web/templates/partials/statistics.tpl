% system = stats.get("system", {})
% video = stats.get("video", {})
% resolution = video.get("resolution", {})
% controllers = stats.get("controllers", {})
% tracking = stats.get("tracking", {})
% temp_c = system.get("temperature_c")
% tracked_people = tracking.get("tracked_people", 0)
% video_connected = bool(video.get("connected"))
% tracking_enabled = bool(tracking.get("enabled"))
% tracking_running = bool(tracking.get("running"))
% tracking_missing = tracking.get("missing_deps") or []
% video_state = "Connected" if video_connected else "Disconnected"
% tracking_state = "Off"
% if tracking_enabled:
%     if tracking_missing:
%         tracking_state = "Unavailable"
%     else:
%         tracking_state = "Running" if tracking_running else "Idle"
%     end
% end
% # Missing deps always force a warn chip so "Running" can't render green while
% # the package probe is reporting a broken install. Banner, on the other hand,
% # is only useful when the user has actually opted into detection.
% if tracking_missing and tracking_enabled:
%     tracking_chip_class = "warn"
% elif tracking_running:
%     tracking_chip_class = "ok"
% elif tracking_enabled:
%     tracking_chip_class = "warn"
% else:
%     tracking_chip_class = "off"
% end
% show_missing_banner = bool(tracking_missing) and tracking_enabled

<p class="stat-help">Live indicators for operation and troubleshooting.</p>

<div class="stats-columns">
    <section class="stat-panel">
        <div class="stat-panel-head">
            <h3 class="stat-panel-title">Video</h3>
            <span class="stat-chip {{'ok' if video_connected else 'off'}}">{{video_state}}</span>
        </div>
        <dl class="metric-list">
            <div class="metric-row">
                <dt class="metric-label">Source</dt>
                <dd class="metric-value">{{video.get('source_label') or video.get('source_type', 'N/A')}}</dd>
            </div>
            <div class="metric-row">
                <dt class="metric-label">Signal</dt>
                <dd class="metric-value">{{video_state}}</dd>
            </div>
            <div class="metric-row">
                <dt class="metric-label">Resolution</dt>
                <dd class="metric-value">{{resolution.get('width', 0)}}x{{resolution.get('height', 0)}}</dd>
            </div>
            <div class="metric-row">
                <dt class="metric-label">Frame Rate (measured)</dt>
                <dd class="metric-value">{{'%.1f fps' % video.get('fps', 0.0)}}</dd>
            </div>
            <div class="metric-row">
                <dt class="metric-label">Frame Rate (source)</dt>
                <dd class="metric-value">{{'%.1f fps' % video.get('source_fps', 0.0)}}</dd>
            </div>
            <div class="metric-row">
                <dt class="metric-label">Pipeline</dt>
                <dd class="metric-value">{{str(video.get('pipeline_state', 'disconnected')).upper()}}</dd>
            </div>
        </dl>
    </section>

    <section class="stat-panel">
        <div class="stat-panel-head">
            <h3 class="stat-panel-title">Device</h3>
        </div>
        <dl class="metric-list">
            <div class="metric-row">
                <dt class="metric-label">IP</dt>
                <dd class="metric-value">{{system.get('ip', 'N/A')}}</dd>
            </div>
            <div class="metric-row">
                <dt class="metric-label">Controllers</dt>
                <dd class="metric-value">{{controllers.get('connected_count', 0)}} connected</dd>
            </div>
            <div class="metric-row">
                <dt class="metric-label">CPU</dt>
                <dd class="metric-value">{{'%.1f %%' % system.get('cpu_percent', 0.0)}}</dd>
            </div>
            <div class="metric-row">
                <dt class="metric-label">RAM</dt>
                <dd class="metric-value">{{'%.1f %%' % system.get('ram_percent', 0.0)}}</dd>
            </div>
            <div class="metric-row">
                <dt class="metric-label">Temperature</dt>
                <dd class="metric-value">{{'%.1f C' % temp_c if temp_c is not None else 'N/A'}}</dd>
            </div>
        </dl>
    </section>

    <section class="stat-panel">
        <div class="stat-panel-head">
            <h3 class="stat-panel-title">Person Detection</h3>
            <span class="stat-chip {{tracking_chip_class}}">{{tracking_state}}</span>
        </div>
% if show_missing_banner:
        <div class="stat-warn" role="alert" style="margin: 0 0 10px; padding: 8px 10px; border-radius: 6px; border: 1px solid rgba(255, 120, 120, 0.45); background: rgba(255, 76, 76, 0.1); color: #ffd6d6; font-size: 0.85rem;">
            <strong>Missing packages:</strong> {{', '.join(tracking_missing)}}.
            Install them from the Person Detection section, then restart.
        </div>
% end
        <dl class="metric-list">
            <div class="metric-row">
                <dt class="metric-label">Status</dt>
                <dd class="metric-value">{{tracking_state}}</dd>
            </div>
            <div class="metric-row">
                <dt class="metric-label">Tracked People</dt>
                <dd class="metric-value">{{tracked_people}}</dd>
            </div>
            <div class="metric-row">
                <dt class="metric-label">Inference (avg)</dt>
                <dd class="metric-value">{{'%.1f ms' % tracking.get('inference_avg_ms', 0.0)}}</dd>
            </div>
            <div class="metric-row">
                <dt class="metric-label">Inference Rate</dt>
                <dd class="metric-value">{{'%.2f Hz' % tracking.get('inference_hz', 0.0)}}</dd>
            </div>
            <div class="metric-row">
                <dt class="metric-label">Detections (last)</dt>
                <dd class="metric-value">{{tracking.get('detections_last', 0)}}</dd>
            </div>
        </dl>
    </section>
</div>
