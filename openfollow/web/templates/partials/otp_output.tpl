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
                <label>Source Interface (optional)</label>
                <div class="input-with-button">
                    %# Pin stable interface name (eth0/wlan0), not IP – resolved
                    %# to a bind IP at runtime, like psn_source_iface.
                    <select name="source_iface" hx-get="/network/interfaces/by_name?current={{config.otp_output.source_iface}}" hx-trigger="load, click from:#refresh-iface-otp"
                            hx-target="this" hx-swap="innerHTML">
                        <option value="{{config.otp_output.source_iface}}">{{config.otp_output.source_iface or '-- Loading... --'}}</option>
                    </select>
                    <button type="button" id="refresh-iface-otp" class="secondary">Scan</button>
                </div>
            </div>
        </div>
    </div>

    <div class="actions">
        <button type="submit" class="save-btn">Save</button>
    </div>
</form>
