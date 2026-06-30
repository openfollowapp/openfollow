% from openfollow.units import UnitSystem, format_length, format_speed, metric_echo, metric_echo_speed, unit_suffix_length, unit_suffix_speed
% _us = UnitSystem(config.ui.unit_system)
% _imp = _us is UnitSystem.IMPERIAL
% _len = unit_suffix_length(_us)
% _spd = unit_suffix_speed(_us)
<form id="movement-section" class="section {{'saved' if defined('saved') and saved else ''}}" data-fold-key="movement" data-help="movement"
      hx-post="/section/movement" hx-target="#movement-section" hx-swap="outerHTML" hx-trigger="submit">
    <div class="section-head">
        <h2>Marker Movement</h2>
        <span class="section-note">Marker speed limits, default speed, and default position</span>
    </div>

    <div class="group">
        <div class="row">
            <div class="field">
                <label>Min Speed ({{_spd}})</label>
                <input id="movement-min-speed" type="{{'text' if _imp else 'number'}}" name="min_speed" value="{{format_speed(config.marker.min_speed, _us) if _imp else config.marker.min_speed}}" min="0" step="any"
                       hx-get="/api/validate/movement/min_speed" hx-trigger="blur changed delay:200ms"
                       hx-target="#movement-min-speed-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="movement-min-speed-error" aria-invalid="false">
                <span id="movement-min-speed-error" class="field-error"></span>
                % if _imp:
                <small class="metric-echo">Stored: {{metric_echo_speed(config.marker.min_speed)}}</small>
                % end
            </div>
            <div class="field">
                <label>Default Speed ({{_spd}})</label>
                <input id="movement-move-speed" type="{{'text' if _imp else 'number'}}" name="move_speed" value="{{format_speed(config.marker.move_speed, _us) if _imp else config.marker.move_speed}}"
                       min="{{config.marker.min_speed}}" max="{{config.marker.max_speed}}" step="any"
                       hx-get="/api/validate/movement/move_speed" hx-trigger="blur changed delay:200ms"
                       hx-target="#movement-move-speed-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="movement-move-speed-error" aria-invalid="false">
                <span id="movement-move-speed-error" class="field-error"></span>
                % if _imp:
                <small class="metric-echo">Stored: {{metric_echo_speed(config.marker.move_speed)}}</small>
                % end
            </div>
            <div class="field">
                <label>Max Speed ({{_spd}})</label>
                <input id="movement-max-speed" type="{{'text' if _imp else 'number'}}" name="max_speed" value="{{format_speed(config.marker.max_speed, _us) if _imp else config.marker.max_speed}}" min="0" step="any"
                       hx-get="/api/validate/movement/max_speed" hx-trigger="blur changed delay:200ms"
                       hx-target="#movement-max-speed-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="movement-max-speed-error" aria-invalid="false">
                <span id="movement-max-speed-error" class="field-error"></span>
                % if _imp:
                <small class="metric-echo">Stored: {{metric_echo_speed(config.marker.max_speed)}}</small>
                % end
            </div>
        </div>
        <div class="row">
            <div class="field">
                <label>Default X ({{_len}}, on reset)</label>
                <input id="movement-default-pos-x" type="{{'text' if _imp else 'number'}}" name="default_pos_x" value="{{format_length(config.marker.default_pos_x, _us) if _imp else config.marker.default_pos_x}}" step="any"
                       hx-get="/api/validate/movement/default_pos_x" hx-trigger="blur changed delay:200ms"
                       hx-target="#movement-default-pos-x-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="movement-default-pos-x-error" aria-invalid="false">
                <span id="movement-default-pos-x-error" class="field-error"></span>
                % if _imp:
                <small class="metric-echo">Stored: {{metric_echo(config.marker.default_pos_x)}}</small>
                % end
            </div>
            <div class="field">
                <label>Default Y ({{_len}}, on reset)</label>
                <input id="movement-default-pos-y" type="{{'text' if _imp else 'number'}}" name="default_pos_y" value="{{format_length(config.marker.default_pos_y, _us) if _imp else config.marker.default_pos_y}}" step="any"
                       hx-get="/api/validate/movement/default_pos_y" hx-trigger="blur changed delay:200ms"
                       hx-target="#movement-default-pos-y-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="movement-default-pos-y-error" aria-invalid="false">
                <span id="movement-default-pos-y-error" class="field-error"></span>
                % if _imp:
                <small class="metric-echo">Stored: {{metric_echo(config.marker.default_pos_y)}}</small>
                % end
            </div>
            <div class="field">
                <label>Default Z ({{_len}}, on reset)</label>
                <input id="movement-default-pos-z" type="{{'text' if _imp else 'number'}}" name="default_pos_z" value="{{format_length(config.marker.default_pos_z, _us) if _imp else config.marker.default_pos_z}}" step="any"
                       hx-get="/api/validate/movement/default_pos_z" hx-trigger="blur changed delay:200ms"
                       hx-target="#movement-default-pos-z-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="movement-default-pos-z-error" aria-invalid="false">
                <span id="movement-default-pos-z-error" class="field-error"></span>
                % if _imp:
                <small class="metric-echo">Stored: {{metric_echo(config.marker.default_pos_z)}}</small>
                % end
            </div>
        </div>
    </div>

    <div class="actions">
        <button type="submit" class="save-btn">Save</button>
    </div>
</form>
