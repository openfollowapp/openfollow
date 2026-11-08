% from openfollow.configuration import KEYBOARD_ACTION_FORM_LABELS
% kb_labels = dict(KEYBOARD_ACTION_FORM_LABELS)
%# Set only where the kiosk session resolved the layout (see keyboard_layout_view).
% kb_layout = keyboard_layout if defined('keyboard_layout') else None
% kb_pending = kb_layout is not None and kb_layout.pending
<form id="keyboard-section" class="section {{'saved' if defined('saved') and saved else ''}}" data-fold-key="keyboard" data-help="keyboard"
      hx-post="/section/keyboard" hx-target="#keyboard-section" hx-swap="outerHTML" hx-trigger="submit">
    <div class="section-head">
        <h2>Keyboard Input</h2>
    </div>

    <div class="group">
        <div class="row">
            <div class="field checkbox-field">
                <label for="keyboard-enabled">Keyboard</label>
                <div class="checkbox-wrap checkbox-field inline">
                    <input type="checkbox" id="keyboard-enabled" name="keyboard_enabled" {{'checked' if config.controller.keyboard_enabled else ''}}>
                    <label for="keyboard-enabled">Move markers with the keyboard</label>
                </div>
            </div>
        </div>
        % if kb_layout is not None:
        <div class="row">
            <div class="field">
                <label for="keyboard-layout">Keyboard Layout</label>
                <select id="keyboard-layout" name="keyboard_layout"
                        hx-get="/api/validate/keyboard/keyboard_layout" hx-trigger="blur changed delay:200ms"
                        hx-target="#keyboard-layout-error" hx-swap="innerHTML" hx-include="closest form"
                        aria-describedby="keyboard-layout-error" aria-invalid="false">
                    % for value, label in kb_layout.layouts:
                    <option value="{{value}}" {{'selected' if value == kb_layout.layout else ''}}>{{label}}</option>
                    % end
                </select>
                <span id="keyboard-layout-error" class="field-error"></span>
            </div>
            %# Reloads the options for a new layout. Save is off meanwhile, or it would send the previous layout's
            %# variant; a failed reload falls back to Standard. The gate runs again after, as htmx re-enables Save.
            <div class="field" hx-get="/section/keyboard/variants" hx-trigger="change from:#keyboard-layout"
                 hx-include="#keyboard-layout" hx-target="#keyboard-variant" hx-swap="innerHTML"
                 hx-disabled-elt="#keyboard-section button[type=submit]"
                 hx-on::after-request="if(!event.detail.successful) document.getElementById('keyboard-variant').value=''; refreshFormGate(this.closest('form'))">
                <label for="keyboard-variant">Variant</label>
                <select id="keyboard-variant" name="keyboard_variant"
                        hx-get="/api/validate/keyboard/keyboard_variant" hx-trigger="blur changed delay:200ms"
                        hx-target="#keyboard-variant-error" hx-swap="innerHTML" hx-include="closest form"
                        aria-describedby="keyboard-variant-error" aria-invalid="false">
                    % include('partials/keyboard_variant_options.tpl', variants=kb_layout.variants, selected=kb_layout.variant)
                </select>
                <span id="keyboard-variant-error" class="field-error"></span>
            </div>
        </div>
        % if kb_pending and kb_layout.layout_pending:
        <div class="field-caution-msg" role="status">In use: {{kb_layout.in_use_label}} until OpenFollow restarts.</div>
        % elif kb_pending:
        <div class="field-caution-msg" role="status">The system keyboard settings change when OpenFollow restarts.</div>
        % end
        % end

        <details class="inline-advanced" data-adv-key="keyboard-mapping">
            <summary>Button Mapping</summary>
            <div class="inline-advanced-content">
                <div style="margin-bottom:0.75rem;">
                    <button type="button" class="save-btn small" onclick="resetKeyboardMappingDefaults(this.closest('.inline-advanced-content'))">
                        Reset to Defaults
                    </button>
                </div>
                <div class="group">
                    <h3 class="group-title">Movement</h3>
                    <div class="row">
                        <div class="field">
                            <label>X / Y Layout</label>
                            <select name="key_move_layout">
                                <option value="wasd" {{'selected' if config.controller.key_move_layout == 'wasd' else ''}}>WASD</option>
                                <option value="ijkl" {{'selected' if config.controller.key_move_layout == 'ijkl' else ''}}>IJKL</option>
                                <option value="numpad" {{'selected' if config.controller.key_move_layout == 'numpad' else ''}}>Numpad (8/4/2/6)</option>
                            </select>
                            <span class="field-note">Arrow keys are reserved for on-display menu navigation.</span>
                        </div>
                        <div class="field">
                            <label for="keyboard-key-move-z-up">{{kb_labels['key_move_z_up']}}</label>
                            <input id="keyboard-key-move-z-up" type="text" name="key_move_z_up"
                                   value="{{config.controller.key_move_z_up}}"
                       hx-get="/api/validate/keyboard/key_move_z_up" hx-trigger="blur changed delay:200ms"
                       hx-target="#keyboard-key-move-z-up-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="keyboard-key-move-z-up-error" aria-invalid="false">
                <span id="keyboard-key-move-z-up-error" class="field-error"></span>
                        </div>
                        <div class="field">
                            <label for="keyboard-key-move-z-down">{{kb_labels['key_move_z_down']}}</label>
                            <input id="keyboard-key-move-z-down" type="text" name="key_move_z_down"
                                   value="{{config.controller.key_move_z_down}}"
                       hx-get="/api/validate/keyboard/key_move_z_down" hx-trigger="blur changed delay:200ms"
                       hx-target="#keyboard-key-move-z-down-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="keyboard-key-move-z-down-error" aria-invalid="false">
                <span id="keyboard-key-move-z-down-error" class="field-error"></span>
                        </div>
                    </div>
                </div>
                <div class="group">
                    <h3 class="group-title">Actions</h3>
                    <div class="row">
                        <div class="field">
                            <label for="keyboard-key-reset">{{kb_labels['key_reset']}}</label>
                            <input id="keyboard-key-reset" type="text" name="key_reset"
                                   value="{{config.controller.key_reset}}"
                       hx-get="/api/validate/keyboard/key_reset" hx-trigger="blur changed delay:200ms"
                       hx-target="#keyboard-key-reset-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="keyboard-key-reset-error" aria-invalid="false">
                <span id="keyboard-key-reset-error" class="field-error"></span>
                        </div>
                        <div class="field">
                            <label for="keyboard-key-toggle-help">{{kb_labels['key_toggle_help']}}</label>
                            <input id="keyboard-key-toggle-help" type="text" name="key_toggle_help"
                                   value="{{config.controller.key_toggle_help}}"
                       hx-get="/api/validate/keyboard/key_toggle_help" hx-trigger="blur changed delay:200ms"
                       hx-target="#keyboard-key-toggle-help-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="keyboard-key-toggle-help-error" aria-invalid="false">
                <span id="keyboard-key-toggle-help-error" class="field-error"></span>
                        </div>
                        <div class="field" data-feature="zones">
                            <label for="keyboard-key-toggle-zones">{{kb_labels['key_toggle_zones']}}</label>
                            <input id="keyboard-key-toggle-zones" type="text" name="key_toggle_zones"
                                   value="{{config.controller.key_toggle_zones}}"
                       hx-get="/api/validate/keyboard/key_toggle_zones" hx-trigger="blur changed delay:200ms"
                       hx-target="#keyboard-key-toggle-zones-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="keyboard-key-toggle-zones-error" aria-invalid="false">
                <span id="keyboard-key-toggle-zones-error" class="field-error"></span>
                        </div>
                        <div class="field">

                            <label for="keyboard-key-speed-down">{{kb_labels['key_speed_down']}}</label>
                            <input id="keyboard-key-speed-down" type="text" name="key_speed_down"
                                   value="{{config.controller.key_speed_down}}"
                       hx-get="/api/validate/keyboard/key_speed_down" hx-trigger="blur changed delay:200ms"
                       hx-target="#keyboard-key-speed-down-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="keyboard-key-speed-down-error" aria-invalid="false">
                <span id="keyboard-key-speed-down-error" class="field-error"></span>
                        </div>
                        <div class="field">
                            <label for="keyboard-key-speed-up">{{kb_labels['key_speed_up']}}</label>
                            <input id="keyboard-key-speed-up" type="text" name="key_speed_up"
                                   value="{{config.controller.key_speed_up}}"
                       hx-get="/api/validate/keyboard/key_speed_up" hx-trigger="blur changed delay:200ms"
                       hx-target="#keyboard-key-speed-up-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="keyboard-key-speed-up-error" aria-invalid="false">
                <span id="keyboard-key-speed-up-error" class="field-error"></span>
                        </div>
                    </div>
                    <div class="row">
                        <div class="field">
                            <label for="keyboard-key-settings">{{kb_labels['key_settings']}}</label>
                            <input id="keyboard-key-settings" type="text" name="key_settings"
                                   value="{{config.controller.key_settings}}"
                       hx-get="/api/validate/keyboard/key_settings" hx-trigger="blur changed delay:200ms"
                       hx-target="#keyboard-key-settings-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="keyboard-key-settings-error" aria-invalid="false">
                <span id="keyboard-key-settings-error" class="field-error"></span>
                        </div>
                        <div class="field">
                            <label for="keyboard-key-clear-messages">{{kb_labels['key_clear_messages']}}</label>
                            <input id="keyboard-key-clear-messages" type="text" name="key_clear_messages"
                                   value="{{config.controller.key_clear_messages}}"
                       hx-get="/api/validate/keyboard/key_clear_messages" hx-trigger="blur changed delay:200ms"
                       hx-target="#keyboard-key-clear-messages-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="keyboard-key-clear-messages-error" aria-invalid="false">
                <span id="keyboard-key-clear-messages-error" class="field-error"></span>
                        </div>
                        <div class="field">
                            <label for="keyboard-key-next-marker">{{kb_labels['key_next_marker']}}</label>
                            <input id="keyboard-key-next-marker" type="text" name="key_next_marker"
                                   value="{{config.controller.key_next_marker}}"
                       hx-get="/api/validate/keyboard/key_next_marker" hx-trigger="blur changed delay:200ms"
                       hx-target="#keyboard-key-next-marker-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="keyboard-key-next-marker-error" aria-invalid="false">
                <span id="keyboard-key-next-marker-error" class="field-error"></span>
                        </div>
                        <div class="field">
                            <label for="keyboard-key-prev-marker">{{kb_labels['key_prev_marker']}}</label>
                            <input id="keyboard-key-prev-marker" type="text" name="key_prev_marker"
                                   value="{{config.controller.key_prev_marker}}"
                       hx-get="/api/validate/keyboard/key_prev_marker" hx-trigger="blur changed delay:200ms"
                       hx-target="#keyboard-key-prev-marker-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="keyboard-key-prev-marker-error" aria-invalid="false">
                <span id="keyboard-key-prev-marker-error" class="field-error"></span>
                        </div>
                    </div>
                    <div class="row" data-feature="paths">
                        <div class="field">
                            <label for="keyboard-key-pathfinder">{{kb_labels['key_pathfinder']}}</label>
                            <input id="keyboard-key-pathfinder" type="text" name="key_pathfinder"
                                   value="{{config.controller.key_pathfinder}}"
                       hx-get="/api/validate/keyboard/key_pathfinder" hx-trigger="blur changed delay:200ms"
                       hx-target="#keyboard-key-pathfinder-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="keyboard-key-pathfinder-error" aria-invalid="false">
                <span id="keyboard-key-pathfinder-error" class="field-error"></span>
                        </div>
                        <div class="field">
                            <label for="keyboard-key-toggle-paths">{{kb_labels['key_toggle_paths']}}</label>
                            <input id="keyboard-key-toggle-paths" type="text" name="key_toggle_paths"
                                   value="{{config.controller.key_toggle_paths}}"
                       hx-get="/api/validate/keyboard/key_toggle_paths" hx-trigger="blur changed delay:200ms"
                       hx-target="#keyboard-key-toggle-paths-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="keyboard-key-toggle-paths-error" aria-invalid="false">
                <span id="keyboard-key-toggle-paths-error" class="field-error"></span>
                        </div>
                    </div>
                </div>
            </div>
        </details>
    </div>

    <div class="actions">
        <button type="submit" class="save-btn">Save</button>
        % if kb_pending:
        <button type="button" class="danger"
                onclick="openfollowRestart({title: 'Restart OpenFollow?', message: 'Outputs pause until OpenFollow is back.'})">Restart OpenFollow</button>
        % end
    </div>
</form>
