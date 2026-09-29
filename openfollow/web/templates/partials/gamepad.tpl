% from openfollow.configuration import GAMEPAD_ACTION_BUTTON_FORM_LABELS, GAMEPAD_MENU_BUTTON_FORM_LABELS, MENU_RESERVED_BUTTONS
% from openfollow.web.bindings import STICK_FIELD_LABELS
% from openfollow.web.labels import pretty_label
<form id="gamepad-section" class="section {{'saved' if defined('saved') and saved else ''}}" data-fold-key="gamepad" data-help="gamepad"
      hx-post="/section/gamepad" hx-target="#gamepad-section" hx-swap="outerHTML" hx-trigger="submit">
    <div class="section-head">
        <h2>Gamepad Input</h2>
    </div>

    <div class="group">
        <div class="row">
            <div class="field checkbox-field">
                <label>Enabled</label>
                <div class="checkbox-wrap"><input type="checkbox" name="enabled" {{'checked' if config.controller.enabled else ''}}></div>
            </div>
            <div class="field checkbox-field">
                <label>Invert Stick Y</label>
                <div class="checkbox-wrap"><input type="checkbox" name="invert_y" {{'checked' if config.controller.invert_y else ''}}></div>
            </div>
            <div class="field">
                <label>Axis Deadzone (0–1)</label>
                <input id="gamepad-deadzone" type="number" name="deadzone" value="{{config.controller.deadzone}}" min="0" max="1" step="0.01"
                       hx-get="/api/validate/gamepad/deadzone" hx-trigger="blur changed delay:200ms"
                       hx-target="#gamepad-deadzone-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="gamepad-deadzone-error" aria-invalid="false">
                <span id="gamepad-deadzone-error" class="field-error"></span>
            </div>
            <div class="field">
                <label>Response Curve</label>
                <select name="curve">
                    <option value="linear" {{'selected' if config.controller.curve == 'linear' else ''}}>Linear</option>
                    <option value="logarithmic" {{'selected' if config.controller.curve == 'logarithmic' else ''}}>Logarithmic</option>
                    <option value="quadratic" {{'selected' if config.controller.curve == 'quadratic' else ''}}>Quadratic</option>
                    <option value="s-law" {{'selected' if config.controller.curve == 's-law' else ''}}>S-Law</option>
                </select>
            </div>
        </div>

        <details class="inline-advanced" data-adv-key="button-detection">
            <summary>Button Detection Map</summary>
            <div class="inline-advanced-content">
                <p class="field-note" style="margin:0 0 0.5rem;">
                    Run the wizard on the app display to detect your controller's button layout.
                </p>
                <div style="margin-bottom:0.75rem;">
                    <button type="button" class="save-btn small"
                            hx-post="/section/gamepad/detect-buttons"
                            hx-target="#gamepad-section" hx-swap="outerHTML">
                        Start Button Detection Wizard
                    </button>
                    % if defined('detection_started') and detection_started:
                    <span style="color:var(--accent);font-size:0.8em;margin-left:0.5rem;"
                          hx-get="/section/gamepad" hx-target="#gamepad-section"
                          hx-swap="outerHTML" hx-trigger="every 2s">Wizard running on app display</span>
                    %# Allow web UI cancellation so keyboardless operator
                    %# isn't stranded after wizard grabs exclusive input.
                    <button type="button" class="secondary small"
                            style="margin-left:0.5rem;"
                            hx-post="/section/gamepad/cancel-button-detection"
                            hx-target="#gamepad-section" hx-swap="outerHTML">
                        Cancel wizard
                    </button>
                    % elif config.controller.mapped_controller_name:
                    <span style="color:var(--muted);font-size:0.8em;margin-left:0.5rem;">Mapped with {{config.controller.mapped_controller_name}}</span>
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
                            <th style="padding:0.25rem 0.5rem;border-bottom:1px solid var(--border);">Physical Button</th>
                            <th style="padding:0.25rem 0.5rem;border-bottom:1px solid var(--border);">Detected As</th>
                            <th style="padding:0.25rem 0.5rem;border-bottom:1px solid var(--border);">Raw ID</th>
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
                    <span style="color:var(--muted);">LT / RT Triggers:</span>
                    <span style="{{'color:var(--accent);font-weight:600;' if config.controller.swap_triggers else 'color:var(--muted);'}}">
                        {{'Swapped' if config.controller.swap_triggers else 'Normal'}}
                    </span>
                    <input type="hidden" name="swap_triggers" value="{{'on' if config.controller.swap_triggers else ''}}">
                </div>
            </div>
        </details>

        <details class="inline-advanced" data-adv-key="button-mapping">
            <summary>Button Mapping</summary>
            <div class="inline-advanced-content">
                <div style="margin-bottom:0.75rem;">
                    <button type="button" class="save-btn small" onclick="resetButtonMappingDefaults(this.closest('.inline-advanced-content'))">
                        Reset to Defaults
                    </button>
                </div>
                <%
                    action_labels = dict(GAMEPAD_ACTION_BUTTON_FORM_LABELS)
                    menu_labels = dict(GAMEPAD_MENU_BUTTON_FORM_LABELS)
                    action_buttons = [b for b in button_names if b]
                    menu_buttons = [b for b in action_buttons if b not in MENU_RESERVED_BUTTONS]
                    c = config.controller
                %>
                <div class="group">
                    <h3 class="group-title">Normal Mode</h3>
                    <div class="row">
                        % for name in ('btn_reset', 'btn_toggle_help', 'btn_toggle_zones', 'btn_settings'):
                        % include('partials/button_binding_select.tpl', name=name, label=action_labels[name], value=getattr(c, name), buttons=action_buttons)
                        % end
                    </div>
                    <div class="row">
                        <div class="field">
                            <label for="gamepad-move-xy-stick">{{STICK_FIELD_LABELS['move_xy_stick']}}</label>
                            <select id="gamepad-move-xy-stick" name="move_xy_stick"
                                    hx-get="/api/validate/gamepad/move_xy_stick" hx-trigger="change"
                                    hx-target="#gamepad-move-xy-stick-error" hx-swap="innerHTML" hx-include="closest form"
                                    aria-describedby="gamepad-move-xy-stick-error" aria-invalid="false">
                                <option value="left" {{'selected' if c.move_xy_stick == 'left' else ''}}>Left Stick</option>
                                <option value="right" {{'selected' if c.move_xy_stick == 'right' else ''}}>Right Stick</option>
                            </select>
                            <span id="gamepad-move-xy-stick-error" class="field-error"></span>
                        </div>
                        <!-- Marker-fader stick selector. Picks which stick Y
                             axis (if any) drives the fader of the marker this
                             controller currently controls. Existing deadzone +
                             curve apply to the deflection (no new fields). -->
                        <div class="field">
                            <label for="gamepad-marker-fader-stick">{{STICK_FIELD_LABELS['marker_fader_stick']}}</label>
                            <select id="gamepad-marker-fader-stick" name="marker_fader_stick"
                                    hx-get="/api/validate/gamepad/marker_fader_stick" hx-trigger="change"
                                    hx-target="#gamepad-marker-fader-stick-error" hx-swap="innerHTML" hx-include="closest form"
                                    aria-describedby="gamepad-marker-fader-stick-error" aria-invalid="false">
                                <option value="" {{'selected' if not c.marker_fader_stick else ''}}>– (unused)</option>
                                <option value="left_y" {{'selected' if c.marker_fader_stick == 'left_y' else ''}}>Left Stick Y</option>
                                <option value="right_y" {{'selected' if c.marker_fader_stick == 'right_y' else ''}}>Right Stick Y</option>
                            </select>
                            <span id="gamepad-marker-fader-stick-error" class="field-error"></span>
                        </div>
                        <div class="field">
                            <label>Marker fader speed (s)</label>
                            <input id="gamepad-marker-fader-speed" type="number" name="marker_fader_max_speed_s"
                                   value="{{c.marker_fader_max_speed_s}}" min="0.05" max="60" step="0.05"
                                   hx-get="/api/validate/gamepad/marker_fader_max_speed_s" hx-trigger="blur changed delay:200ms"
                                   hx-target="#gamepad-marker-fader-speed-error" hx-swap="innerHTML" hx-include="closest form"
                                   aria-describedby="gamepad-marker-fader-speed-error" aria-invalid="false">
                            <span id="gamepad-marker-fader-speed-error" class="field-error"></span>
                        </div>
                    </div>
                    <div class="row">
                        % for name in ('btn_speed_down', 'btn_speed_up', 'btn_move_z_down', 'btn_move_z_up'):
                        % include('partials/button_binding_select.tpl', name=name, label=action_labels[name], value=getattr(c, name), buttons=action_buttons)
                        % end
                    </div>
                    <div class="row">
                        % for name in ('btn_next_marker', 'btn_prev_marker', 'btn_clear_messages'):
                        % include('partials/button_binding_select.tpl', name=name, label=action_labels[name], value=getattr(c, name), buttons=action_buttons)
                        % end
                    </div>
                </div>
                <div class="group">
                    <h3 class="group-title">Menu Navigation</h3>
                    <div class="row">
                        % for name in ('btn_menu_confirm', 'btn_menu_cancel'):
                        % include('partials/button_binding_select.tpl', name=name, label=menu_labels[name], value=getattr(c, name), buttons=menu_buttons)
                        % end
                    </div>
                </div>
            </div>
        </details>
    </div>

    <div class="actions">
        <button type="submit" class="save-btn">Save</button>
    </div>
</form>
