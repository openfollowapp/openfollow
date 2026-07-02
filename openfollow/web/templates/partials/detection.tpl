% saved_section = defined('saved_section') and saved_section or ''
<div id="detection-section" class="experimental-feature detection-cards {{'saved' if saved_section else ''}}">

    <div class="detection-intro">
        <h2>{{_('Person Detection')}} <span class="badge-experimental">{{_('Experimental')}}</span></h2>
        <span class="section-note">{{_('Optional YOLO person detection. Compute-heavy &ndash; a workstation is recommended over a Pi 5; see each box\'s help. Each box saves on its own.')}}</span>
    </div>

% missing = defined('detection_missing') and detection_missing or []
% install_feedback = defined('install_feedback') and install_feedback or ''
% install_error = defined('install_error') and install_error
% extras_installed = defined('detection_extras_installed') and detection_extras_installed or {}
% di = defined('detection_install') and detection_install or {}
% di_state = di.get('state', 'idle')
% di_running = di_state == 'running'
% if missing:
    <div role="alert" style="margin: 0 0 14px; padding: 10px 12px; border-radius: 8px; border: 1px solid rgba(255, 120, 120, 0.45); background: rgba(255, 76, 76, 0.1); color: #ffd6d6; font-size: 0.85rem;">
        <strong>{{_('Detection needs extra components:')}}</strong> {{', '.join(missing)}}.
        {{_('Install with')}} <code>bash /usr/share/openfollow/install-detection.sh</code>, {{_('then restart.')}}
    </div>
% end
% if install_feedback:
%     fb_bg = 'rgba(255, 76, 76, 0.1)' if install_error else 'rgba(76, 175, 80, 0.12)'
%     fb_border = 'rgba(255, 120, 120, 0.45)' if install_error else 'rgba(120, 200, 120, 0.45)'
%     fb_color = '#ffd6d6' if install_error else '#d6ffd9'
%     fb_role = 'alert' if install_error else 'status'
%     fb_live = 'assertive' if install_error else 'polite'
    <div role="{{fb_role}}" aria-live="{{fb_live}}" aria-atomic="true" style="margin: 0 0 14px; padding: 10px 12px; border-radius: 8px; border: 1px solid {{fb_border}}; background: {{fb_bg}}; color: {{fb_color}}; font-size: 0.85rem;">
        {{install_feedback}}
    </div>
% end
% if di_running:
    <div role="status" aria-live="polite" aria-atomic="true" class="install-progress"
         hx-get="/section/detection"
         hx-trigger="every 1s"
         hx-target="#detection-section"
         hx-swap="outerHTML"
         style="margin: 0 0 14px; padding: 10px 12px; border-radius: 8px; border: 1px solid rgba(120, 180, 255, 0.45); background: rgba(120, 180, 255, 0.10); color: #d6e6ff; font-size: 0.85rem;">
        <strong>{{di.get('message') or 'Working...'}}</strong>
%     tail = di.get('tail') or ''
%     if tail.strip():
        <pre style="margin: 6px 0 0; padding: 6px 8px; background: rgba(0,0,0,0.25); border-radius: 4px; font-size: 0.75rem; max-height: 8em; overflow: auto; white-space: pre-wrap; color: #cfd6df;">{{tail}}</pre>
%     end
    </div>
% elif di_state == 'success':
    <div role="status" aria-live="polite" aria-atomic="true"
         style="margin: 0 0 14px; padding: 10px 12px; border-radius: 8px; border: 1px solid rgba(120, 200, 120, 0.45); background: rgba(76, 175, 80, 0.12); color: #d6ffd9; font-size: 0.85rem;">
        <strong>{{di.get('message') or 'Done.'}}</strong>
    </div>
% elif di_state == 'error':
    <div role="alert" aria-live="assertive" aria-atomic="true"
         style="margin: 0 0 14px; padding: 10px 12px; border-radius: 8px; border: 1px solid rgba(255, 120, 120, 0.45); background: rgba(255, 76, 76, 0.10); color: #ffd6d6; font-size: 0.85rem;">
        <strong>{{di.get('message') or 'Failed.'}}</strong>
