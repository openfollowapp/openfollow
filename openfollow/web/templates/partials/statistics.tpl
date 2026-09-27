% import hashlib
% from openfollow.web.labels import pretty_label, video_error_token, video_signal_label
% from openfollow.web.live_alerts import DETECTION_MISSING_STEP, statistics_alerts
% system = stats.get("system", {})
% video = stats.get("video", {})
% resolution = video.get("resolution", {})
% controllers = stats.get("controllers", {})
% tracking = stats.get("tracking", {})
% playback = stats.get("playback", {})
% temp_c = system.get("temperature_c")
% tracked_people = tracking.get("tracked_people", 0)
% video_connected = bool(video.get("connected"))
% tracking_enabled = bool(tracking.get("enabled"))
% tracking_running = bool(tracking.get("running"))
% tracking_missing = tracking.get("missing_deps") or []
% # Named by what failed: "Disconnected" sends an operator nowhere.
% video_state = video_signal_label(video_connected, str(video.get("failure") or "none"))
% # Keyed off the connection, not off a falsy figure: a placeholder ("No Signal")
% # pipeline publishes no geometry and no rate, while a connected variable-rate
% # source legitimately advertises 0 fps and must not read as "not connected".
% input_w = resolution.get("width", 0)
% input_h = resolution.get("height", 0)
% input_resolution = ("%dx%d" % (input_w, input_h)) if video_connected and input_w and input_h else "N/A"
% source_fps_text = ("%.1f fps" % video.get("source_fps", 0.0)) if video_connected else "N/A"
% # The boxes carry no role: this panel is swapped every second, and the
% # announcer beside it speaks their text once per change (web/live_alerts.py).
% # Both are credential-free - the status marker redacts on the way in, and
% # this partial is exempt from the web PIN.
% alerts = statistics_alerts(stats)
% output_resolution = system.get("output_resolution")
% output_text = ("%dx%d" % (output_resolution["width"], output_resolution["height"])) if output_resolution else "N/A (no display)"
% tracking_state = "Off"
% if tracking_enabled:
%     if tracking_missing:
%         tracking_state = "Unavailable"
%     else:
%         tracking_state = "Running" if tracking_running else "Idle"
%     end
% end
% # Missing deps always force the error chip so "Running" can't render green
% # while the package probe is reporting a broken install.
% if tracking_missing and tracking_enabled:
%     tracking_chip_class = "off"
% elif tracking_running:
%     tracking_chip_class = "ok"
% elif tracking_enabled:
%     tracking_chip_class = "info"
% else:
%     tracking_chip_class = ""
% end
% frame_age = playback.get("seconds_since_last_frame")
% # The watchdog flag alone is not enough: it is set from the housekeeping
% # timeout, which is the same main loop the frame clock is on, so a block
% # inside one callback stops both. The age is measured on the web thread and
% # keeps growing, so it is what decides the chip.
% stale_after = playback.get("stale_after_s") or 1.0
% frame_stalled = bool(playback.get("stalled")) or (frame_age is not None and frame_age >= stale_after)
% if frame_stalled:
%     frame_clock_state = "Stalled %.0f s" % frame_age if frame_age is not None else "Stalled"
% elif frame_age is None:
%     frame_clock_state = "Starting"
% else:
%     frame_clock_state = "Running"
% end
% frame_clock_chip = "off" if frame_stalled else ("ok" if frame_age is not None else "info")

<div class="stats-columns">
    <section class="stat-panel">
        <div class="stat-panel-head">
            <h3 class="stat-panel-title">Video</h3>
            <span class="stat-chip {{'ok' if video_connected else 'off'}}">{{video_state}}</span>
        </div>
% if alerts.video is not None:
%     include('partials/video_error_box.tpl', failure_text=alerts.video[0], error_message='', action=alerts.video[1], token=video_error_token(alerts.video[0], '', alerts.video[1]), scope='stats', live='')
% end
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
                <dt class="metric-label">Input resolution</dt>
                <dd class="metric-value">{{input_resolution}}</dd>
            </div>
            <div class="metric-row">
                <dt class="metric-label">Frame Rate (source)</dt>
                <dd class="metric-value">{{source_fps_text}}</dd>
            </div>
            <div class="metric-row">
                <dt class="metric-label">Pipeline</dt>
                <dd class="metric-value">{{pretty_label(video.get('pipeline_state', 'disconnected'))}}</dd>
            </div>
        </dl>
    </section>

    <section class="stat-panel">
        <div class="stat-panel-head">
            <h3 class="stat-panel-title">Device</h3>
        </div>
% if alerts.controllers:
        <div class="notice error">
% for line in alerts.controllers:
            <div>{{line}}</div>
% end
        </div>
% end
        <dl class="metric-list">
            <div class="metric-row">
                <dt class="metric-label">IP</dt>
                <dd class="metric-value">{{system.get('ip', 'N/A')}}</dd>
            </div>
            <div class="metric-row">
                <dt class="metric-label">Controllers</dt>
                <dd class="metric-value">{{controllers.get('connected_count', 0)}} connected{{(' · %d missing' % controllers['missing_count']) if controllers.get('missing_count') else ''}}</dd>
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
            <div class="metric-row">
                <dt class="metric-label">Output resolution</dt>
                <dd class="metric-value">{{output_text}}</dd>
            </div>
            <div class="metric-row">
                <dt class="metric-label">Overlay redraw rate</dt>
                <dd class="metric-value">{{'%.1f fps' % system.get('hud_fps', 0.0)}}</dd>
            </div>
            <div class="metric-row">
                <dt class="metric-label">Frame clock</dt>
                <dd class="metric-value">
                    <span class="stat-chip {{frame_clock_chip}}">{{frame_clock_state}}</span>
                </dd>
            </div>
        </dl>
    </section>

    <section class="stat-panel">
        <div class="stat-panel-head">
            <h3 class="stat-panel-title">Person Detection</h3>
            <span class="stat-chip{{(" " + tracking_chip_class) if tracking_chip_class else ""}}">{{tracking_state}}</span>
        </div>
% if alerts.detection is not None:
        <div class="notice error">
            <div>{{alerts.detection}}</div>
            <div class="notice-sub">{{DETECTION_MISSING_STEP}}</div>
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
