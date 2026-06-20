<form id="rttrpm-output-section" class="section experimental-feature {{'saved' if defined('saved') and saved else ''}}" data-fold-key="rttrpm_output" data-help="rttrpm_output"
      hx-post="/section/rttrpm_output" hx-target="#rttrpm-output-section" hx-swap="outerHTML" hx-trigger="submit">
    <div class="section-head">
        <h2>RTTrPM Output <span class="badge-experimental">Experimental</span></h2>
        <span class="section-note">Real Time Tracking Protocol – Motion – unicast UDP send-only output</span>
    </div>

    <div class="group">
        <h3 class="group-title">RTTrPM Network</h3>
        <div class="row">
            <div class="field checkbox-field">
                <label>Enabled</label>
                <div class="checkbox-wrap"><input type="checkbox" name="enabled" {{'checked' if config.rttrpm_output.enabled else ''}}></div>
            </div>
            <div class="field">
                <label>Destination Host</label>
                <input id="rttrpm-output-host" type="text" name="host" value="{{config.rttrpm_output.host}}" placeholder="127.0.0.1"
                       hx-get="/api/validate/rttrpm_output/host" hx-trigger="blur changed delay:200ms"
                       hx-target="#rttrpm-output-host-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="rttrpm-output-host-error" aria-invalid="false">
                <span id="rttrpm-output-host-error" class="field-error"></span>
                <span class="field-note">IP address of the RTTrPM receiver</span>
            </div>
            <div class="field">
                <label>UDP Port</label>
                <input id="rttrpm-output-port" type="number" name="port" value="{{config.rttrpm_output.port}}" min="1" max="65535" step="1"
                       hx-get="/api/validate/rttrpm_output/port" hx-trigger="blur changed delay:200ms"
                       hx-target="#rttrpm-output-port-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="rttrpm-output-port-error" aria-invalid="false">
                <span id="rttrpm-output-port-error" class="field-error"></span>
            </div>
            <div class="field">
                <label>Send Rate (FPS)</label>
                <input id="rttrpm-output-fps" type="number" name="fps" value="{{config.rttrpm_output.fps}}" min="1" max="240" step="1"
                       hx-get="/api/validate/rttrpm_output/fps" hx-trigger="blur changed delay:200ms"
                       hx-target="#rttrpm-output-fps-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="rttrpm-output-fps-error" aria-invalid="false">
                <span id="rttrpm-output-fps-error" class="field-error"></span>
            </div>
            <div class="field">
                <label>Context</label>
                <input id="rttrpm-output-context" type="number" name="context" value="{{config.rttrpm_output.context}}" min="0" max="4294967295" step="1"
                       hx-get="/api/validate/rttrpm_output/context" hx-trigger="blur changed delay:200ms"
                       hx-target="#rttrpm-output-context-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="rttrpm-output-context-error" aria-invalid="false">
                <span id="rttrpm-output-context-error" class="field-error"></span>
                <span class="field-note">User-defined context value (0–4294967295)</span>
            </div>
        </div>
    </div>

    <div class="actions">
        <button type="submit" class="save-btn">Save</button>
    </div>
</form>