%     tail = di.get('tail') or ''
%     if tail.strip():
        <pre style="margin: 6px 0 0; padding: 6px 8px; background: rgba(0,0,0,0.25); border-radius: 4px; font-size: 0.75rem; max-height: 8em; overflow: auto; white-space: pre-wrap; color: #ffd6d6;">{{tail}}</pre>
%     end
    </div>
% end

    <form class="section {{'saved' if saved_section == 'models' else ''}}" data-fold-key="detection_models" data-help="detection"
          hx-post="/section/detection/models" hx-target="#detection-section" hx-swap="outerHTML" hx-trigger="submit">
        <div class="section-head">
            <h2>{{_('Models')}}</h2>
            <span class="section-note">{{_('Pick the active model, download new ones, and remove ones you no longer need.')}}</span>
        </div>

        <div class="group">
            <h3 class="group-title">{{_('Model')}}</h3>
% available_models = defined('detection_available_models') and detection_available_models or []
% installed_models = defined('detection_installed_models') and detection_installed_models or []
% storage_info = defined('detection_storage_info') and detection_storage_info or {}
% saved_model = config.detection.model
% selectable = [(v, lbl) for v, lbl, avail in available_models if avail]
% catalogue_unavailable = [(v, lbl) for v, lbl, avail in available_models if not avail]
            <div class="row">
                <div class="field">
                    <label>{{_('Model')}}</label>
%     if selectable:
                    <select name="model">
%         for value, label in selectable:
                        <option value="{{value}}" {{'selected' if value == saved_model else ''}}>{{label}}</option>
%         end
                    </select>
                    <span class="field-note">{{_('Only models present in the storage folder are listed.')}}</span>
%     else:
                    <input type="hidden" name="model" value="{{saved_model}}">
                    <span class="field-note">{{_('No models installed yet &ndash; download one below.')}}</span>
%     end
                </div>
            </div>
%     if storage_info:
            <p class="section-note" style="margin: 8px 0 0;">
                {{_('Models disk:')}} <strong>{{storage_info.get('free_h', '?')}} {{_('free')}}</strong> {{_('of')}} {{storage_info.get('total_h', '?')}}
                (<code>{{storage_info.get('path', '')}}</code>)
            </p>
%     end
        </div>

%     if installed_models:
        <div class="group">
            <h3 class="group-title">{{_('Installed models')}}</h3>
            <div class="detection-installed-models">
%         for m in installed_models:
                <div class="row" style="align-items: center; gap: 10px;">
                    <code style="flex: 1 1 auto;">{{m['name']}}</code>
                    <span class="field-note" style="margin: 0;">{{m['size_h']}}</span>
                    <button type="button" class="broadcast-btn"
                            {{'disabled' if di_running else ''}}
                            hx-post="/section/detection/models/delete"
                            hx-vals='{"model": "{{m['name']}}"}'
                            hx-target="#detection-section"
                            hx-swap="outerHTML"
                            hx-confirm="{{_('Delete %s? This removes the file from disk.') % m['name']}}">
                        {{_('Delete')}}
                    </button>
                </div>
%         end
            </div>
        </div>
%     end

