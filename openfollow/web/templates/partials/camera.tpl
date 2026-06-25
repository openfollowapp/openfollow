% from openfollow.units import UnitSystem, format_length, metric_echo, unit_suffix_length
% _us = UnitSystem(config.ui.unit_system)
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
                <input type="number" id="camera_panel_fov" name="fov" value="{{config.camera.fov}}" min="1" max="179" step="any" oninput="cameraPanelHfovEdited()"
                       hx-get="/api/validate/camera/fov" hx-trigger="blur changed delay:200ms"
                       hx-target="#camera-fov-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="camera-fov-error" aria-invalid="false">
                <span id="camera-fov-error" class="field-error"></span>
            </div>
            <div class="field" id="camera_panel_sensor_field">
                <label>Sensor Size</label>
                <select id="camera_panel_sensor" onchange="cameraPanelSensorOrFocalChanged()"></select>
            </div>
            <div class="field" id="camera_panel_sensor_custom_field" style="display:none;">
                <label>Sensor Width (mm)</label>
                <input type="number" name="sensor_width_mm" id="camera_panel_sensor_custom" step="0.01" min="0" oninput="cameraPanelSensorOrFocalChanged()"
                       hx-get="/api/validate/camera/sensor_width_mm" hx-trigger="blur changed delay:200ms"
                       hx-target="#camera-sensor-width-mm-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="camera-sensor-width-mm-error" aria-invalid="false">
                <span id="camera-sensor-width-mm-error" class="field-error"></span>
            </div>
            <div class="field">
                <label>Focal Length (mm)</label>
                <input type="number" name="focal_length_mm" id="camera_panel_focal"
                       value="{{config.camera.focal_length_mm if config.camera.focal_length_mm is not None else ''}}"
                       step="0.1" min="0" oninput="cameraPanelSensorOrFocalChanged()"
                       hx-get="/api/validate/camera/focal_length_mm" hx-trigger="blur changed delay:200ms"
                       hx-target="#camera-focal-length-mm-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="camera-focal-length-mm-error" aria-invalid="false">
                <span id="camera-focal-length-mm-error" class="field-error"></span>
            </div>
            <input type="hidden" id="camera_panel_sensor_initial"
                   value="{{config.camera.sensor_width_mm if config.camera.sensor_width_mm is not None else ''}}">
        </div>
        <p class="hint" style="margin:0.4rem 0 0 0;font-size:0.8rem;color:var(--muted);">
            FOV is horizontal (as printed on camera datasheets). Pick your sensor size and focal length and we'll compute FOV automatically.
        </p>
    </div>

    <div class="group experimental-feature">
        <h3 class="group-title">Lens distortion <span class="badge-experimental">Experimental</span></h3>
        <p class="field-note">Overlay-only curvature to match a fisheye / wide-angle lens; the video is never warped.</p>
        <div class="row">
            <div class="field">
                <label>Barrel / fisheye (k1)</label>
                <input type="number" id="camera-lens-k1" name="lens_k1" value="{{config.camera.lens_k1}}" min="-0.4" max="0.4" step="0.005"
                       oninput="var s=document.getElementById('camera-lens-k1-range'); if (s) s.value=this.value;"
                       hx-get="/api/validate/camera/lens_k1" hx-trigger="blur changed delay:200ms"
                       hx-target="#camera-lens-k1-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="camera-lens-k1-error" aria-invalid="false">
                <input type="range" id="camera-lens-k1-range" min="-0.4" max="0.4" step="0.005" value="{{config.camera.lens_k1}}"
                       aria-label="Barrel / fisheye (k1) slider"
                       oninput="var n=document.getElementById('camera-lens-k1'); n.value=this.value;">
                <span id="camera-lens-k1-error" class="field-error"></span>
            </div>
            <div class="field">
                <label>Edge fit (k2)</label>
                <input type="number" id="camera-lens-k2" name="lens_k2" value="{{config.camera.lens_k2}}" min="-0.2" max="0.2" step="0.005"
                       oninput="var s=document.getElementById('camera-lens-k2-range'); if (s) s.value=this.value;"
                       hx-get="/api/validate/camera/lens_k2" hx-trigger="blur changed delay:200ms"
                       hx-target="#camera-lens-k2-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="camera-lens-k2-error" aria-invalid="false">
                <input type="range" id="camera-lens-k2-range" min="-0.2" max="0.2" step="0.005" value="{{config.camera.lens_k2}}"
                       aria-label="Edge fit (k2) slider"
                       oninput="var n=document.getElementById('camera-lens-k2'); n.value=this.value;">
                <span id="camera-lens-k2-error" class="field-error"></span>
            </div>
        </div>
    </div>

    <script>
    (function() {
        if (window.__cameraPanelLensHelperInstalled) return;
        window.__cameraPanelLensHelperInstalled = true;

        var SENSOR_SIZES = [
            { id: '1/4',    label: '1/4" (3.6 mm)',              width_mm: 3.6  },
            { id: '1/3',    label: '1/3" (4.8 mm)',              width_mm: 4.8  },
            { id: '1/2.8',  label: '1/2.8" (5.37 mm)',           width_mm: 5.37 },
            { id: '1/2.5',  label: '1/2.5" (5.76 mm)',           width_mm: 5.76 },
            { id: '1/2',    label: '1/2" (6.4 mm)',              width_mm: 6.4  },
            { id: '1/1.8',  label: '1/1.8" (7.18 mm)',           width_mm: 7.18 },
            { id: '1/1.7',  label: '1/1.7" (7.6 mm)',            width_mm: 7.6  },
            { id: '2/3',    label: '2/3" (8.8 mm)',              width_mm: 8.8  },
            { id: '1',      label: '1" (13.2 mm)',               width_mm: 13.2 },
            { id: 'mft',    label: 'Micro Four Thirds (17.3 mm)', width_mm: 17.3 },
            { id: 'apsc',   label: 'APS-C (23.6 mm)',            width_mm: 23.6 },
            { id: 'super35',label: 'Super 35 (24.89 mm)',        width_mm: 24.89 },
            { id: 'ff',     label: 'Full Frame 35 mm (36 mm)',   width_mm: 36   },
            { id: 'custom', label: 'Custom…',                    width_mm: null },
            { id: '',       label: '– choose –',                 width_mm: null },
        ];
        window.__CAMERA_PANEL_SENSORS = SENSOR_SIZES;

        function getSensorWidth() {
            var sel = document.getElementById('camera_panel_sensor');
            if (!sel) return null;
            if (sel.value === 'custom') {
                var v = parseFloat(document.getElementById('camera_panel_sensor_custom').value);
                return isFinite(v) && v > 0 ? v : null;
            }
            var entry = SENSOR_SIZES.find(function(s) { return s.id === sel.value; });
            return entry && entry.width_mm ? entry.width_mm : null;
        }

        function hfovFromSensor(sw, fl) {
            if (!sw || !fl) return null;
            return 2 * Math.atan(sw / (2 * fl)) * 180 / Math.PI;
        }

        function setStale(stale) {
            ['camera_panel_sensor_field', 'camera_panel_sensor_custom_field', 'camera_panel_focal'].forEach(function(id) {
                var el = document.getElementById(id);
                if (el) el.classList.toggle('lens-helper-stale', stale);
            });
        }

        window.cameraPanelSensorOrFocalChanged = function() {
            var sel = document.getElementById('camera_panel_sensor');
            var customField = document.getElementById('camera_panel_sensor_custom_field');
            var customInput = document.getElementById('camera_panel_sensor_custom');
            if (sel && customField) {
                customField.style.display = sel.value === 'custom' ? '' : 'none';
            }
            // Mirror the resolved sensor width into the posted hidden/custom input,
            // so the value reaches the server regardless of preset vs. custom.
            var sw = getSensorWidth();
            if (customInput && sel && sel.value !== 'custom') {
                customInput.value = sw !== null ? String(sw) : '';
            }
            var hfov = hfovFromSensor(sw, parseFloat(document.getElementById('camera_panel_focal').value));
            if (hfov !== null && isFinite(hfov)) {
                document.getElementById('camera_panel_fov').value = hfov.toFixed(2);
                setStale(false);
            }
        };

        window.cameraPanelHfovEdited = function() {
            setStale(true);
        };

        function init() {
            var sel = document.getElementById('camera_panel_sensor');
            if (!sel || sel.options.length) return;
            SENSOR_SIZES.forEach(function(s) {
                var opt = document.createElement('option');
                opt.value = s.id;
                opt.textContent = s.label;
                sel.appendChild(opt);
            });
            sel.value = '';
            var initialSw = parseFloat(document.getElementById('camera_panel_sensor_initial').value);
            if (isFinite(initialSw) && initialSw > 0) {
                var match = SENSOR_SIZES.find(function(s) {
                    return s.width_mm && Math.abs(s.width_mm - initialSw) < 0.05;
                });
                if (match) {
                    sel.value = match.id;
                } else {
                    sel.value = 'custom';
                    document.getElementById('camera_panel_sensor_custom').value = initialSw;
                    document.getElementById('camera_panel_sensor_custom_field').style.display = '';
                }
            }
        }
        init();
        // Re-init after HTMX swaps the camera form back in (save → re-render).
        document.body.addEventListener('htmx:afterSwap', init);
    })();
    </script>
    <style>
    .lens-helper-stale, .lens-helper-stale label,
    .lens-helper-stale input, .lens-helper-stale select { opacity: 0.55; }
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
