<form id="keyboard-section" class="section {{'saved' if defined('saved') and saved else ''}}" data-fold-key="keyboard" data-help="keyboard"
      hx-post="/section/keyboard" hx-target="#keyboard-section" hx-swap="outerHTML" hx-trigger="submit">
    <div class="section-head">
        <h2>{{_('Keyboard Input')}}</h2>
        <span class="section-note">{{_('Keyboard controls for the on-display UI')}}</span>
    </div>

    <div class="group">
        <div class="row">
            <div class="field checkbox-field">
                <label>{{_('Enabled')}}</label>
                <div class="checkbox-wrap"><input type="checkbox" name="keyboard_enabled" {{'checked' if config.controller.keyboard_enabled else ''}}></div>
            </div>
        </div>

        <details class="inline-advanced" data-adv-key="keyboard-mapping">
            <summary>{{_('Button Mapping')}}</summary>
            <div class="inline-advanced-content">
                <div style="margin-bottom:0.75rem;">
                    <button type="button" class="save-btn small" onclick="resetKeyboardMappingDefaults(this.closest('.inline-advanced-content'))">
                        {{_('Reset to Defaults')}}
                    </button>
                </div>
                <div class="group">
                    <h3 class="group-title">{{_('Movement')}}</h3>
                    <div class="row">
                        <div class="field">
                            <label>{{_('X / Y Layout')}}</label>
                            <select name="key_move_layout">
                                <option value="wasd" {{'selected' if config.controller.key_move_layout == 'wasd' else ''}}>{{_('WASD')}}</option>
                                <option value="ijkl" {{'selected' if config.controller.key_move_layout == 'ijkl' else ''}}>{{_('IJKL')}}</option>
                                <option value="numpad" {{'selected' if config.controller.key_move_layout == 'numpad' else ''}}>{{_('Numpad (8/4/2/6)')}}</option>
                            </select>
                            <span class="field-note">{{_('Arrow keys are reserved for on-display menu navigation.')}}</span>
                        </div>
                        <div class="field">
                            <label>{{_('Z+')}}</label>
                            <input id="keyboard-key-move-z-up" type="text" name="key_move_z_up"
                                   value="{{config.controller.key_move_z_up}}"
                       hx-get="/api/validate/keyboard/key_move_z_up" hx-trigger="blur changed delay:200ms"
                       hx-target="#keyboard-key-move-z-up-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="keyboard-key-move-z-up-error" aria-invalid="false">
                <span id="keyboard-key-move-z-up-error" class="field-error"></span>
                        </div>
                        <div class="field">
                            <label>{{_('Z-')}}</label>
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
                    <h3 class="group-title">{{_('Actions')}}</h3>
                    <div class="row">
                        <div class="field">
                            <label>{{_('Reset Marker')}}</label>
                            <input id="keyboard-key-reset" type="text" name="key_reset"
                                   value="{{config.controller.key_reset}}"
                       hx-get="/api/validate/keyboard/key_reset" hx-trigger="blur changed delay:200ms"
                       hx-target="#keyboard-key-reset-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="keyboard-key-reset-error" aria-invalid="false">
                <span id="keyboard-key-reset-error" class="field-error"></span>
                        </div>
                        <div class="field">
                            <label>{{_('Toggle Help')}}</label>
                            <input id="keyboard-key-toggle-help" type="text" name="key_toggle_help"
                                   value="{{config.controller.key_toggle_help}}"
                       hx-get="/api/validate/keyboard/key_toggle_help" hx-trigger="blur changed delay:200ms"
                       hx-target="#keyboard-key-toggle-help-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="keyboard-key-toggle-help-error" aria-invalid="false">
                <span id="keyboard-key-toggle-help-error" class="field-error"></span>
                        </div>
                        <div class="field">
                            <label>{{_('Toggle Zone Overlay')}}</label>
                            <input id="keyboard-key-toggle-zones" type="text" name="key_toggle_zones"
                                   value="{{config.controller.key_toggle_zones}}"
                                   pattern="(?!^[wasdefWASDEF]$).*"
                                   title="{{_('Cannot be W, A, S, D, E, or F (reserved for movement)')}}"
                       hx-get="/api/validate/keyboard/key_toggle_zones" hx-trigger="blur changed delay:200ms"
                       hx-target="#keyboard-key-toggle-zones-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="keyboard-key-toggle-zones-error" aria-invalid="false">
                <span id="keyboard-key-toggle-zones-error" class="field-error"></span>
                        </div>
                        <div class="field">
                            <label>{{_('Speed -')}}</label>
                            <input id="keyboard-key-speed-down" type="text" name="key_speed_down"
                                   value="{{config.controller.key_speed_down}}"
                       hx-get="/api/validate/keyboard/key_speed_down" hx-trigger="blur changed delay:200ms"
                       hx-target="#keyboard-key-speed-down-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="keyboard-key-speed-down-error" aria-invalid="false">
                <span id="keyboard-key-speed-down-error" class="field-error"></span>
                        </div>
                        <div class="field">
                            <label>{{_('Speed +')}}</label>
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
                            <label>{{_('Settings Menu')}}</label>
                            <input id="keyboard-key-settings" type="text" name="key_settings"
                                   value="{{config.controller.key_settings}}"
                       hx-get="/api/validate/keyboard/key_settings" hx-trigger="blur changed delay:200ms"
                       hx-target="#keyboard-key-settings-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="keyboard-key-settings-error" aria-invalid="false">
                <span id="keyboard-key-settings-error" class="field-error"></span>
                        </div>
                        <div class="field">
                            <label>{{_('Clear Messages')}}</label>
                            <input id="keyboard-key-clear-messages" type="text" name="key_clear_messages"
                                   value="{{config.controller.key_clear_messages}}"
                       hx-get="/api/validate/keyboard/key_clear_messages" hx-trigger="blur changed delay:200ms"
                       hx-target="#keyboard-key-clear-messages-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="keyboard-key-clear-messages-error" aria-invalid="false">
                <span id="keyboard-key-clear-messages-error" class="field-error"></span>
                        </div>
                        <div class="field">
                            <label>{{_('Next Marker')}}</label>
                            <input id="keyboard-key-next-marker" type="text" name="key_next_marker"
                                   value="{{config.controller.key_next_marker}}"
                       hx-get="/api/validate/keyboard/key_next_marker" hx-trigger="blur changed delay:200ms"
                       hx-target="#keyboard-key-next-marker-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="keyboard-key-next-marker-error" aria-invalid="false">
                <span id="keyboard-key-next-marker-error" class="field-error"></span>
                        </div>
                        <div class="field">
                            <label>{{_('Prev Marker')}}</label>
                            <input id="keyboard-key-prev-marker" type="text" name="key_prev_marker"
                                   value="{{config.controller.key_prev_marker}}"
                       hx-get="/api/validate/keyboard/key_prev_marker" hx-trigger="blur changed delay:200ms"
                       hx-target="#keyboard-key-prev-marker-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="keyboard-key-prev-marker-error" aria-invalid="false">
                <span id="keyboard-key-prev-marker-error" class="field-error"></span>
                        </div>
                    </div>
                </div>
            </div>
        </details>
    </div>

    <div class="actions">
        <button type="submit" class="save-btn">{{_('Save')}}</button>
    </div>
</form>
