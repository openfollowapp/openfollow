% from openfollow.configuration import MOUSE3D_AXIS_FORM_LABELS, MOUSE3D_AXIS_TARGETS, MOUSE3D_BUTTON_FORM_LABELS, VALID_CURVES
% m = config.mouse3d
<form id="mouse3d-section" class="section {{'saved' if defined('saved') and saved else ''}}" data-fold-key="mouse3d" data-help="mouse3d"
      hx-post="/section/mouse3d" hx-target="#mouse3d-section" hx-swap="outerHTML" hx-trigger="submit">
    <div class="section-head">
        <h2>3D Mouse Input</h2>
    </div>
    % include('partials/mouse3d_status.tpl', mouse3d_status=mouse3d_status)

    <div class="group">
        <div class="row">
            <div class="field checkbox-field">
                <label for="m3d-enabled">3D Mouse</label>
                <div class="checkbox-wrap checkbox-field inline">
                    <input type="checkbox" id="m3d-enabled" name="enabled" {{'checked' if m.enabled else ''}}>
                    <label for="m3d-enabled">Steer markers with a 3D Mouse</label>
                </div>
            </div>
            <div class="field">
                <label for="m3d-curve">Response curve</label>
                <select id="m3d-curve" name="curve">
                    % for c in VALID_CURVES:
                    <option value="{{c}}" {{'selected' if m.curve == c else ''}}>{{c}}</option>
                    % end
                </select>
            </div>
        </div>
    </div>

    <div class="group">
        <h3 class="group-title">Axis Mapping</h3>
        % for axis, label in MOUSE3D_AXIS_FORM_LABELS:
        <div class="row">
            <div class="field">
                <label for="m3d-map-{{axis}}" id="m3d-map-{{axis}}-label">{{label}}</label>
                <select id="m3d-map-{{axis}}" name="map_{{axis}}">
                    % for t in MOUSE3D_AXIS_TARGETS:
                    <option value="{{t}}" {{'selected' if getattr(m, 'map_' + axis) == t else ''}}>{{t}}</option>
                    % end
                </select>
            </div>
            % for idp, nm, lo, hi, lbl in (("sens", "sens", 0, 10, "Sensitivity"), ("dz", "deadzone", 0, 1, "Deadzone")):
            <div class="field">
                <label for="m3d-{{idp}}-{{axis}}">{{lbl}}</label>
                <input id="m3d-{{idp}}-{{axis}}" type="number" name="{{nm}}_{{axis}}" value="{{getattr(m, nm + '_' + axis)}}" min="{{lo}}" max="{{hi}}" step="any"
                       hx-get="/api/validate/mouse3d/{{nm}}_{{axis}}" hx-trigger="blur changed delay:200ms"
                       hx-target="#m3d-{{idp}}-{{axis}}-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="m3d-{{idp}}-{{axis}}-error" aria-invalid="false">
                <span id="m3d-{{idp}}-{{axis}}-error" class="field-error"></span>
            </div>
            % end
            <div class="field">
                <label for="m3d-invert-{{axis}}" id="m3d-invert-{{axis}}-label">Direction</label>
                <select id="m3d-invert-{{axis}}" name="invert_{{axis}}" aria-labelledby="m3d-map-{{axis}}-label m3d-invert-{{axis}}-label">
                    <option value="false" {{'' if getattr(m, 'invert_' + axis) else 'selected'}}>Normal</option>
                    <option value="true" {{'selected' if getattr(m, 'invert_' + axis) else ''}}>Inverted</option>
                </select>
            </div>
        </div>
        % end
    </div>

    <div class="group">
        <h3 class="group-title">Buttons</h3>
        % for i in range(0, len(MOUSE3D_BUTTON_FORM_LABELS), 2):
        <div class="row">
            % for field, label in MOUSE3D_BUTTON_FORM_LABELS[i:i + 2]:
            <div class="field">
                <label for="m3d-{{field}}">{{label}}</label>
                <div class="detect-input">
                    <input id="m3d-{{field}}" type="number" name="{{field}}" value="{{getattr(m, field) if getattr(m, field) >= 0 else ''}}" min="0" step="1" placeholder="none"
                           hx-get="/api/validate/mouse3d/{{field}}" hx-trigger="blur changed delay:200ms"
                           hx-target="#m3d-{{field}}-error" hx-swap="innerHTML" hx-include="closest form"
                           aria-describedby="m3d-{{field}}-error" aria-invalid="false">
                    <button type="button" class="secondary small detect-btn" data-detect-input="m3d-{{field}}" data-detect-url="/section/mouse3d/detect">Detect</button>
                </div>
                <span id="m3d-{{field}}-error" class="field-error"></span>
            </div>
            % end
        </div>
        % end
    </div>

    <div class="actions">
        <button type="submit" class="save-btn">Save</button>
    </div>
</form>
