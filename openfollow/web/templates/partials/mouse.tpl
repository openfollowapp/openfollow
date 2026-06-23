<form id="mouse-section" class="section experimental-feature {{'saved' if defined('saved') and saved else ''}}" data-fold-key="mouse" data-help="mouse"
      hx-post="/section/mouse" hx-target="#mouse-section" hx-swap="outerHTML" hx-trigger="submit">
    <div class="section-head">
        <h2>Mouse Input <span class="badge-experimental">Experimental</span></h2>
        <span class="section-note">Click a marker's ground circle to take control; right-click releases</span>
    </div>

    <div class="group">
        <div class="row">
            <div class="field checkbox-field">
                <label>Enabled</label>
                <div class="checkbox-wrap"><input type="checkbox" name="mouse_enabled" {{'checked' if config.controller.mouse_enabled else ''}}></div>
            </div>
        </div>
    </div>

    <div class="group">
        <h3 class="group-title">Steering</h3>
        <div class="row">
            <div class="field">
                <label for="mouse-hysteresis-px">Hysteresis (px)</label>
                <input id="mouse-hysteresis-px" type="number" name="mouse_hysteresis_px" value="{{config.controller.mouse_hysteresis_px}}" min="0" max="200" step="any"
                       hx-get="/api/validate/mouse/mouse_hysteresis_px" hx-trigger="blur changed delay:200ms"
                       hx-target="#mouse-hysteresis-px-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="mouse-hysteresis-px-error" aria-invalid="false">
                <span id="mouse-hysteresis-px-error" class="field-error"></span>
            </div>
            <div class="field">
                <label for="mouse-smoothing">Smoothing</label>
                <input id="mouse-smoothing" type="number" name="mouse_smoothing" value="{{config.controller.mouse_smoothing}}" min="0.01" max="1" step="any"
                       hx-get="/api/validate/mouse/mouse_smoothing" hx-trigger="blur changed delay:200ms"
                       hx-target="#mouse-smoothing-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="mouse-smoothing-error" aria-invalid="false">
                <span id="mouse-smoothing-error" class="field-error"></span>
            </div>
            <div class="field">
                <label for="mouse-max-distance">Max distance (m)</label>
                <input id="mouse-max-distance" type="number" name="mouse_max_distance" value="{{config.controller.mouse_max_distance}}" min="0" max="10000" step="any"
                       hx-get="/api/validate/mouse/mouse_max_distance" hx-trigger="blur changed delay:200ms"
                       hx-target="#mouse-max-distance-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="mouse-max-distance-error" aria-invalid="false">
                <span id="mouse-max-distance-error" class="field-error"></span>
            </div>
        </div>
        <span class="field-note">0 hysteresis / 1.0 smoothing = direct 1:1 control; max distance 0 = unlimited.</span>
    </div>

    <div class="group">
        <h3 class="group-title">Scroll Wheel (Height)</h3>
        <div class="row">
            <div class="field checkbox-field">
                <label>Wheel controls Z</label>
                <div class="checkbox-wrap"><input type="checkbox" name="mouse_wheel_z_enabled" {{'checked' if config.controller.mouse_wheel_z_enabled else ''}}></div>
            </div>
            <div class="field checkbox-field">
                <label>Invert wheel</label>
                <div class="checkbox-wrap"><input type="checkbox" name="mouse_wheel_invert" {{'checked' if config.controller.mouse_wheel_invert else ''}}></div>
            </div>
            <div class="field">
                <label for="mouse-wheel-z-step">Step per tick (m)</label>
                <input id="mouse-wheel-z-step" type="number" name="mouse_wheel_z_step" value="{{config.controller.mouse_wheel_z_step}}" min="0" max="10" step="any"
                       hx-get="/api/validate/mouse/mouse_wheel_z_step" hx-trigger="blur changed delay:200ms"
                       hx-target="#mouse-wheel-z-step-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="mouse-wheel-z-step-error" aria-invalid="false">
                <span id="mouse-wheel-z-step-error" class="field-error"></span>
            </div>
        </div>
    </div>

    <div class="actions">
        <button type="submit" class="save-btn">Save</button>
    </div>
</form>
