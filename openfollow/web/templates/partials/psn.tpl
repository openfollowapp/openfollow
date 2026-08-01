<form id="psn-section" class="section {{'saved' if defined('saved') and saved else ''}}" data-fold-key="psn" data-help="psn"
      hx-post="/section/psn" hx-target="#psn-section" hx-swap="outerHTML" hx-trigger="submit">
    <div class="section-head">
        <h2>PSN Output</h2>
        <span class="section-note">PosiStageNet (PSN) – identity and multicast configuration</span>
    </div>

    <div class="group">
        <h3 class="group-title">Network Identity</h3>
        %# Station name (psn_system_name) is EDITED on the General tab's
        %# "Station Settings" box (web UI cleanup) – it's the device's
        %# identity, not a PSN-specific knob, and /section/general owns
        %# it. Shown here read-only so operators can see what PSN
        %# actually broadcasts; ``disabled`` keeps it out of this form's
        %# POST so there's a single writer.
        <div class="row">
            <div class="field wide">
                <label>PSN System Name</label>
                <input id="psn-psn-system-name" type="text" value="{{config.psn_system_name}}" disabled
                       aria-readonly="true">
                <span class="field-note">Read-only – set it in General → Station Settings.</span>
            </div>
            <div class="field">
                <label>PSN Multicast IP</label>
                <input id="psn-psn-mcast-ip" type="text" name="psn_mcast_ip" value="{{config.psn_mcast_ip}}"
                       hx-get="/api/validate/psn/psn_mcast_ip" hx-trigger="blur changed delay:200ms"
                       hx-target="#psn-psn-mcast-ip-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="psn-psn-mcast-ip-error" aria-invalid="false">
                <span id="psn-psn-mcast-ip-error" class="field-error"></span>
            </div>
            <div class="field">
                <label>Network Interface</label>
                %# Read-only pointer: the pin is edited in one place for every
                %# protocol (General > Interface Assignment), so this section
                %# reports where PSN is bound rather than offering a second
                %# control for the same field.
                %# Startup advisory: pinned iface wasn't live at boot, so
                %# auto-detected working IP. Rendered only when pin missed.
                % _adv = psn_source_advisory if defined('psn_source_advisory') and psn_source_advisory else {}
                % if _adv.get('banner'):
                <div class="notice warning" role="status">{{_adv['banner']}}</div>
                % end
                <div class="ia-pointer">
                    <span class="ia-pointer-value">{{config.psn_source_iface or 'Auto-detect'}}</span>
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