% include('partials/detection_model_download.tpl', config=config, catalogue_unavailable=catalogue_unavailable, extras_installed=extras_installed, di_running=di_running)

        <div class="actions">
            <button type="submit" class="save-btn">{{_('Save')}}</button>
        </div>
    </form>

    <form class="section {{'saved' if saved_section == 'inference' else ''}}" data-fold-key="detection_inference" data-help="detection"
          hx-post="/section/detection/inference" hx-target="#detection-section" hx-swap="outerHTML" hx-trigger="submit">
        <div class="section-head">
            <h2>{{_('Detection &amp; Display')}}</h2>
            <span class="section-note">{{_('Core inference settings and what the overlay draws.')}}</span>
        </div>
        <div class="group">
            <h3 class="group-title">{{_('Detection')}}</h3>
            <div class="row row--toggles">
                <div class="field checkbox-field">
                    <label>{{_('Enabled')}}</label>
                    <div class="checkbox-wrap"><input type="checkbox" name="enabled" {{'checked' if config.detection.enabled else ''}}></div>
                </div>
                <div class="field checkbox-field">
                    <label>{{_('CLAHE Preprocess')}}</label>
                    <div class="checkbox-wrap"><input type="checkbox" name="preprocess_clahe" {{'checked' if config.detection.preprocess_clahe else ''}}></div>
                </div>
            </div>
            <div class="row row--pair">
                <div class="field">
                    <label>{{_('Confidence (0–1)')}}</label>
                    <input id="detection-confidence" type="number" name="confidence" value="{{config.detection.confidence}}" min="0" max="1" step="0.05"
                           hx-get="/api/validate/detection/confidence" hx-trigger="blur changed delay:200ms"
                           hx-target="#detection-confidence-error" hx-swap="innerHTML" hx-include="closest form"
                           aria-describedby="detection-confidence-error" aria-invalid="false">
                    <span id="detection-confidence-error" class="field-error"></span>
                </div>
                <div class="field">
                    <label>{{_('Detection Rate (FPS)')}}</label>
                    <select name="interval_ms">
                        <option value="1000" {{'selected' if config.detection.interval_ms == 1000 else ''}}>{{_('1 FPS')}}</option>
                        <option value="500" {{'selected' if config.detection.interval_ms == 500 else ''}}>{{_('2 FPS')}}</option>
                        <option value="200" {{'selected' if config.detection.interval_ms == 200 else ''}}>{{_('5 FPS')}}</option>
                        <option value="100" {{'selected' if config.detection.interval_ms == 100 else ''}}>{{_('10 FPS')}}</option>
                        <option value="67" {{'selected' if config.detection.interval_ms == 67 else ''}}>{{_('15 FPS')}}</option>
                        <option value="33" {{'selected' if config.detection.interval_ms == 33 else ''}}>{{_('30 FPS')}}</option>
                    </select>
                </div>
            </div>
            <div class="row row--pair">
                <div class="field">
                    <label>{{_('Inference Size')}}</label>
                    <select name="inference_size">
                        <option value="320" {{'selected' if config.detection.inference_size == 320 else ''}}>{{_('320 (faster)')}}</option>
                        <option value="416" {{'selected' if config.detection.inference_size == 416 else ''}}>{{_('416')}}</option>
                        <option value="512" {{'selected' if config.detection.inference_size == 512 else ''}}>{{_('512')}}</option>
                        <option value="640" {{'selected' if config.detection.inference_size == 640 else ''}}>{{_('640 (default, slower)')}}</option>
                    </select>
                    <span class="field-note">{{_('Match the model\'s export image size.')}}</span>
                </div>
                <div class="field">
                    <label>{{_('Max Persons')}}</label>
                    <input id="detection-max-persons" type="number" name="max_persons" value="{{config.detection.max_persons}}" min="1" max="50" step="1"
                           hx-get="/api/validate/detection/max_persons" hx-trigger="blur changed delay:200ms"
                           hx-target="#detection-max-persons-error" hx-swap="innerHTML" hx-include="closest form"
                           aria-describedby="detection-max-persons-error" aria-invalid="false">
                    <span id="detection-max-persons-error" class="field-error"></span>
                </div>
            </div>
        </div>

        <div class="group">
            <h3 class="group-title">{{_('Display')}}</h3>
            <div class="row row--toggles">
                <div class="field checkbox-field">
                    <label>{{_('Show Boxes')}}</label>
                    <div class="checkbox-wrap"><input type="checkbox" name="show_boxes" {{'checked' if config.detection.show_boxes else ''}}></div>
                </div>
                <div class="field checkbox-field">
                    <label>{{_('Show Labels')}}</label>
                    <div class="checkbox-wrap"><input type="checkbox" name="show_labels" {{'checked' if config.detection.show_labels else ''}}></div>
                </div>
            </div>
            <div class="row row--pair">
                <div class="field">
                    <label>{{_('Box Color')}}</label>
                    %# Native picker replaced by circle-swatch full-variant.
                    %# Picked up by color-picker.js via data-color-picker;
                    %# hidden input carries form value. Inline validator dropped
                    %# (see analogous crosshair field in marker.tpl).
                    <button id="detection-box-color" type="button" class="color-swatch-trigger"
                            data-color-picker="full" data-value="{{config.detection.box_color}}"
                            aria-label="{{_('Detection box colour')}}"></button>
                    <input type="hidden" name="box_color" value="{{config.detection.box_color}}">
                    <span class="field-note">{{_('The box attached to a marker is drawn in that marker\'s colour.')}}</span>
                </div>
                <div class="field">
                    <label>{{_('Box Thickness (px)')}}</label>
                    <input id="detection-box-thickness" type="number" name="box_thickness" value="{{config.detection.box_thickness}}" min="1" max="10" step="1"
                           hx-get="/api/validate/detection/box_thickness" hx-trigger="blur changed delay:200ms"
                           hx-target="#detection-box-thickness-error" hx-swap="innerHTML" hx-include="closest form"
                           aria-describedby="detection-box-thickness-error" aria-invalid="false">
                    <span id="detection-box-thickness-error" class="field-error"></span>
                </div>
            </div>
        </div>

        <div class="actions">
            <button type="submit" class="save-btn">{{_('Save')}}</button>
        </div>
    </form>

    <form class="section {{'saved' if saved_section == 'tracking' else ''}}" data-fold-key="detection_tracking" data-help="detection"
          hx-post="/section/detection/tracking" hx-target="#detection-section" hx-swap="outerHTML" hx-trigger="submit">
        <div class="section-head">
            <h2>{{_('Tracking')}}</h2>
            <span class="section-note">{{_('Pick how detection drives the marker, then tune the motion.')}}</span>
        </div>
        <div class="row">
            <div class="field">
                <label>{{_('Tracking Mode')}}</label>
                <div class="seg-toggle" role="radiogroup" aria-label="{{_('Tracking mode')}}">
                    <label class="seg-option">
                        <input type="radio" name="pin_mode" value="assist" {{'checked' if config.detection.pin_mode == 'assist' else ''}}>
                        <span><strong>{{_('AI Assisted')}}</strong><small>{{_('Refine your manual control')}}</small></span>
                    </label>
                    <label class="seg-option">
                        <input type="radio" name="pin_mode" value="replace" {{'checked' if config.detection.pin_mode == 'replace' else ''}}>
                        <span><strong>{{_('Fully Automatic')}}</strong><small>{{_('Auto-pin the largest person')}}</small></span>
                    </label>
                </div>
            </div>
        </div>
        <div class="row row--toggles">
            <div class="field checkbox-field">
                <label>{{_('Pin Marker')}}</label>
                <div class="checkbox-wrap"><input type="checkbox" name="pin_marker" {{'checked' if config.detection.pin_marker else ''}}></div>
            </div>
        </div>
        <div class="row row--pair">
            <div class="field">
                <label>{{_('Pin To Marker')}}</label>
