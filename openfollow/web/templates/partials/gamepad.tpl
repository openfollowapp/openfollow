% from openfollow.web.labels import pretty_label
<form id="gamepad-section" class="section {{'saved' if defined('saved') and saved else ''}}" data-fold-key="gamepad" data-help="gamepad"
      hx-post="/section/gamepad" hx-target="#gamepad-section" hx-swap="outerHTML" hx-trigger="submit">
    <div class="section-head">
        <h2>{{_('Gamepad Input')}}</h2>
        <span class="section-note">{{_('Input behavior for game controllers')}}</span>
    </div>

    <div class="group">
        <div class="row">
            <div class="field checkbox-field">
                <label>{{_('Enabled')}}</label>
                <div class="checkbox-wrap"><input type="checkbox" name="enabled" {{'checked' if config.controller.enabled else ''}}></div>
            </div>
            <div class="field checkbox-field">
                <label>{{_('Invert Y/Z Mapping')}}</label>
                <div class="checkbox-wrap"><input type="checkbox" name="invert_y" {{'checked' if config.controller.invert_y else ''}}></div>
            </div>
            <div class="field">
                <label>{{_('Axis Deadzone (0–1)')}}</label>
                <input id="gamepad-deadzone" type="number" name="deadzone" value="{{config.controller.deadzone}}" min="0" max="1" step="0.01"
                       hx-get="/api/validate/gamepad/deadzone" hx-trigger="blur changed delay:200ms"
                       hx-target="#gamepad-deadzone-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="gamepad-deadzone-error" aria-invalid="false">
                <span id="gamepad-deadzone-error" class="field-error"></span>
            </div>
            <div class="field">
                <label>{{_('Response Curve')}}</label>
                <select name="curve">
                    <option value="linear" {{'selected' if config.controller.curve == 'linear' else ''}}>{{_('Linear')}}</option>
                    <option value="logarithmic" {{'selected' if config.controller.curve == 'logarithmic' else ''}}>{{_('Logarithmic')}}</option>
                    <option value="quadratic" {{'selected' if config.controller.curve == 'quadratic' else ''}}>{{_('Quadratic')}}</option>
                    <option value="s-law" {{'selected' if config.controller.curve == 's-law' else ''}}>{{_('S-Law')}}</option>
                </select>
            </div>
        </div>

        <details class="inline-advanced" data-adv-key="button-detection">
            <summary>{{_('Button Detection Map')}}</summary>
            <div class="inline-advanced-content">
                <p class="field-note" style="margin:0 0 0.5rem;">
                    {{_('Run the wizard on the app display to detect your controller\'s button layout.')}}
                </p>
                <div style="margin-bottom:0.75rem;">
                    <button type="button" class="save-btn small"
                            hx-post="/section/gamepad/detect-buttons"
                            hx-target="#gamepad-section" hx-swap="outerHTML">
                        {{_('Start Button Detection Wizard')}}
                    </button>
                    % if defined('detection_started') and detection_started:
                    <span style="color:var(--accent);font-size:0.8em;margin-left:0.5rem;"
                          hx-get="/section/gamepad" hx-target="#gamepad-section"
                          hx-swap="outerHTML" hx-trigger="every 2s">{{_('Wizard running on app display')}}</span>
                    %# Allow web UI cancellation so keyboardless operator
                    %# isn't stranded after wizard grabs exclusive input.
                    <button type="button" class="secondary small"
                            style="margin-left:0.5rem;"
                            hx-post="/section/gamepad/cancel-button-detection"
                            hx-target="#gamepad-section" hx-swap="outerHTML">
                        {{_('Cancel wizard')}}
                    </button>
                    % elif config.controller.mapped_controller_name:
                    <span style="color:var(--muted);font-size:0.8em;margin-left:0.5rem;">{{_('Mapped with')}} {{config.controller.mapped_controller_name}}</span>
                    % end
                </div>
                <%
                    detection_entries = [
                        ('A', 'map_a', 'A'), ('B', 'map_b', 'B'), ('X', 'map_x', 'X'), ('Y', 'map_y', 'Y'),
                        ('LB', 'map_lb', 'LB'), ('RB', 'map_rb', 'RB'),
                        ('LT', None, 'LT'), ('RT', None, 'RT'),
                        ('Back', 'map_back', 'BACK'), ('Start', 'map_start', 'START'),
                        ('D-Pad Up', 'map_dpad_up', 'DPAD_UP'), ('D-Pad Down', 'map_dpad_down', 'DPAD_DOWN'),
                        ('D-Pad Left', 'map_dpad_left', 'DPAD_LEFT'), ('D-Pad Right', 'map_dpad_right', 'DPAD_RIGHT'),
                    ]
                    _hat_names = {-1: 'hat Up', -2: 'hat Down', -3: 'hat Left', -4: 'hat Right'}
                %>
                <table style="width:100%;font-size:0.82em;border-collapse:collapse;">
                    <thead>
                        <tr style="color:var(--muted);text-align:left;">
                            <th style="padding:0.25rem 0.5rem;border-bottom:1px solid var(--border);">{{_('Physical Button')}}</th>
                            <th style="padding:0.25rem 0.5rem;border-bottom:1px solid var(--border);">{{_('Detected As')}}</th>
                            <th style="padding:0.25rem 0.5rem;border-bottom:1px solid var(--border);">{{_('Raw ID')}}</th>
                        </tr>
                    </thead>
                    <tbody>
                        % for label, field, wiz_key in detection_entries:
                        <%
                            val = '' if field is None else getattr(config.controller, field)
                            is_default = True if field is None else (val == field.replace('map_', '').upper())
                            val_display = '–' if field is None else val
                            raw_idx = config.controller.button_raw_indices.get(wiz_key)
                            raw_display = ('–' if raw_idx is None else 'axis ' + str(-100 - raw_idx) if raw_idx <= -100 else _hat_names.get(raw_idx, 'hat ' + str(raw_idx)) if raw_idx < 0 else 'btn ' + str(raw_idx))
                        %>
                        <tr>
                            <td style="padding:0.2rem 0.5rem;">{{pretty_label(wiz_key)}}</td>
                            <td style="padding:0.2rem 0.5rem;{{'color:var(--accent);font-weight:600;' if not is_default else 'color:var(--muted);'}}">{{pretty_label(val_display)}}</td>
                            <td style="padding:0.2rem 0.5rem;color:var(--muted);font-family:monospace;">{{raw_display}}</td>
                        </tr>
                        % if field is not None:
                        <input type="hidden" name="{{field}}" value="{{val}}">
                        % end
                        % end
                    </tbody>
                </table>
                <div style="margin-top:0.5rem;font-size:0.82em;">
                    <span style="color:var(--muted);">{{_('LT / RT Triggers:')}}</span>
                    <span style="{{'color:var(--accent);font-weight:600;' if config.controller.swap_triggers else 'color:var(--muted);'}}">
                        {{_('Swapped') if config.controller.swap_triggers else _('Normal')}}
                    </span>
                    <input type="hidden" name="swap_triggers" value="{{'on' if config.controller.swap_triggers else ''}}">
                </div>
            </div>
        </details>

        <details class="inline-advanced" data-adv-key="button-mapping">
            <summary>{{_('Button Mapping')}}</summary>
            <div class="inline-advanced-content">
                <div style="margin-bottom:0.75rem;">
                    <button type="button" class="save-btn small" onclick="resetButtonMappingDefaults(this.closest('.inline-advanced-content'))">
                        {{_('Reset to Defaults')}}
                    </button>
                </div>
                <div class="group">
                    <h3 class="group-title">{{_('Normal Mode')}}</h3>
                    <div class="row">
                        <div class="field">
                            <label>{{_('Reset Marker')}}</label>
                            <select name="btn_reset">
                                <option value="" {{'selected' if not config.controller.btn_reset else ''}}>{{_('–')}}</option>
                                % for btn in button_names:
                                <option value="{{btn}}" {{'selected' if config.controller.btn_reset == btn else ''}}>{{pretty_label(btn)}}</option>
                                % end
                            </select>
                        </div>
                        <div class="field">
                            <label>{{_('Toggle Help')}}</label>
                            <select name="btn_toggle_help">
                                <option value="" {{'selected' if not config.controller.btn_toggle_help else ''}}>{{_('–')}}</option>
                                % for btn in button_names:
                                <option value="{{btn}}" {{'selected' if config.controller.btn_toggle_help == btn else ''}}>{{pretty_label(btn)}}</option>
                                % end
                            </select>
                        </div>
                        <div class="field">
                            <label>{{_('Toggle Zone Overlay')}}</label>
                            <select name="btn_toggle_zones">
                                <option value="" {{'selected' if not config.controller.btn_toggle_zones else ''}}>{{_('–')}}</option>
                                % for btn in button_names:
                                <option value="{{btn}}" {{'selected' if config.controller.btn_toggle_zones == btn else ''}}>{{pretty_label(btn)}}</option>
                                % end
                            </select>
                        </div>
                        <div class="field">
                            <label>{{_('Settings Menu')}}</label>
                            <select name="btn_settings">
                                <option value="" {{'selected' if not config.controller.btn_settings else ''}}>{{_('–')}}</option>
                                % for btn in button_names:
                                <option value="{{btn}}" {{'selected' if config.controller.btn_settings == btn else ''}}>{{pretty_label(btn)}}</option>
                                % end
                            </select>
                        </div>
                    </div>
                    <div class="row">
                        <div class="field">
                            <label>{{_('Move X/Y')}}</label>
                            <select name="move_xy_stick">
                                <option value="left" {{'selected' if config.controller.move_xy_stick == 'left' else ''}}>{{_('Left Stick')}}</option>
                                <option value="right" {{'selected' if config.controller.move_xy_stick == 'right' else ''}}>{{_('Right Stick')}}</option>
                            </select>
                        </div>
                        <!-- Marker-fader stick selector. Picks which stick Y
                             axis (if any) drives the fader of the marker this
                             controller currently controls. Existing deadzone +
                             curve apply to the deflection (no new fields). -->
                        <div class="field">
                            <label>{{_('Marker fader stick')}}</label>
                            <select name="marker_fader_stick">
                                <option value="" {{'selected' if not config.controller.marker_fader_stick else ''}}>{{_('– (unused)')}}</option>
                                <option value="left_y" {{'selected' if config.controller.marker_fader_stick == 'left_y' else ''}}>{{_('Left Stick Y')}}</option>
                                <option value="right_y" {{'selected' if config.controller.marker_fader_stick == 'right_y' else ''}}>{{_('Right Stick Y')}}</option>
                            </select>
                        </div>
                        <div class="field">
                            <label>{{_('Marker fader speed (s)')}}</label>
                            <input id="gamepad-marker-fader-speed" type="number" name="marker_fader_max_speed_s"
                                   value="{{config.controller.marker_fader_max_speed_s}}" min="0.05" max="60" step="0.05"
                                   hx-get="/api/validate/gamepad/marker_fader_max_speed_s" hx-trigger="blur changed delay:200ms"
                                   hx-target="#gamepad-marker-fader-speed-error" hx-swap="innerHTML" hx-include="closest form"
                                   aria-describedby="gamepad-marker-fader-speed-error" aria-invalid="false">
                            <span id="gamepad-marker-fader-speed-error" class="field-error"></span>
                        </div>
                    </div>
                    <div class="row">
                        <div class="field">
                            <label>{{_('Speed -')}}</label>
                            <select name="btn_speed_down">
                                <option value="" {{'selected' if not config.controller.btn_speed_down else ''}}>{{_('–')}}</option>
                                % for btn in button_names:
                                <option value="{{btn}}" {{'selected' if config.controller.btn_speed_down == btn else ''}}>{{pretty_label(btn)}}</option>
                                % end
                            </select>
                        </div>
                        <div class="field">
                            <label>{{_('Speed +')}}</label>
                            <select name="btn_speed_up">
                                <option value="" {{'selected' if not config.controller.btn_speed_up else ''}}>{{_('–')}}</option>
                                % for btn in button_names:
                                <option value="{{btn}}" {{'selected' if config.controller.btn_speed_up == btn else ''}}>{{pretty_label(btn)}}</option>
                                % end
                            </select>
                        </div>
                        <div class="field">
                            <label>{{_('Move Z-')}}</label>
                            <select name="btn_move_z_down">
                                <option value="" {{'selected' if not config.controller.btn_move_z_down else ''}}>{{_('–')}}</option>
                                % for btn in button_names:
                                <option value="{{btn}}" {{'selected' if config.controller.btn_move_z_down == btn else ''}}>{{pretty_label(btn)}}</option>
                                % end
                            </select>
                        </div>
                        <div class="field">
                            <label>{{_('Move Z+')}}</label>
                            <select name="btn_move_z_up">
                                <option value="" {{'selected' if not config.controller.btn_move_z_up else ''}}>{{_('–')}}</option>
                                % for btn in button_names:
                                <option value="{{btn}}" {{'selected' if config.controller.btn_move_z_up == btn else ''}}>{{pretty_label(btn)}}</option>
                                % end
                            </select>
                        </div>
                    </div>
                    <div class="row">
                        <div class="field">
                            <label>{{_('Next Marker')}}</label>
                            <select name="btn_next_marker">
                                <option value="" {{'selected' if not config.controller.btn_next_marker else ''}}>{{_('–')}}</option>
                                % for btn in button_names:
                                <option value="{{btn}}" {{'selected' if config.controller.btn_next_marker == btn else ''}}>{{pretty_label(btn)}}</option>
                                % end
                            </select>
                        </div>
                        <div class="field">
                            <label>{{_('Prev Marker')}}</label>
                            <select name="btn_prev_marker">
                                <option value="" {{'selected' if not config.controller.btn_prev_marker else ''}}>{{_('–')}}</option>
                                % for btn in button_names:
                                <option value="{{btn}}" {{'selected' if config.controller.btn_prev_marker == btn else ''}}>{{pretty_label(btn)}}</option>
                                % end
                            </select>
                        </div>
                        <div class="field">
                            <label>{{_('Clear Messages')}}</label>
                            <select name="btn_clear_messages">
                                <option value="" {{'selected' if not config.controller.btn_clear_messages else ''}}>{{_('–')}}</option>
                                % for btn in button_names:
                                <option value="{{btn}}" {{'selected' if config.controller.btn_clear_messages == btn else ''}}>{{pretty_label(btn)}}</option>
                                % end
                            </select>
                        </div>
                    </div>
                </div>
                <div class="group">
                    <h3 class="group-title">{{_('Menu Navigation')}}</h3>
                    <p class="field-note" style="margin:0 0 0.5rem;">
                        {{_('Shared by the Settings menu, source / interface selection, and calibration apply/cancel.')}}
                    </p>
                    <div class="row">
                        <div class="field">
                            <label>{{_('Confirm')}}</label>
                            <select name="btn_menu_confirm">
                                <option value="" {{'selected' if not config.controller.btn_menu_confirm else ''}}>{{_('–')}}</option>
                                % for btn in button_names:
                                <option value="{{btn}}" {{'selected' if config.controller.btn_menu_confirm == btn else ''}}>{{pretty_label(btn)}}</option>
                                % end
                            </select>
                        </div>
                        <div class="field">
                            <label>{{_('Cancel')}}</label>
                            <select name="btn_menu_cancel">
                                <option value="" {{'selected' if not config.controller.btn_menu_cancel else ''}}>{{_('–')}}</option>
                                % for btn in button_names:
                                <option value="{{btn}}" {{'selected' if config.controller.btn_menu_cancel == btn else ''}}>{{pretty_label(btn)}}</option>
                                % end
                            </select>
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
