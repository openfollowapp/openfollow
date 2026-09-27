%# Pi Camera: which camera the station uses, and naming it in config.txt. Loaded
%# into #picam-camera-setup inside the Video Source form, so every htmx element
%# here names its own target: the form's would replace the whole section.
% from openfollow.privilege.camera_config import AUTOMATIC, MODULE_NAMES, camera_label, sensor_label
% _s = setup
% _mode = mode if defined('mode') else 'view'
% _banner = banner if defined('banner') else None
% _camera_name = camera_name if defined('camera_name') else ''
% _url = '/section/video_source/camera-setup'
% _target = '#picam-camera-setup'
% _configured = _s.get('configured') or ''
% _sensor, _, _port = _configured.partition(',')
% _sensors = _s.get('sensors') or []
% _modules = [name for name in MODULE_NAMES if name in _sensors]
% _others = [name for name in _sensors if name not in MODULE_NAMES]
% _detected = _s.get('detected') or []
% _paths = [cam.get('path', '') for cam in _detected]
% _models = [cam.get('model', '') for cam in _detected]
% _stale = bool(_camera_name) and _camera_name not in _paths
% _available = bool(_s.get('available'))
% _pending = _available and bool(_s.get('pending'))
% _checking = _mode == 'checking'
% _panel = _available and not _checking and (_mode == 'edit' or not (_detected or _pending))
% _row = bool(_detected) or _pending or _checking
% _pick = not _checking and (len(_detected) > 1 or (_stale and bool(_detected)))
% _running = ' and '.join(camera_label(token) for token in _s.get('active') or []) or 'no camera'
% if _configured:
%     _value = camera_label(_configured) + ('' if _s.get('managed') else ', set in config.txt')
% elif _detected:
%     _value = sensor_label(_models[0]) + ', found automatically'
% else:
%     _value = 'Automatic'
% end
<div class="camera-block">
% if _banner:
%     _failed = _banner.get('kind') == 'error'
    <div class="notice {{'error' if _failed else 'success'}}"
         role="{{'alert' if _failed else 'status'}}"
         aria-live="{{'assertive' if _failed else 'polite'}}"
         aria-atomic="true">{{_banner.get('text', '')}}</div>
% end
% if _mode != 'restarting':
%     if _row:
    <div class="row ndi-row">
        <div class="field wide">
%         if _pick:
            <label for="camsetup-stream">Camera</label>
            <select id="camsetup-stream" name="picam_camera_name">
                <option value="" {{'selected' if not _camera_name else ''}}>First camera found</option>
%             for _i, _cam in enumerate(_detected):
%                 _suffix = f' #{_i + 1}' if _models.count(_cam.get('model', '')) > 1 else ''
                <option value="{{_cam.get('path', '')}}" {{'selected' if _cam.get('path') == _camera_name else ''}}>{{sensor_label(_cam.get('model', ''))}}{{_suffix}}</option>
%             end
%             if _stale:
                <option value="{{_camera_name}}" selected>{{_camera_name.rsplit('/', 1)[-1]}} (not found)</option>
%             end
            </select>
%         else:
            <label for="camsetup-value">Camera</label>
            <output id="camsetup-value" class="value-box">{{_value}}</output>
%         end
        </div>
%         if _available and not _panel and not _checking:
        <button type="button" class="secondary"
                hx-get="{{_url}}?edit=1" hx-target="{{_target}}" hx-swap="innerHTML">Change</button>
%         end
    </div>
%     end
%     if _checking:
    <div class="notice" role="status"
         hx-get="{{_url}}" hx-trigger="load delay:3s" hx-target="{{_target}}" hx-swap="innerHTML">Checking the camera…</div>
%     elif _pending:
    <div class="notice warning" role="status">Takes effect after the next restart.<div class="notice-sub">Running now: {{_running}}</div></div>
    <div class="actions">
        <button type="button" class="secondary small"
                hx-post="{{_url}}/restart" hx-target="{{_target}}" hx-swap="innerHTML"
                hx-confirm="Restart the station now? Video and tracking stop until it is back.">Restart now</button>
    </div>
%     elif _mode == 'view' and not _detected:
    <div class="notice error" role="alert">No camera found on this station.<div class="notice-sub">{{'Name the camera and the connector it is plugged into.' if _available else _s.get('reason', '')}}</div></div>
%     elif not _available and _s.get('reason'):
    <div class="notice warning" role="status">{{_s.get('reason')}}</div>
%     end
%     if _panel:
    <div class="inset-panel">
        <div class="row">
            <div class="field wide">
                <label for="camsetup-sensor">Camera module</label>
                <select id="camsetup-sensor" name="camera_sensor"
                        onchange="document.getElementById('camsetup-connector').disabled = this.value === '{{AUTOMATIC}}'">
                    <option value="{{AUTOMATIC}}" {{'selected' if not _configured else ''}}>Automatic</option>
%         if _modules:
                    <optgroup label="Raspberry Pi camera modules">
%             for _name in _modules:
                        <option value="{{_name}}" {{'selected' if _name == _sensor else ''}}>{{sensor_label(_name)}}</option>
%             end
                    </optgroup>
%         end
%         if _others:
                    <optgroup label="Other sensors">
%             for _name in _others:
                        <option value="{{_name}}" {{'selected' if _name == _sensor else ''}}>{{_name}}</option>
%             end
                    </optgroup>
%         end
                </select>
            </div>
            <div class="field">
                <label for="camsetup-connector">Connector</label>
                <select id="camsetup-connector" name="camera_connector" {{'disabled' if not _configured else ''}}>
                    <option value="cam0" {{'selected' if _port != 'cam1' else ''}}>CAM/DISP 0</option>
                    <option value="cam1" {{'selected' if _port == 'cam1' else ''}}>CAM/DISP 1</option>
                </select>
            </div>
        </div>
        <div class="actions">
            <button type="button" class="btn-primary small"
                    hx-post="{{_url}}" hx-include="#camsetup-sensor, #camsetup-connector"
                    hx-target="{{_target}}" hx-swap="innerHTML">Apply</button>
%         if _mode == 'edit':
            <button type="button" class="btn-neutral small"
                    hx-get="{{_url}}" hx-target="{{_target}}" hx-swap="innerHTML">Cancel</button>
%         end
        </div>
    </div>
%     end
% end
</div>
