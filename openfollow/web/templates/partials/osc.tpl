<form id="osc-section" class="section {{'saved' if defined('saved') and saved else ''}}" data-fold-key="osc" data-help="osc"
      hx-post="/section/osc" hx-target="#osc-section" hx-swap="outerHTML" hx-trigger="submit">
    <div class="section-head">
        <h2>{{_('OSC Input')}}</h2>
        <span class="section-note">{{_('Receive marker positions via OSC')}}</span>
    </div>

    <div class="group">
        <h3 class="group-title">{{_('OSC Server')}}</h3>
        <div class="row">
            <div class="field checkbox-field">
                <label>{{_('Enabled')}}</label>
                <div class="checkbox-wrap"><input type="checkbox" name="enabled" {{'checked' if config.osc.enabled else ''}}></div>
            </div>
            <div class="field">
                <label>{{_('UDP Port')}}</label>
                <input id="osc-port" type="number" name="port" value="{{config.osc.port}}" min="1" max="65535" step="1"
                       hx-get="/api/validate/osc/port" hx-trigger="blur changed delay:200ms"
                       hx-target="#osc-port-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="osc-port-error" aria-invalid="false">
                <span id="osc-port-error" class="field-error"></span>
            </div>
        </div>
        <div class="row">
            <div class="field" style="flex: 1 1 100%;">
                <label>{{_('Multicast group')}}</label>
                <input id="osc-multicast-group" type="text" name="multicast_group"
                       value="{{config.osc.multicast_group}}"
                       placeholder="{{_('e.g. 239.10.10.10 (blank = off)')}}"
                       hx-get="/api/validate/osc/multicast_group" hx-trigger="blur changed delay:200ms"
                       hx-target="#osc-multicast-group-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="osc-multicast-group-error" aria-invalid="false">
                <span id="osc-multicast-group-error" class="field-error"></span>
            </div>
        </div>
        <div class="row">
            <div class="field" style="flex: 1 1 100%;">
                <label>{{_('Allowed sender IPs')}}</label>
                <input id="osc-allowed-sender-ips" type="text" name="allowed_sender_ips"
                       value="{{', '.join(str(ip) for ip in config.osc.allowed_sender_ips)}}"
                       placeholder="{{_('e.g. 192.168.1.10, 192.168.1.20')}}"
                       hx-get="/api/validate/osc/allowed_sender_ips" hx-trigger="blur changed delay:200ms"
                       hx-target="#osc-allowed-sender-ips-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="osc-allowed-sender-ips-error" aria-invalid="false">
                <span id="osc-allowed-sender-ips-error" class="field-error"></span>
            </div>
        </div>
    </div>

    <div class="actions">
        <button type="submit" class="save-btn">{{_('Save')}}</button>
    </div>
</form>
