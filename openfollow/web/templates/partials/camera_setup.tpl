%# Pi camera setup: what the boot configuration names, and changing it. Loaded
%# into #picam-camera-setup inside the Video Source form, so every htmx element
%# here names its own target: the form's would replace the whole section.
% from openfollow.privilege.camera_config import AUTOMATIC, camera_label
% _s = setup
% _banner = banner if defined('banner') else None
% _failed = bool(_banner) and _banner.get("kind") == "error"
% _configured = _s.get("configured") or ""
% _sensor, _, _port = _configured.partition(",")
<div class="group">
    <h3 class="group-title">Camera setup</h3>
% if _banner:
    <div class="notice {{'error' if _failed else 'success'}}"
         role="{{'alert' if _failed else 'status'}}"
         aria-live="{{'assertive' if _failed else 'polite'}}"
         aria-atomic="true">{{_banner.get('text', '')}}</div>
% end
% if not _s.get("available"):
%     if _s.get("reason"):
    <p class="muted">{{_s.get('reason')}}</p>
%     end
% else:
%     if not _configured:
    <p>Automatic: the station detects Raspberry Pi camera modules by itself.</p>
%     elif _s.get("managed"):
    <p>{{camera_label(_configured)}}, set up here.</p>
%     else:
    <p>{{camera_label(_configured)}}, set in config.txt.</p>
%     end
%     if _s.get("pending"):
    <div class="notice warning" role="status">Takes effect after the next restart.</div>
    <div class="actions">
        <button type="button" class="secondary"
                hx-post="/section/video_source/camera-setup/restart"
                hx-target="#picam-camera-setup" hx-swap="innerHTML"
                hx-confirm="Restart the station now? Video and tracking stop until it is back.">Restart now</button>
    </div>
%     end
    <div class="row">
        <div class="field">
            <label for="camsetup-sensor">Camera</label>
            <select id="camsetup-sensor" name="camera_sensor">
                <option value="{{AUTOMATIC}}" {{'selected' if not _configured else ''}}>Automatic (Raspberry Pi cameras)</option>
%     for name in _s.get("sensors", []):
                <option value="{{name}}" {{'selected' if name == _sensor else ''}}>{{name}}</option>
%     end
            </select>
        </div>
        <div class="field">
            <label for="camsetup-connector">Connector</label>
            <select id="camsetup-connector" name="camera_connector">
                <option value="cam0" {{'selected' if _port != 'cam1' else ''}}>CAM/DISP 0</option>
                <option value="cam1" {{'selected' if _port == 'cam1' else ''}}>CAM/DISP 1</option>
            </select>
        </div>
    </div>
    <div class="actions">
        <button type="button" class="secondary"
                hx-post="/section/video_source/camera-setup"
                hx-include="#camsetup-sensor, #camsetup-connector"
                hx-target="#picam-camera-setup" hx-swap="innerHTML">Apply</button>
    </div>
% end
</div>
