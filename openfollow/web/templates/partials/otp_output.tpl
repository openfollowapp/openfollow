<form id="otp-output-section" class="section {{'saved' if defined('saved') and saved else ''}}" data-fold-key="otp_output" data-help="otp_output"
      hx-post="/section/otp_output" hx-target="#otp-output-section" hx-swap="outerHTML" hx-trigger="submit">
    <div class="section-head">
        <h2>OTP Output</h2>
        <span class="section-note">ANSI E1.59 Object Transform Protocol – parallel output alongside PSN</span>
    </div>

    <div class="group">
        <h3 class="group-title">OTP Network</h3>
        <div class="row">
            <div class="field checkbox-field">
                <label>Enabled</label>
                <div class="checkbox-wrap"><input type="checkbox" name="enabled" {{'checked' if config.otp_output.enabled else ''}}></div>
            </div>
            <div class="field">
                <label>System Number</label>
                <input id="otp-output-system-number" type="number" name="system_number" value="{{config.otp_output.system_number}}" min="1" max="200" step="1"
                       hx-get="/api/validate/otp_output/system_number" hx-trigger="blur changed delay:200ms"
                       hx-target="#otp-output-system-number-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="otp-output-system-number-error" aria-invalid="false">
                <span id="otp-output-system-number-error" class="field-error"></span>
            </div>
            <div class="field">
                <label>Multicast addresses</label>
                <div class="readonly-display" aria-label="Computed OTP multicast addresses">
                    <div><strong>Transform:</strong> {{config.otp_output.transform_mcast_ip}}</div>
                    <div><strong>Advertisement:</strong> {{config.otp_output.advertisement_mcast_ip}}</div>
                </div>
            </div>
            <div class="field">
                <label>UDP Port</label>
                <input id="otp-output-port" type="number" name="port" value="{{config.otp_output.port}}" min="1" max="65535" step="1"
                       hx-get="/api/validate/otp_output/port" hx-trigger="blur changed delay:200ms"
                       hx-target="#otp-output-port-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="otp-output-port-error" aria-invalid="false">
                <span id="otp-output-port-error" class="field-error"></span>
            </div>
        </div>
        <div class="row">
            <div class="field">
                <label>Priority</label>
                <input id="otp-output-priority" type="number" name="priority" value="{{config.otp_output.priority}}" min="0" max="200" step="1"
                       hx-get="/api/validate/otp_output/priority" hx-trigger="blur changed delay:200ms"
                       hx-target="#otp-output-priority-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="otp-output-priority-error" aria-invalid="false">
                <span id="otp-output-priority-error" class="field-error"></span>
            </div>
            <div class="field">
                <label>Source Interface</label>
                %# Read-only pointer – the pin is edited centrally in
                %# General > Interface Assignment, alongside every other plane.
                <div class="ia-pointer">
                    <span class="ia-pointer-value">{{config.otp_output.source_iface or 'Follows station interface'}}</span>
                    <a class="ia-link" href="#interface-assignment"
                       onclick="goToSection('general', 'interface-assignment'); return false;">Change in General &rsaquo; Interface Assignment</a>
                </div>
            </div>
        </div>
    </div>

    <div class="actions">
        <button type="submit" class="save-btn">Save</button>
    </div>
</form>
