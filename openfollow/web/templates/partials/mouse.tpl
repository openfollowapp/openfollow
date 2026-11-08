<form id="mouse-section" class="section {{'saved' if defined('saved') and saved else ''}}" data-fold-key="mouse" data-help="mouse"
      hx-post="/section/mouse" hx-target="#mouse-section" hx-swap="outerHTML" hx-trigger="submit">
    <div class="section-head">
        <h2>Mouse Input</h2>
    </div>

    <div class="group">
        <div class="row">
            <div class="field checkbox-field">
                <label for="mouse-enabled">Mouse</label>
                <div class="checkbox-wrap checkbox-field inline">
                    <input type="checkbox" id="mouse-enabled" name="mouse_enabled" {{'checked' if config.controller.mouse_enabled else ''}}>
                    <label for="mouse-enabled">Steer markers with the mouse</label>
                </div>
            </div>
            <div class="field checkbox-field">
                <label for="mouse-double-click-reset">Double right-click</label>
                <div class="checkbox-wrap checkbox-field inline">
                    <input type="checkbox" id="mouse-double-click-reset" name="mouse_double_click_reset" {{'checked' if config.controller.mouse_double_click_reset else ''}}>
                    <label for="mouse-double-click-reset">Reset and release the marker</label>
                </div>
            </div>
        </div>
    </div>

    <div class="group">
        <h3 class="group-title">Steering</h3>
        <div class="row">
            <div class="field">
                <label for="mouse-hysteresis-px">Hysteresis (px)</label>
                <input id="mouse-hysteresis-px" type="number" name="mouse_hysteresis_px" value="{{config.controller.mouse_hysteresis_px}}" min="0" max="200" step="1"
                       hx-get="/api/validate/mouse/mouse_hysteresis_px" hx-trigger="blur changed delay:200ms"
                       hx-target="#mouse-hysteresis-px-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="mouse-hysteresis-px-error" aria-invalid="false">
                <span id="mouse-hysteresis-px-error" class="field-error"></span>
            </div>
            <div class="field">
                <label for="mouse-smoothing">Smoothing</label>
                <input id="mouse-smoothing" type="number" name="mouse_smoothing" value="{{config.controller.mouse_smoothing}}" min="0" max="1" step="any"
                       hx-get="/api/validate/mouse/mouse_smoothing" hx-trigger="blur changed delay:200ms"
                       hx-target="#mouse-smoothing-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="mouse-smoothing-error" aria-invalid="false">
                <span id="mouse-smoothing-error" class="field-error"></span>
            </div>
            <div class="field">
                <label for="mouse-max-y">Maximum Y+ (m)</label>
                <input id="mouse-max-y" type="number" name="mouse_max_y" value="{{config.controller.mouse_max_y}}" min="0" max="10000" step="any"
                       hx-get="/api/validate/mouse/mouse_max_y" hx-trigger="blur changed delay:200ms"
                       hx-target="#mouse-max-y-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="mouse-max-y-error" aria-invalid="false">
                <span id="mouse-max-y-error" class="field-error"></span>
            </div>
        </div>
    </div>

    <div class="group">
        <h3 class="group-title">Scroll Wheel (Height)</h3>
        <div class="row">
            <div class="field checkbox-field">
                <label for="mouse-wheel-z-enabled">Scroll wheel</label>
                <div class="checkbox-wrap checkbox-field inline">
                    <input type="checkbox" id="mouse-wheel-z-enabled" name="mouse_wheel_z_enabled" {{'checked' if config.controller.mouse_wheel_z_enabled else ''}}>
                    <label for="mouse-wheel-z-enabled">Raise and lower the marker with the wheel</label>
                </div>
            </div>
            <div class="field">
                <label for="mouse-wheel-invert">Wheel direction</label>
                <select id="mouse-wheel-invert" name="mouse_wheel_invert">
                    <option value="false" {{'' if config.controller.mouse_wheel_invert else 'selected'}}>Scroll up raises the marker</option>
                    <option value="true" {{'selected' if config.controller.mouse_wheel_invert else ''}}>Scroll up lowers the marker</option>
                </select>
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
