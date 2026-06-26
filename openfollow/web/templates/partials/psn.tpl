<form id="psn-section" class="section {{'saved' if defined('saved') and saved else ''}}" data-fold-key="psn" data-help="psn"
      hx-post="/section/psn" hx-target="#psn-section" hx-swap="outerHTML" hx-trigger="submit">
    <div class="section-head">
        <h2>{{_('PSN Output')}}</h2>
        <span class="section-note">{{_('PosiStageNet (PSN) – identity and multicast configuration')}}</span>
    </div>

    <div class="group">
        <h3 class="group-title">{{_('Network Identity')}}</h3>
        %# Station name (psn_system_name) is EDITED on the General tab's
        %# "Station Settings" box (web UI cleanup) – it's the device's
        %# identity, not a PSN-specific knob, and /section/general owns
        %# it. Shown here read-only so operators can see what PSN
        %# actually broadcasts; ``disabled`` keeps it out of this form's
        %# POST so there's a single writer.
        <div class="row">
            <div class="field wide">
                <label>{{_('PSN System Name')}}</label>
                <input id="psn-psn-system-name" type="text" value="{{config.psn_system_name}}" disabled
                       aria-readonly="true">
                <span class="field-note">{{_('Read-only – set it in General → Station Settings.')}}</span>
            </div>
            <div class="field">
                <label>{{_('PSN Multicast IP')}}</label>
                <input id="psn-psn-mcast-ip" type="text" name="psn_mcast_ip" value="{{config.psn_mcast_ip}}"
                       hx-get="/api/validate/psn/psn_mcast_ip" hx-trigger="blur changed delay:200ms"
                       hx-target="#psn-psn-mcast-ip-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="psn-psn-mcast-ip-error" aria-invalid="false">
                <span id="psn-psn-mcast-ip-error" class="field-error"></span>
            </div>
            <div class="field">
                <label>{{_('PSN Network Interface')}}</label>
                %# Startup advisory: pinned iface wasn't live at boot, so
                %# auto-detected working IP. Rendered only when pin missed.
                % _adv = psn_source_advisory if defined('psn_source_advisory') and psn_source_advisory else {}
                % if _adv.get('banner'):
                <div class="notice warning" role="status">{{_adv['banner']}}</div>
                % end
                <div class="input-with-button">
                    %# Pin stable interface name (eth0/wlan0, not IP).
                    %# Iface stable across DHCP/venue changes.
                    <select name="psn_source_iface" hx-get="/network/interfaces/by_name" hx-trigger="load, click from:#refresh-iface-psn"
                            hx-target="this" hx-swap="innerHTML">
                        <option value="{{config.psn_source_iface}}">{{config.psn_source_iface or _('-- Loading... --')}}</option>
                    </select>
                    <button type="button" id="refresh-iface-psn" class="secondary">{{_('Scan')}}</button>
                </div>
            </div>
        </div>
    </div>

    <div class="actions">
        <button type="submit" class="save-btn">{{_('Save')}}</button>
    </div>
</form>