%     saved_pin_id = config.detection.pin_marker_id
%     pin_id_in_list = saved_pin_id in config.controlled_marker_ids
                <select name="pin_marker_id">
                    <option value="-1" {{'selected' if saved_pin_id < 0 else ''}}>{{_('Currently selected (controller)')}}</option>
%     if saved_pin_id >= 0 and not pin_id_in_list:
                    <option value="{{saved_pin_id}}" selected disabled>{{_('Marker %s (unavailable)') % saved_pin_id}}</option>
%     end
% for tid in config.controlled_marker_ids:
                    <option value="{{tid}}" {{'selected' if saved_pin_id == tid else ''}}>{{_('Marker %s') % tid}}</option>
% end
                </select>
            </div>
            <div class="field">
                <label>{{_('Pin Point')}}</label>
                <select name="pin_point">
                    <option value="top" {{'selected' if config.detection.pin_point == 'top' else ''}}>{{_('Top (Head)')}}</option>
                    <option value="bottom" {{'selected' if config.detection.pin_point == 'bottom' else ''}}>{{_('Bottom (Feet)')}}</option>
                </select>
            </div>
        </div>
        <div class="row row--pair">
            <div class="field">
                <label>{{_('Smoothing / glide (0–1)')}}</label>
                <input id="detection-smoothing" type="number" name="smoothing" value="{{config.detection.smoothing}}" min="0.01" max="1" step="0.01"
                       hx-get="/api/validate/detection/smoothing" hx-trigger="blur changed delay:200ms"
                       hx-target="#detection-smoothing-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="detection-smoothing-error" aria-invalid="false">
                <span id="detection-smoothing-error" class="field-error"></span>
            </div>
            <div class="field">
                <label>{{_('Prediction')}}</label>
                <input id="detection-prediction" type="number" name="prediction" value="{{config.detection.prediction}}" min="0" max="20" step="0.5"
                       hx-get="/api/validate/detection/prediction" hx-trigger="blur changed delay:200ms"
                       hx-target="#detection-prediction-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="detection-prediction-error" aria-invalid="false">
                <span id="detection-prediction-error" class="field-error"></span>
            </div>
        </div>
        <div class="row row--pair">
            <div class="field">
                <label>{{_('Grace Period (ms)')}}</label>
                <input id="detection-grace-period-ms" type="number" name="grace_period_ms" value="{{config.detection.grace_period_ms}}" min="0" max="10000" step="100"
                       hx-get="/api/validate/detection/grace_period_ms" hx-trigger="blur changed delay:200ms"
                       hx-target="#detection-grace-period-ms-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="detection-grace-period-ms-error" aria-invalid="false">
                <span id="detection-grace-period-ms-error" class="field-error"></span>
            </div>
        </div>

        <div class="group group--assist" data-assist-only {{'' if config.detection.pin_mode == 'assist' else 'hidden'}}>
            <h3 class="group-title">{{_('Assisted Tracking')}}</h3>
            <span class="section-note">{{_('Where the AI output sits relative to your manual anchor.')}}</span>
            <div class="row row--pair">
                <div class="field">
                    <label>{{_('Assist Radius (m)')}}</label>
                    <input id="detection-assist-radius-m" type="number" name="assist_radius_m" value="{{config.detection.assist_radius_m}}" min="0.1" max="50" step="0.1"
                           hx-get="/api/validate/detection/assist_radius_m" hx-trigger="blur changed delay:200ms"
                           hx-target="#detection-assist-radius-m-error" hx-swap="innerHTML" hx-include="closest form"
                           aria-describedby="detection-assist-radius-m-error" aria-invalid="false">
                    <span id="detection-assist-radius-m-error" class="field-error"></span>
                </div>
                <div class="field">
                    <label>{{_('Clip strength (0–1)')}}</label>
                    <input id="detection-assist-strength" type="number" name="assist_strength" value="{{config.detection.assist_strength}}" min="0" max="1" step="0.05"
                           hx-get="/api/validate/detection/assist_strength" hx-trigger="blur changed delay:200ms"
                           hx-target="#detection-assist-strength-error" hx-swap="innerHTML" hx-include="closest form"
                           aria-describedby="detection-assist-strength-error" aria-invalid="false">
                    <span id="detection-assist-strength-error" class="field-error"></span>
                </div>
            </div>
        </div>

        <div class="actions">
            <button type="submit" class="save-btn">{{_('Save')}}</button>
        </div>
    </form>
</div>
