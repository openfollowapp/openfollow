% import math
% from datetime import datetime
% from openfollow.lens_model import C_BOUND
% from openfollow.scene.calibration_points import pin_errors
% from openfollow.units import UnitSystem, format_length
% _us = UnitSystem(config.ui.unit_system)
% _cam = config.camera
% # The lens centre counts in pixels of the live video; without one there is no number to show.
% _vs = get('video_size')
% _hd = 0.5 * math.hypot(*_vs) if _vs else 0.0
% # Whole pixels within the Setup Wizard's bound, which a centre at the bound can round past.
% _cmax = math.floor(C_BOUND * _hd)
% _cpx = lambda v: max(-_cmax, min(_cmax, round(v * _hd)))
% # A rounded value of -0 reads as 0.
% _fix = lambda v, n: "%.*f" % (n, round(v, n) + 0.0)
% _marks = _cam.calibration_marks
% _pinned = sum(1 for _m in _marks if _m.pin)
% # "4 corners", "3 of 4 known points": the pinned of each kind, named by its count.
% def _pinned_of(kind, one, many):
%   _all = [_m for _m in _marks if (_m.kind == "known") == (kind == "known")]
%   _on = sum(1 for _m in _all if _m.pin)
%   return ("%d" % _on if _on == len(_all) else "%d of %d" % (_on, len(_all))) + " " + (one if len(_all) == 1 else many) if _all else ""
% end
% _pinned_text = _pinned_of("known", "corner", "corners") if _cam.calibration_mode == "simple" else ", ".join(t for t in (_pinned_of("known", "known point", "known points"), _pinned_of("triangulated", "triangulated point", "triangulated points")) if t)
% # Pins are fractions of the snapshot, counted in pixels of the live video.
% _errors = pin_errors(_cam, *_vs) if _vs else {}
% _ratings = {"excellent": "Excellent", "good": "Good", "fair": "Fair", "poor": "Poor", "user_input": "User input", "loaded": "Loaded"}
<form id="camera-section" class="section {{'saved' if defined('saved') and saved else ''}}" data-fold-key="camera" data-help="camera"
      data-template-form="1">
    <div class="section-head">
        <h2>Camera &amp; Lens Calibration</h2>
        <span class="section-note">Set by the Setup Wizard</span>
    </div>

    <div class="camera-readout">
        <div class="group">
            <h3 class="group-title">Position and orientation</h3>
            <dl class="network-state-grid" id="camera-position">
                <dt>Position X</dt>
                <dd>{{format_length(_cam.pos_x, _us)}}</dd>
                <dt>Position Y</dt>
                <dd>{{format_length(_cam.pos_y, _us)}}</dd>
                <dt>Position Z</dt>
                <dd>{{format_length(_cam.pos_z, _us)}}</dd>
                <dt>Pitch</dt>
                <dd>{{_fix(_cam.pitch, 2)}}°</dd>
                <dt>Yaw</dt>
                <dd>{{_fix(_cam.yaw, 2)}}°</dd>
                <dt>Roll</dt>
                <dd>{{_fix(_cam.roll, 2)}}°</dd>
            </dl>
        </div>
        <div class="group">
            <h3 class="group-title">Lens</h3>
            <dl class="network-state-grid" id="camera-lens">
                <dt>Horizontal Field of View</dt>
                <dd>{{_fix(_cam.fov, 2)}}°</dd>
                <dt>Barrel / fisheye</dt>
                <dd>{{_fix(_cam.lens_k1, 3)}}</dd>
                <dt>Corner correction</dt>
                <dd>{{_fix(_cam.lens_k2, 3)}}</dd>
                <dt>Right of the picture centre</dt>
                <dd>{{"%d px" % _cpx(_cam.lens_cx) if _vs else "No video"}}</dd>
                <dt>Above the picture centre</dt>
                <dd>{{"%d px" % _cpx(-_cam.lens_cy) if _vs else "No video"}}</dd>
            </dl>
        </div>
        <div class="group">
            <h3 class="group-title">Calibration</h3>
            <dl class="network-state-grid" id="camera-calibration">
                <dt>Type</dt>
                <dd>{{("Simple" if _cam.calibration_mode == "simple" else "Advanced") if _marks else "None"}}</dd>
                % if _marks:
                % if _cam.calibration_time:
                <dt>Calibrated</dt>
                <dd>{{datetime.fromisoformat(_cam.calibration_time).strftime("%Y-%m-%d %H:%M")}}</dd>
                % end
                % if _cam.calibration_source:
                <dt>Video source</dt>
                <dd>{{_cam.calibration_source}}</dd>
                % end
                % if _cam.calibration_lens_rating:
                <dt>Lens rating</dt>
                <dd>{{_ratings[_cam.calibration_lens_rating]}}</dd>
                % end
                <dt>Pinned</dt>
                <dd>{{_pinned_text}}</dd>
                % if _pinned and (_errors or not _vs):
                <dt>Accuracy</dt>
                <dd>{{"Within %.1f px" % max(_errors.values()) if _errors else "No video"}}</dd>
                % end
                % if _cam.calibration_height is not None:
                <dt>Camera height</dt>
                <dd>Fixed at {{format_length(_cam.calibration_height, _us)}}</dd>
                % end
                % end
            </dl>
        </div>
    </div>
    <style>
    .camera-readout { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0 1.5rem; }
    @media (max-width: 680px) { .camera-readout { grid-template-columns: 1fr; } }
    </style>

    % if _marks:
    % _by_id = {m.id: m for m in _marks}
    <details class="inline-advanced" id="camera-calibration-marks">
        <summary>Calibration points</summary>
        <table class="data-table">
            <thead>
                <tr>
                    <th scope="col">Point</th>
                    <th scope="col">Kind</th>
                    <th scope="col">Position</th>
                    <th scope="col">Measured from</th>
                    <th scope="col">Pinned</th>
                    <th scope="col">Error</th>
                </tr>
            </thead>
            <tbody>
            % for _m in _marks:
                % _at = [_m.x, _m.y, _m.z] if _m.kind == "known" else _m.position
                <tr>
                    <th scope="row">{{_m.name}}</th>
                    <td>{{"Known point" if _m.kind == "known" else "Triangulated point"}}</td>
                    % if _at:
                    <td>X = {{format_length(_at[0], _us)}}<br>Y = {{format_length(_at[1], _us)}}<br>Z = {{format_length(_at[2], _us)}}</td>
                    % else:
                    <td>–</td>
                    % end
                    % if _m.kind == "triangulated":
                    <td>{{_by_id[_m.from_a].name}} {{format_length(_m.distance_a, _us)}}<br>{{_by_id[_m.from_b].name}} {{format_length(_m.distance_b, _us)}}</td>
                    % else:
                    <td>–</td>
                    % end
                    <td>{{"Yes" if _m.pin else "No"}}</td>
                    <td>{{"%.1f px" % _errors[_m.id] if _m.id in _errors else "–"}}</td>
                </tr>
            % end
            </tbody>
        </table>
    </details>
    % end

    <div class="actions">
        <a href="/wizard" class="btn-link save-btn">Open Setup Wizard</a>
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
