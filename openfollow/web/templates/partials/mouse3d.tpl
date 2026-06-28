% from openfollow.configuration import MOUSE3D_AXIS_TARGETS, VALID_CURVES
% m = config.mouse3d
<form id="mouse3d-section" class="section {{'saved' if defined('saved') and saved else ''}}" data-fold-key="mouse3d" data-help="mouse3d"
      hx-post="/section/mouse3d" hx-target="#mouse3d-section" hx-swap="outerHTML" hx-trigger="submit">
    <div class="section-head">
        <h2>3D Mouse Input</h2>
        <span class="section-note">Steer the selected marker with a connected 6DOF 3D Mouse</span>
    </div>

    <div class="group">
        <div class="row">
            <div class="field checkbox-field">
                <label>Enabled</label>
                <div class="checkbox-wrap"><input type="checkbox" name="enabled" {{'checked' if m.enabled else ''}}></div>
            </div>
            <div class="field">
                <label for="m3d-deadzone">Deadzone</label>
                <input id="m3d-deadzone" type="number" name="deadzone" value="{{m.deadzone}}" min="0" max="1" step="any"
                       hx-get="/api/validate/mouse3d/deadzone" hx-trigger="blur changed delay:200ms"
                       hx-target="#m3d-deadzone-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="m3d-deadzone-error" aria-invalid="false">
                <span id="m3d-deadzone-error" class="field-error"></span>
            </div>
            <div class="field">
                <label for="m3d-curve">Response curve</label>
                <select id="m3d-curve" name="curve">
                    % for c in VALID_CURVES:
                    <option value="{{c}}" {{'selected' if m.curve == c else ''}}>{{c}}</option>
                    % end
                </select>
            </div>
        </div>
    </div>

    <div class="group">
        <h3 class="group-title">Axis Mapping</h3>

        %# pan_x
        <div class="row">
            <div class="field">
                <label for="m3d-map-pan_x">Pan X target</label>
                <select id="m3d-map-pan_x" name="map_pan_x">
                    % for t in MOUSE3D_AXIS_TARGETS:
                    <option value="{{t}}" {{'selected' if m.map_pan_x == t else ''}}>{{t}}</option>
                    % end
                </select>
            </div>
            <div class="field">
                <label for="m3d-sens-pan_x">Sensitivity</label>
                <input id="m3d-sens-pan_x" type="number" name="sens_pan_x" value="{{m.sens_pan_x}}" min="0" max="10" step="any"
                       hx-get="/api/validate/mouse3d/sens_pan_x" hx-trigger="blur changed delay:200ms"
                       hx-target="#m3d-sens-pan_x-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="m3d-sens-pan_x-error" aria-invalid="false">
                <span id="m3d-sens-pan_x-error" class="field-error"></span>
            </div>
            <div class="field checkbox-field">
                <label>Invert</label>
                <div class="checkbox-wrap"><input type="checkbox" name="invert_pan_x" {{'checked' if m.invert_pan_x else ''}}></div>
            </div>
        </div>

        %# pan_y
        <div class="row">
            <div class="field">
                <label for="m3d-map-pan_y">Pan Y target</label>
                <select id="m3d-map-pan_y" name="map_pan_y">
                    % for t in MOUSE3D_AXIS_TARGETS:
                    <option value="{{t}}" {{'selected' if m.map_pan_y == t else ''}}>{{t}}</option>
                    % end
                </select>
            </div>
            <div class="field">
                <label for="m3d-sens-pan_y">Sensitivity</label>
                <input id="m3d-sens-pan_y" type="number" name="sens_pan_y" value="{{m.sens_pan_y}}" min="0" max="10" step="any"
                       hx-get="/api/validate/mouse3d/sens_pan_y" hx-trigger="blur changed delay:200ms"
                       hx-target="#m3d-sens-pan_y-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="m3d-sens-pan_y-error" aria-invalid="false">
                <span id="m3d-sens-pan_y-error" class="field-error"></span>
            </div>
            <div class="field checkbox-field">
                <label>Invert</label>
                <div class="checkbox-wrap"><input type="checkbox" name="invert_pan_y" {{'checked' if m.invert_pan_y else ''}}></div>
            </div>
        </div>

        %# lift
        <div class="row">
            <div class="field">
                <label for="m3d-map-lift">Lift target</label>
                <select id="m3d-map-lift" name="map_lift">
                    % for t in MOUSE3D_AXIS_TARGETS:
                    <option value="{{t}}" {{'selected' if m.map_lift == t else ''}}>{{t}}</option>
                    % end
                </select>
            </div>
            <div class="field">
                <label for="m3d-sens-lift">Sensitivity</label>
                <input id="m3d-sens-lift" type="number" name="sens_lift" value="{{m.sens_lift}}" min="0" max="10" step="any"
                       hx-get="/api/validate/mouse3d/sens_lift" hx-trigger="blur changed delay:200ms"
                       hx-target="#m3d-sens-lift-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="m3d-sens-lift-error" aria-invalid="false">
                <span id="m3d-sens-lift-error" class="field-error"></span>
            </div>
            <div class="field checkbox-field">
                <label>Invert</label>
                <div class="checkbox-wrap"><input type="checkbox" name="invert_lift" {{'checked' if m.invert_lift else ''}}></div>
            </div>
        </div>

        %# pitch
        <div class="row">
            <div class="field">
                <label for="m3d-map-pitch">Pitch target</label>
                <select id="m3d-map-pitch" name="map_pitch">
                    % for t in MOUSE3D_AXIS_TARGETS:
                    <option value="{{t}}" {{'selected' if m.map_pitch == t else ''}}>{{t}}</option>
                    % end
                </select>
            </div>
            <div class="field">
                <label for="m3d-sens-pitch">Sensitivity</label>
                <input id="m3d-sens-pitch" type="number" name="sens_pitch" value="{{m.sens_pitch}}" min="0" max="10" step="any"
                       hx-get="/api/validate/mouse3d/sens_pitch" hx-trigger="blur changed delay:200ms"
                       hx-target="#m3d-sens-pitch-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="m3d-sens-pitch-error" aria-invalid="false">
                <span id="m3d-sens-pitch-error" class="field-error"></span>
            </div>
            <div class="field checkbox-field">
                <label>Invert</label>
                <div class="checkbox-wrap"><input type="checkbox" name="invert_pitch" {{'checked' if m.invert_pitch else ''}}></div>
            </div>
        </div>

        %# yaw
        <div class="row">
            <div class="field">
                <label for="m3d-map-yaw">Yaw target</label>
                <select id="m3d-map-yaw" name="map_yaw">
                    % for t in MOUSE3D_AXIS_TARGETS:
                    <option value="{{t}}" {{'selected' if m.map_yaw == t else ''}}>{{t}}</option>
                    % end
                </select>
            </div>
            <div class="field">
                <label for="m3d-sens-yaw">Sensitivity</label>
                <input id="m3d-sens-yaw" type="number" name="sens_yaw" value="{{m.sens_yaw}}" min="0" max="10" step="any"
                       hx-get="/api/validate/mouse3d/sens_yaw" hx-trigger="blur changed delay:200ms"
                       hx-target="#m3d-sens-yaw-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="m3d-sens-yaw-error" aria-invalid="false">
                <span id="m3d-sens-yaw-error" class="field-error"></span>
            </div>
            <div class="field checkbox-field">
                <label>Invert</label>
                <div class="checkbox-wrap"><input type="checkbox" name="invert_yaw" {{'checked' if m.invert_yaw else ''}}></div>
            </div>
        </div>

        %# roll
        <div class="row">
            <div class="field">
                <label for="m3d-map-roll">Roll target</label>
                <select id="m3d-map-roll" name="map_roll">
                    % for t in MOUSE3D_AXIS_TARGETS:
                    <option value="{{t}}" {{'selected' if m.map_roll == t else ''}}>{{t}}</option>
                    % end
                </select>
            </div>
            <div class="field">
                <label for="m3d-sens-roll">Sensitivity</label>
                <input id="m3d-sens-roll" type="number" name="sens_roll" value="{{m.sens_roll}}" min="0" max="10" step="any"
                       hx-get="/api/validate/mouse3d/sens_roll" hx-trigger="blur changed delay:200ms"
                       hx-target="#m3d-sens-roll-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="m3d-sens-roll-error" aria-invalid="false">
                <span id="m3d-sens-roll-error" class="field-error"></span>
            </div>
            <div class="field checkbox-field">
                <label>Invert</label>
                <div class="checkbox-wrap"><input type="checkbox" name="invert_roll" {{'checked' if m.invert_roll else ''}}></div>
            </div>
        </div>
    </div>

    <div class="group">
        <h3 class="group-title">Buttons</h3>
        <span class="section-note">Click Detect, then press a button. Blank = unbound.</span>
        <div class="row">
            <div class="field">
                <label for="m3d-btn_reset">Reset marker</label>
                <div class="detect-input">
                    <input id="m3d-btn_reset" type="number" name="btn_reset" value="{{m.btn_reset if m.btn_reset >= 0 else ''}}" min="0" step="1" placeholder="none"
                           hx-get="/api/validate/mouse3d/btn_reset" hx-trigger="blur changed delay:200ms"
                           hx-target="#m3d-btn_reset-error" hx-swap="innerHTML" hx-include="closest form"
                           aria-describedby="m3d-btn_reset-error" aria-invalid="false">
                    <button type="button" class="secondary small detect-btn" data-detect-input="m3d-btn_reset" data-detect-url="/section/mouse3d/detect">Detect</button>
                </div>
                <span id="m3d-btn_reset-error" class="field-error"></span>
            </div>
            <div class="field">
                <label for="m3d-btn_settings">Settings menu</label>
                <div class="detect-input">
                    <input id="m3d-btn_settings" type="number" name="btn_settings" value="{{m.btn_settings if m.btn_settings >= 0 else ''}}" min="0" step="1" placeholder="none"
                           hx-get="/api/validate/mouse3d/btn_settings" hx-trigger="blur changed delay:200ms"
                           hx-target="#m3d-btn_settings-error" hx-swap="innerHTML" hx-include="closest form"
                           aria-describedby="m3d-btn_settings-error" aria-invalid="false">
                    <button type="button" class="secondary small detect-btn" data-detect-input="m3d-btn_settings" data-detect-url="/section/mouse3d/detect">Detect</button>
                </div>
                <span id="m3d-btn_settings-error" class="field-error"></span>
            </div>
        </div>
        <div class="row">
            <div class="field">
                <label for="m3d-btn_next_marker">Next marker</label>
                <div class="detect-input">
                    <input id="m3d-btn_next_marker" type="number" name="btn_next_marker" value="{{m.btn_next_marker if m.btn_next_marker >= 0 else ''}}" min="0" step="1" placeholder="none"
                           hx-get="/api/validate/mouse3d/btn_next_marker" hx-trigger="blur changed delay:200ms"
                           hx-target="#m3d-btn_next_marker-error" hx-swap="innerHTML" hx-include="closest form"
                           aria-describedby="m3d-btn_next_marker-error" aria-invalid="false">
                    <button type="button" class="secondary small detect-btn" data-detect-input="m3d-btn_next_marker" data-detect-url="/section/mouse3d/detect">Detect</button>
                </div>
                <span id="m3d-btn_next_marker-error" class="field-error"></span>
            </div>
            <div class="field">
                <label for="m3d-btn_prev_marker">Previous marker</label>
                <div class="detect-input">
                    <input id="m3d-btn_prev_marker" type="number" name="btn_prev_marker" value="{{m.btn_prev_marker if m.btn_prev_marker >= 0 else ''}}" min="0" step="1" placeholder="none"
                           hx-get="/api/validate/mouse3d/btn_prev_marker" hx-trigger="blur changed delay:200ms"
                           hx-target="#m3d-btn_prev_marker-error" hx-swap="innerHTML" hx-include="closest form"
                           aria-describedby="m3d-btn_prev_marker-error" aria-invalid="false">
                    <button type="button" class="secondary small detect-btn" data-detect-input="m3d-btn_prev_marker" data-detect-url="/section/mouse3d/detect">Detect</button>
                </div>
                <span id="m3d-btn_prev_marker-error" class="field-error"></span>
            </div>
        </div>
        <div class="row">
            <div class="field">
                <label for="m3d-btn_speed_up">Speed up</label>
                <div class="detect-input">
                    <input id="m3d-btn_speed_up" type="number" name="btn_speed_up" value="{{m.btn_speed_up if m.btn_speed_up >= 0 else ''}}" min="0" step="1" placeholder="none"
                           hx-get="/api/validate/mouse3d/btn_speed_up" hx-trigger="blur changed delay:200ms"
                           hx-target="#m3d-btn_speed_up-error" hx-swap="innerHTML" hx-include="closest form"
                           aria-describedby="m3d-btn_speed_up-error" aria-invalid="false">
                    <button type="button" class="secondary small detect-btn" data-detect-input="m3d-btn_speed_up" data-detect-url="/section/mouse3d/detect">Detect</button>
                </div>
                <span id="m3d-btn_speed_up-error" class="field-error"></span>
            </div>
            <div class="field">
                <label for="m3d-btn_speed_down">Speed down</label>
                <div class="detect-input">
                    <input id="m3d-btn_speed_down" type="number" name="btn_speed_down" value="{{m.btn_speed_down if m.btn_speed_down >= 0 else ''}}" min="0" step="1" placeholder="none"
                           hx-get="/api/validate/mouse3d/btn_speed_down" hx-trigger="blur changed delay:200ms"
                           hx-target="#m3d-btn_speed_down-error" hx-swap="innerHTML" hx-include="closest form"
                           aria-describedby="m3d-btn_speed_down-error" aria-invalid="false">
                    <button type="button" class="secondary small detect-btn" data-detect-input="m3d-btn_speed_down" data-detect-url="/section/mouse3d/detect">Detect</button>
                </div>
                <span id="m3d-btn_speed_down-error" class="field-error"></span>
            </div>
        </div>
        <div class="row">
            <div class="field">
                <label for="m3d-btn_toggle_help">Toggle help</label>
                <div class="detect-input">
                    <input id="m3d-btn_toggle_help" type="number" name="btn_toggle_help" value="{{m.btn_toggle_help if m.btn_toggle_help >= 0 else ''}}" min="0" step="1" placeholder="none"
                           hx-get="/api/validate/mouse3d/btn_toggle_help" hx-trigger="blur changed delay:200ms"
                           hx-target="#m3d-btn_toggle_help-error" hx-swap="innerHTML" hx-include="closest form"
                           aria-describedby="m3d-btn_toggle_help-error" aria-invalid="false">
                    <button type="button" class="secondary small detect-btn" data-detect-input="m3d-btn_toggle_help" data-detect-url="/section/mouse3d/detect">Detect</button>
                </div>
                <span id="m3d-btn_toggle_help-error" class="field-error"></span>
            </div>
            <div class="field">
                <label for="m3d-btn_toggle_zones">Toggle zones</label>
                <div class="detect-input">
                    <input id="m3d-btn_toggle_zones" type="number" name="btn_toggle_zones" value="{{m.btn_toggle_zones if m.btn_toggle_zones >= 0 else ''}}" min="0" step="1" placeholder="none"
                           hx-get="/api/validate/mouse3d/btn_toggle_zones" hx-trigger="blur changed delay:200ms"
                           hx-target="#m3d-btn_toggle_zones-error" hx-swap="innerHTML" hx-include="closest form"
                           aria-describedby="m3d-btn_toggle_zones-error" aria-invalid="false">
                    <button type="button" class="secondary small detect-btn" data-detect-input="m3d-btn_toggle_zones" data-detect-url="/section/mouse3d/detect">Detect</button>
                </div>
                <span id="m3d-btn_toggle_zones-error" class="field-error"></span>
            </div>
        </div>
    </div>

    <div class="actions">
        <button type="submit" class="save-btn">Save</button>
    </div>
</form>
