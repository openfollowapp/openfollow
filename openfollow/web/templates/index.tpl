% rebase('base.tpl', header_logout=bool(config.web_pin), platform_warning_inline=True)

<div class="tab-bar">
    <button type="button" class="tab-btn" data-tab="overview">Overview</button>
    <button type="button" class="tab-btn" data-tab="general">General</button>
    <button type="button" class="tab-btn" data-tab="camera-grid">Camera & Grid</button>
    <button type="button" class="tab-btn" data-tab="marker-and-zones">Markers & Zones</button>
    <button type="button" class="tab-btn" data-tab="input">Input</button>
    <button type="button" class="tab-btn" data-tab="output">Output</button>
    <button type="button" class="tab-btn" data-tab="detection" data-feature="detection">Person Detection</button>
</div>
% include('partials/platform_warning.tpl', platform_warning=get('platform_warning'))

<!-- Overview -->
<div class="tab-content" id="tab-overview">
    % include('partials/diagnostics.tpl', stats=stats, cards=diagnostics_cards, local=local, peers=peers, station_down=station_down)
</div>

<!-- General -->
<div class="tab-content" id="tab-general">
    <div class="wizard-launch">
        <a href="/wizard" class="btn-link save-btn">Open Setup Wizard</a>
        <span class="section-note">Guided camera positioning and grid calibration</span>
    </div>
    % include('partials/general.tpl', config=config, saved=False, local_ips=local_ips, update_status=update_status, fqdn_problems=fqdn_problems, update_available=update_available, latest_version=latest_version, update_supported=update_supported, startup_supported=startup_supported)
    %# "Send config to other stations" (send_config.tpl) is hidden –
    %# peer-broadcast is being replaced with peer-pull; partial and route
    %# left for backwards compatibility. Re-add to restore.
    %# include('partials/send_config.tpl')
    % include('partials/config_transfer.tpl')
</div>

<!-- Camera & Grid -->
<div class="tab-content" id="tab-camera-grid">
    <div class="wizard-launch">
        <a href="/wizard" class="btn-link save-btn">Open Setup Wizard</a>
        <span class="section-note">Guided camera positioning and grid calibration</span>
    </div>
    % include('partials/video_source.tpl', config=config, saved=False, available_inputs=available_inputs, input_html_fragments=input_html_fragments, video_failure=video_failure, video_failure_text=video_failure_text, video_error_message=video_error_message, video_failure_action=video_failure_action, video_limits_text=video_limits_text, video_limits_action=video_limits_action)
    % include('partials/camera.tpl', config=config, saved=False, video_size=get('camera_video_size'))
    % include('partials/grid.tpl', config=config, saved=False)
</div>

<!-- Markers & Zones -->
<div class="tab-content" id="tab-marker-and-zones">
    % include('partials/marker.tpl', config=config, saved=False)
    % include('partials/movement.tpl', config=config, saved=False)
    % include('partials/trigger_zones.tpl', config=config, saved=False)
    % include('partials/zone_editor.tpl', config=config)
    % include('partials/paths.tpl', config=config, saved=False)
    % include('partials/path_editor.tpl')
</div>

<!-- Input -->
<div class="tab-content" id="tab-input">
    % include('partials/controller_slots.tpl', controllers=controller_slots)
    % include('partials/gamepad.tpl', config=config, saved=False, button_names=button_names)
    % include('partials/keyboard.tpl', config=config, saved=False, keyboard_layout=keyboard_layout)
    % include('partials/mouse.tpl', config=config, saved=False)
    % include('partials/mouse3d.tpl', config=config, saved=False, mouse3d_status=stats.get('mouse3d') or {})
    % include('partials/osc.tpl', config=config, saved=False)
    % include('partials/operator_messages.tpl', config=config, saved=False)
    % include('partials/touch_remote.tpl', config=config, saved=False)
    % # MIDI page: Devices and Virtual Faders sections. Config constants
    % # imported here keep partial dropdowns / fader count in sync with
    % # running ``MidiSubsystem``.
    % from openfollow.configuration import VALID_FADER_MIDI_TYPES
    % include('partials/midi.tpl',
    %     config=config, saved=False,
    %     **midi_devices,
    %     valid_fader_midi_types=VALID_FADER_MIDI_TYPES,
    %     marker_fader_values=marker_fader_values,
    %     fader_values=fader_values)
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
    %     placeholders=sorted(PLACEHOLDERS),
    %     registered_marker_ids=registered_marker_ids,
    %     unresolved_by_row=unresolved_by_row,
    %     marker_display_by_row=marker_display_by_row,
    %     unresolved_reasons_by_row=unresolved_reasons_by_row,
    %     fault_summary_by_row=fault_summary_by_row)
</div>

<!-- Person Detection -->
<div class="tab-content" id="tab-detection" data-feature="detection">
    % include('partials/detection.tpl', config=config, saved=False)
</div>
