% import math
% from openfollow.lens_model import C_BOUND
% from openfollow.units import UnitSystem, format_length, metric_echo, unit_suffix_length
% _us = UnitSystem(config.ui.unit_system)
% # The lens centre fields count in pixels of the live video; without one they are off and the saved centre stays.
% _vs = get('video_size')
% _hd = 0.5 * math.hypot(*_vs) if _vs else 0.0
% _cmax = math.floor(C_BOUND * _hd)
% # Whole pixels within the fields' bound, which a centre at the bound can round past.
% _cpx = lambda v: max(-_cmax, min(_cmax, round(v * _hd)))
% _imp = _us is UnitSystem.IMPERIAL
% _len = unit_suffix_length(_us)
<form id="camera-section" class="section {{'saved' if defined('saved') and saved else ''}}" data-fold-key="camera" data-help="camera"
      data-template-form="1"
      hx-post="/section/camera" hx-target="#camera-section" hx-swap="outerHTML" hx-trigger="submit">
    <div class="section-head">
        <h2>Camera</h2>
        <span class="section-note">PSN-space position and orientation</span>
    </div>

    <div class="group">
        <h3 class="group-title">Position</h3>
        <div class="row">
            <div class="field">
                <label>Position X ({{_len}})</label>
                <input id="camera-pos-x" type="{{'text' if _imp else 'number'}}" name="pos_x" value="{{format_length(config.camera.pos_x, _us) if _imp else config.camera.pos_x}}" step="any"
                       hx-get="/api/validate/camera/pos_x" hx-trigger="blur changed delay:200ms"
                       hx-target="#camera-pos-x-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="camera-pos-x-error" aria-invalid="false">
                <span id="camera-pos-x-error" class="field-error"></span>
                % if _imp:
                <small class="metric-echo">Stored: {{metric_echo(config.camera.pos_x)}}</small>
                % end
            </div>
            <div class="field">
                <label>Position Y ({{_len}})</label>
                <input id="camera-pos-y" type="{{'text' if _imp else 'number'}}" name="pos_y" value="{{format_length(config.camera.pos_y, _us) if _imp else config.camera.pos_y}}" step="any"
                       hx-get="/api/validate/camera/pos_y" hx-trigger="blur changed delay:200ms"
                       hx-target="#camera-pos-y-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="camera-pos-y-error" aria-invalid="false">
                <span id="camera-pos-y-error" class="field-error"></span>
                % if _imp:
                <small class="metric-echo">Stored: {{metric_echo(config.camera.pos_y)}}</small>
                % end
            </div>
            <div class="field">
                <label>Position Z ({{_len}})</label>
                <input id="camera-pos-z" type="{{'text' if _imp else 'number'}}" name="pos_z" value="{{format_length(config.camera.pos_z, _us) if _imp else config.camera.pos_z}}" step="any"
                       hx-get="/api/validate/camera/pos_z" hx-trigger="blur changed delay:200ms"
                       hx-target="#camera-pos-z-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="camera-pos-z-error" aria-invalid="false">
                <span id="camera-pos-z-error" class="field-error"></span>
                % if _imp:
                <small class="metric-echo">Stored: {{metric_echo(config.camera.pos_z)}}</small>
                % end
            </div>
        </div>
    </div>

    <div class="group">
        <h3 class="group-title">Orientation</h3>
        <div class="row">
            <div class="field">
                <label>Pitch (°)</label>
                <input id="camera-pitch" type="number" name="pitch" value="{{config.camera.pitch}}" step="any"
                       hx-get="/api/validate/camera/pitch" hx-trigger="blur changed delay:200ms"
                       hx-target="#camera-pitch-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="camera-pitch-error" aria-invalid="false">
                <span id="camera-pitch-error" class="field-error"></span>
            </div>
            <div class="field">
                <label>Yaw (°)</label>
                <input id="camera-yaw" type="number" name="yaw" value="{{config.camera.yaw}}" step="any"
                       hx-get="/api/validate/camera/yaw" hx-trigger="blur changed delay:200ms"
                       hx-target="#camera-yaw-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="camera-yaw-error" aria-invalid="false">
                <span id="camera-yaw-error" class="field-error"></span>
            </div>
            <div class="field">
                <label>Roll (°)</label>
                <input id="camera-roll" type="number" name="roll" value="{{config.camera.roll}}" step="any"
                       hx-get="/api/validate/camera/roll" hx-trigger="blur changed delay:200ms"
                       hx-target="#camera-roll-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="camera-roll-error" aria-invalid="false">
                <span id="camera-roll-error" class="field-error"></span>
            </div>
        </div>
    </div>

    <div class="group">
        <h3 class="group-title">Lens</h3>
        <div class="row">
            <div class="field">
                <label>Horizontal Field of View (°)</label>
                <input type="number" name="fov" value="{{config.camera.fov}}" min="1" max="179" step="any"
                       hx-get="/api/validate/camera/fov" hx-trigger="blur changed delay:200ms"
                       hx-target="#camera-fov-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="camera-fov-error" aria-invalid="false">
                <span id="camera-fov-error" class="field-error"></span>
            </div>
        </div>
    </div>

    <div class="group">
        <h3 class="group-title">Lens distortion</h3>
        <div class="row">
            <div class="field">
                <label>Barrel / fisheye</label>
                <input type="number" id="camera-lens-k1" name="lens_k1" value="{{config.camera.lens_k1}}" step="any"
                       oninput="var s=document.getElementById('camera-lens-k1-range'); if (s) s.value=this.value;"
                       hx-get="/api/validate/camera/lens_k1"
                       hx-trigger="blur changed delay:200ms, change from:#camera-lens-k1-range, change from:#camera-lens-k2, change from:#camera-lens-k2-range, change from:#camera-lens-cx-px, change from:#camera-lens-cy-px"
                       hx-target="#camera-lens-k1-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="camera-lens-k1-error" aria-invalid="false">
                <input type="range" id="camera-lens-k1-range" min="{{min(-1.0, config.camera.lens_k1)}}" max="{{max(0.5, config.camera.lens_k1)}}" step="0.005" value="{{config.camera.lens_k1}}"
                       aria-label="Barrel / fisheye slider"
                       oninput="var n=document.getElementById('camera-lens-k1'); n.value=this.value;">
                <span id="camera-lens-k1-error" class="field-error"></span>
            </div>
            <div class="field">
                <label>Corner correction</label>
                <input type="number" id="camera-lens-k2" name="lens_k2" value="{{config.camera.lens_k2}}" step="any"
                       oninput="var s=document.getElementById('camera-lens-k2-range'); if (s) s.value=this.value;"
                       hx-get="/api/validate/camera/lens_k2"
                       hx-trigger="blur changed delay:200ms, change from:#camera-lens-k2-range, change from:#camera-lens-k1, change from:#camera-lens-k1-range, change from:#camera-lens-cx-px, change from:#camera-lens-cy-px"
                       hx-target="#camera-lens-k2-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="camera-lens-k2-error" aria-invalid="false">
                <input type="range" id="camera-lens-k2-range" min="{{min(-0.8, config.camera.lens_k2)}}" max="{{max(0.3, config.camera.lens_k2)}}" step="0.005" value="{{config.camera.lens_k2}}"
                       aria-label="Corner correction slider"
                       oninput="var n=document.getElementById('camera-lens-k2'); n.value=this.value;">
                <span id="camera-lens-k2-error" class="field-error"></span>
            </div>
        </div>
        <div class="lens-centre" role="group" aria-labelledby="camera-lens-centre-title">
            <div class="lens-centre-title" id="camera-lens-centre-title">Lens centre</div>
            <div class="row">
                <div class="field">
                    <label for="camera-lens-cx-px">Right of the picture centre (px)</label>
                    <input type="number" id="camera-lens-cx-px" step="1" data-hidden="camera-lens-cx" data-half-diag="{{_hd}}"
                           {{!('min="%d" max="%d" value="%d"' % (-_cmax, _cmax, _cpx(config.camera.lens_cx))) if _vs else 'disabled placeholder="No video" value=""'}}
                           oninput="cameraLensCentreInput(this)"
                           hx-get="/api/validate/camera/lens_cx"
                           hx-trigger="blur changed delay:200ms, change from:#camera-lens-cy-px, change from:#camera-lens-k1, change from:#camera-lens-k1-range, change from:#camera-lens-k2, change from:#camera-lens-k2-range"
                           hx-target="#camera-lens-cx-error" hx-swap="innerHTML" hx-include="closest form"
                           hx-on::before-request="cameraLensCentreChecked(this, event)"
                           aria-describedby="camera-lens-cx-error" aria-invalid="false">
                    <span id="camera-lens-cx-error" class="field-error"></span>
                </div>
                <div class="field">
                    <label for="camera-lens-cy-px">Above the picture centre (px)</label>
                    <input type="number" id="camera-lens-cy-px" step="1" data-hidden="camera-lens-cy" data-half-diag="{{_hd}}"
                           {{!('min="%d" max="%d" value="%d"' % (-_cmax, _cmax, _cpx(-config.camera.lens_cy))) if _vs else 'disabled placeholder="No video" value=""'}}
                           oninput="cameraLensCentreInput(this)"
                           hx-get="/api/validate/camera/lens_cy"
                           hx-trigger="blur changed delay:200ms, change from:#camera-lens-cx-px, change from:#camera-lens-k1, change from:#camera-lens-k1-range, change from:#camera-lens-k2, change from:#camera-lens-k2-range"
                           hx-target="#camera-lens-cy-error" hx-swap="innerHTML" hx-include="closest form"
                           hx-on::before-request="cameraLensCentreChecked(this, event)"
                           aria-describedby="camera-lens-cy-error" aria-invalid="false">
                    <span id="camera-lens-cy-error" class="field-error"></span>
                </div>
            </div>
            <input type="hidden" id="camera-lens-cx" name="lens_cx" value="{{config.camera.lens_cx}}">
            <input type="hidden" id="camera-lens-cy" name="lens_cy" value="{{config.camera.lens_cy}}">
        </div>
    </div>

    % if config.camera.calibration_marks:
    % _marks = {m.id: m for m in config.camera.calibration_marks}
    <div class="group" id="camera-calibration-marks">
        <h3 class="group-title">Calibration ({{"Simple" if config.camera.calibration_mode == "simple" else "Advanced"}})</h3>
        <table class="data-table">
            <thead>
                <tr>
                    <th scope="col">Point</th>
                    <th scope="col">Kind</th>
                    <th scope="col">Position</th>
                    <th scope="col">Measured from</th>
                    <th scope="col">Pinned</th>
                </tr>
            </thead>
            <tbody>
            % for _m in config.camera.calibration_marks:
                % _at = [_m.x, _m.y, _m.z] if _m.kind == "known" else _m.position
                <tr>
                    <th scope="row">{{_m.name}}</th>
                    <td>{{"Known point" if _m.kind == "known" else "Distance point"}}</td>
                    % if _at:
                    <td>X = {{format_length(_at[0], _us)}}<br>Y = {{format_length(_at[1], _us)}}<br>Z = {{format_length(_at[2], _us)}}</td>
                    % else:
                    <td>–</td>
                    % end
                    % if _m.kind == "triangulated":
                    <td>{{_marks[_m.from_a].name}} {{format_length(_m.distance_a, _us)}}<br>{{_marks[_m.from_b].name}} {{format_length(_m.distance_b, _us)}}</td>
                    % else:
                    <td>–</td>
                    % end
                    <td>{{"Yes" if _m.pin else "No"}}</td>
                </tr>
            % end
            </tbody>
        </table>
        % if config.camera.calibration_height is not None:
        % _h = config.camera.calibration_height
        <p id="camera-calibration-height" style="margin:0;padding:0.4rem 0.6rem;font-size:var(--btn-font-sm);">Camera height: Fixed at {{format_length(_h, _us)}}</p>
        % end
    </div>
    % end

    <script>
    (function() {
        if (window.__cameraPanelLensCentreInstalled) return;
        window.__cameraPanelLensCentreInstalled = true;

        // A lens centre field's pixels, or null when it holds no whole number within its bound.
        function lensCentrePx(el) {
            var text = String(el.value).trim(), v = text === '' ? NaN : Number(text);
            return Number.isInteger(v) && Math.abs(v) <= parseFloat(el.max) ? v : null;
        }
        // Each field writes its hidden lens_cx / lens_cy in half-diagonals of the video it was shown for; up is -y.
        window.cameraLensCentreInput = function(el) {
            var px = lensCentrePx(el), hidden = document.getElementById(el.dataset.hidden);
            if (px === null || !hidden) return;
            var v = px / parseFloat(el.dataset.halfDiag);
            hidden.value = String(el.dataset.hidden === 'camera-lens-cy' ? -v || 0 : v);
        };
        // A field without a number in range says so in pixels and keeps its hidden value; the station checks the rest.
        window.cameraLensCentreChecked = function(el, event) {
            if (el.disabled || lensCentrePx(el) !== null) return;
            event.preventDefault();
            var span = document.getElementById(el.getAttribute('aria-describedby'));
            span.innerHTML = '<span class="field-error-msg" role="alert" aria-live="assertive">Must be a whole number from −' + el.max + ' to ' + el.max + '.</span>';
            el.setAttribute('aria-invalid', 'true');
            refreshFormGate(el.form);
        };
    })();
    </script>
    <style>
    .lens-centre { margin-top: 0.9rem; }
    .lens-centre-title { margin-bottom: 0.45rem; color: var(--text); font-size: 0.85rem; font-weight: 600; }
    </style>

    <div class="actions">
        <button type="submit" class="save-btn">Save</button>
        <button type="button" class="broadcast-btn" onclick="broadcastSection('camera', this.form)">Apply to all stations</button>
        <!-- Save / Load template buttons. Camera and grid share one
             ``camera_grid`` template type (tuned together). Buttons bind to
             same modal flow: saving from either side captures both sections,
             loading reloads page so both re-render with new state. -->
        <button type="button" class="secondary"
                data-template-save
                data-template-deps="#camera-section, #grid-section"
                onclick="window.cameraGridSaveTemplate()">Save as template…</button>
        <button type="button" class="secondary"
                onclick="window.cameraGridLoadTemplate()">Load template…</button>
    </div>
</form>
