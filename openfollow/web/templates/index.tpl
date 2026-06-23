% rebase('base.tpl')

%# Restart button moved into the Diagnostics section (Overview tab) –
%# it's a rare-use action and doesn't earn a permanent slot in the
%# top chrome. The station name pill moved into the hero-panel's
%# top-right corner (see base.tpl). Logout (when a PIN is set) is
%# the only persistent top-right action that remains.
% if config.web_pin:
<div class="top-bar-actions">
    <form method="POST" action="/logout" style="margin:0;">
        <button type="submit" class="secondary small">Logout</button>
    </form>
</div>
% end
%# Restart progress banner – global (any tab can trigger the restart
%# from Diagnostics, so the notice has to live above the tabs).
<div id="top-restart-notice" class="restart-notice" style="display:none;margin-bottom:1rem;">{{_('App is restarting… Please wait.')}}</div>

<div class="tab-bar">
    <button type="button" class="tab-btn" data-tab="overview">{{_('Overview')}}</button>
    <button type="button" class="tab-btn" data-tab="general">{{_('General')}}</button>
    <button type="button" class="tab-btn" data-tab="camera-grid">{{_('Camera & Grid')}}</button>
    <button type="button" class="tab-btn" data-tab="marker-and-zones">{{_('Markers & Zones')}}</button>
    <button type="button" class="tab-btn" data-tab="input">{{_('Input')}}</button>
    <button type="button" class="tab-btn" data-tab="output">{{_('Output')}}</button>
    <button type="button" class="tab-btn experimental-feature" data-tab="detection">{{_('Person Detection')}}</button>
</div>

<!-- Overview -->
<div class="tab-content" id="tab-overview">
    % include('partials/overview.tpl', local=local, peers=peers)
    <div class="section" id="statistics-section" data-fold-key="statistics" data-help="statistics">
        <div class="section-head">
            <h2>{{_('Live Statistics')}}</h2>
            <span class="section-note">{{_('Core status first, advanced diagnostics on demand')}}</span>
        </div>
        <div id="statistics-section-content"
             hx-get="/section/statistics"
             hx-trigger="every 1s"
             hx-swap="innerHTML">
            % include('partials/statistics.tpl', stats=stats)
        </div>
    </div>
    % # The section shell renders once; only its live cards poll every 5s
    % # (see partials/diagnostics.tpl ``#diagnostics-live``), so the head /
    % # fold state / bundle + log-tail tools stay put without flickering.
    % include('partials/diagnostics.tpl',
    %     cards=diagnostics_cards,
    %     log_unavailable_warning=diagnostics_log_warning)
</div>

<!-- General -->
<div class="tab-content" id="tab-general">
    % include('partials/general.tpl', config=config, saved=False, local_ips=local_ips, update_status=update_status, network_state=network_state)
    %# "Send config to other stations" (send_config.tpl) is hidden –
    %# peer-broadcast is being replaced with peer-pull; partial and route
    %# left for backwards compatibility. Re-add to restore.
    %# include('partials/send_config.tpl')
    % include('partials/config_transfer.tpl')
</div>

<!-- Camera & Grid -->
<div class="tab-content" id="tab-camera-grid">
    <div class="wizard-launch">
        <a href="/wizard" class="btn-link save-btn">{{_('Open Setup Wizard')}}</a>
        <span class="section-note">{{_('Guided camera positioning and grid calibration')}}</span>
    </div>
    % include('partials/video_source.tpl', config=config, saved=False, available_inputs=available_inputs, input_html_fragments=input_html_fragments)
    % include('partials/camera.tpl', config=config, saved=False)
    % include('partials/grid.tpl', config=config, saved=False)
</div>

<!-- Markers & Zones -->
<div class="tab-content" id="tab-marker-and-zones">
    % include('partials/marker.tpl', config=config, saved=False)
    % include('partials/movement.tpl', config=config, saved=False)
    % include('partials/trigger_zones.tpl', config=config, saved=False)
    % include('partials/zone_editor.tpl', config=config)
</div>

<!-- Input -->
<div class="tab-content" id="tab-input">
    % include('partials/gamepad.tpl', config=config, saved=False, button_names=button_names)
    % include('partials/keyboard.tpl', config=config, saved=False)
    % include('partials/mouse.tpl', config=config, saved=False)
    % include('partials/osc.tpl', config=config, saved=False)
    % include('partials/operator_messages.tpl', config=config, saved=False)
    % # MIDI page: Devices and Virtual Faders sections. Config constants
    % # imported here keep partial dropdowns / fader count in sync with
    % # running ``MidiSubsystem``.
    % from openfollow.configuration import VALID_FADER_MIDI_TYPES
    % include('partials/midi.tpl',
    %     config=config, saved=False,
    %     discovered_devices=midi_discovered_devices,
    %     valid_fader_midi_types=VALID_FADER_MIDI_TYPES,
    %     marker_fader_values=marker_fader_values)
</div>

<!-- Output -->
<div class="tab-content" id="tab-output">
    % include('partials/psn.tpl', config=config, saved=False, local_ips=local_ips, psn_source_advisory=psn_source_advisory)
    % include('partials/otp_output.tpl', config=config, saved=False)
    % include('partials/rttrpm_output.tpl', config=config, saved=False)
    % from openfollow.configuration import (
    %     VALID_OSC_FRAMINGS,
    %     VALID_OSC_TRANSMITTER_PROTOCOLS,
    %     VALID_OSC_TRANSMITTER_RATES,
    %     VALID_KEY_NAMES,
    %     VALID_MIDI_MESSAGE_TYPES,
    %     VALID_TRIGGER_EDGES,
    %     VALID_TRIGGER_KINDS,
    %     VALID_TRIGGER_MODIFIERS,
    % )
    % from openfollow.osc.template import PLACEHOLDERS
    % # OSC destinations come first: transmitters/zones reference them.
    % include('partials/osc_destinations.tpl',
    %     config=config, saved=False, focus_id="",
    %     valid_protocols=VALID_OSC_TRANSMITTER_PROTOCOLS,
    %     valid_framings=VALID_OSC_FRAMINGS)
    % # Trigger forms need operator's fader/patch alias lists passed
    % # from route handler (``_osc_binding_form_sources``); keeps
    % # template declarative without reaching into route module.
    % include('partials/osc_bindings.tpl',
    %     config=config, saved=False, focus_id="",
    %     valid_rates=VALID_OSC_TRANSMITTER_RATES,
    %     valid_kinds=VALID_TRIGGER_KINDS,
    %     valid_edges=VALID_TRIGGER_EDGES,
    %     valid_modifiers=VALID_TRIGGER_MODIFIERS,
    %     valid_keys=sorted(VALID_KEY_NAMES),
    %     valid_buttons=button_names,
    %     valid_midi_types=VALID_MIDI_MESSAGE_TYPES,
    %     virtual_fader_names=virtual_fader_names,
    %     midi_patches=midi_patches,
    %     builtin_templates=osc_system_templates,
    %     user_templates=osc_user_templates,
    %     placeholders=sorted(PLACEHOLDERS))
</div>

<!-- Person Detection -->
<div class="tab-content experimental-feature" id="tab-detection">
    % include('partials/detection.tpl', config=config, saved=False)
</div>
