% from openfollow.units import UnitSystem, format_length, metric_echo, unit_suffix_length
% _us = UnitSystem(config.ui.unit_system)
% _imp = _us is UnitSystem.IMPERIAL
% _len = unit_suffix_length(_us)
<form id="trigger-zones-section" class="section {{'saved' if defined('saved') and saved else ''}}" data-fold-key="trigger_zones" data-help="trigger_zones"
      data-template-form="1"
      hx-post="/section/trigger_zones" hx-target="#trigger-zones-section" hx-swap="outerHTML" hx-trigger="submit">
    <div class="section-head">
        <h2>Trigger Zones</h2>
        <span class="section-note">Fire OSC messages when markers or detections enter/leave polygon regions</span>
    </div>

    <div class="group">
        <h3 class="group-title">Global</h3>
        <div class="row">
            <div class="field checkbox-field">
                <label>Enabled</label>
                <div class="checkbox-wrap"><input type="checkbox" name="enabled" {{'checked' if config.trigger_zones.enabled else ''}}></div>
            </div>
            <div class="field checkbox-field">
                <label>Show Overlay</label>
                <div class="checkbox-wrap"><input type="checkbox" name="show_overlay" {{'checked' if config.trigger_zones.show_overlay else ''}}></div>
            </div>
            <div class="field">
                <label>Eval Rate (FPS)</label>
                <select name="eval_fps">
                    <option value="1" {{'selected' if config.trigger_zones.eval_fps == 1 else ''}}>1 FPS</option>
                    <option value="5" {{'selected' if config.trigger_zones.eval_fps == 5 else ''}}>5 FPS</option>
                    <option value="10" {{'selected' if config.trigger_zones.eval_fps == 10 else ''}}>10 FPS</option>
                    <option value="15" {{'selected' if config.trigger_zones.eval_fps == 15 else ''}}>15 FPS</option>
                    <option value="30" {{'selected' if config.trigger_zones.eval_fps == 30 else ''}}>30 FPS</option>
                    <option value="60" {{'selected' if config.trigger_zones.eval_fps == 60 else ''}}>60 FPS</option>
                </select>
            </div>
            <div class="field">
                <label>Debounce (ms)</label>
                <input id="trigger-zones-debounce-ms" type="number" name="debounce_ms" value="{{config.trigger_zones.debounce_ms}}" min="0" max="60000" step="10"
                       hx-get="/api/validate/trigger_zones/debounce_ms" hx-trigger="blur changed delay:200ms"
                       hx-target="#trigger-zones-debounce-ms-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="trigger-zones-debounce-ms-error" aria-invalid="false">
                <span id="trigger-zones-debounce-ms-error" class="field-error"></span>
            </div>
            <div class="field">
                <label>Hysteresis ({{_len}})</label>
                <input id="trigger-zones-hysteresis" type="{{'text' if _imp else 'number'}}" name="hysteresis" value="{{format_length(config.trigger_zones.hysteresis, _us) if _imp else config.trigger_zones.hysteresis}}" min="0" max="10" step="0.01"
                       hx-get="/api/validate/trigger_zones/hysteresis" hx-trigger="blur changed delay:200ms"
                       hx-target="#trigger-zones-hysteresis-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="trigger-zones-hysteresis-error" aria-invalid="false">
                <span id="trigger-zones-hysteresis-error" class="field-error"></span>
                % if _imp:
                <small class="metric-echo">Stored: {{metric_echo(config.trigger_zones.hysteresis)}}</small>
                % end
            </div>
        </div>
        <div class="row">
            <div class="field">
                <label>Default OSC Host</label>
                <input id="trigger-zones-default-osc-host" type="text" name="default_osc_host" value="{{config.trigger_zones.default_osc_host}}" placeholder="127.0.0.1"
                       hx-get="/api/validate/trigger_zones/default_osc_host" hx-trigger="blur changed delay:200ms"
                       hx-target="#trigger-zones-default-osc-host-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="trigger-zones-default-osc-host-error" aria-invalid="false">
                <span id="trigger-zones-default-osc-host-error" class="field-error"></span>
            </div>
            <div class="field">
                <label>Default OSC Port</label>
                <input id="trigger-zones-default-osc-port" type="number" name="default_osc_port" value="{{config.trigger_zones.default_osc_port}}" min="1" max="65535" step="1"
                       hx-get="/api/validate/trigger_zones/default_osc_port" hx-trigger="blur changed delay:200ms"
                       hx-target="#trigger-zones-default-osc-port-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="trigger-zones-default-osc-port-error" aria-invalid="false">
                <span id="trigger-zones-default-osc-port-error" class="field-error"></span>
            </div>
        </div>
    </div>

    <div class="actions">
        <button type="submit" class="save-btn">Save</button>
    </div>
</form>
