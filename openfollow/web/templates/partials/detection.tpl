% saved_section = defined('saved_section') and saved_section or ''
%# One-of-N widget rule:
%#   seg-toggle--N  -> a mode (2-4 options) that changes what the form shows (Tracking).
%#   tier-list      -> an ordered ladder of 5+ ranked tiers where order is information (Quality).
%#   <select>       -> plain enumerated values with no per-option descriptor (Detection rate).
<div id="detection-section" class="experimental-feature detection-cards {{'saved' if saved_section else ''}}">

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
        {{_('Install with')}} <code>bash /usr/share/openfollow/install-detection.sh</code>{{_(', then restart.')}}
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
        <strong>{{di.get('message') or _('Working...')}}</strong>
%     tail = di.get('tail') or ''
%     if tail.strip():
        <pre style="margin: 6px 0 0; padding: 6px 8px; background: rgba(0,0,0,0.25); border-radius: 4px; font-size: 0.75rem; max-height: 8em; overflow: auto; white-space: pre-wrap; color: #cfd6df;">{{tail}}</pre>
%     end
    </div>
% elif di_state == 'success':
    <div role="status" aria-live="polite" aria-atomic="true"
         style="margin: 0 0 14px; padding: 10px 12px; border-radius: 8px; border: 1px solid rgba(120, 200, 120, 0.45); background: rgba(76, 175, 80, 0.12); color: #d6ffd9; font-size: 0.85rem;">
        <strong>{{di.get('message') or _('Done.')}}</strong>
    </div>
% elif di_state == 'error':
    <div role="alert" aria-live="assertive" aria-atomic="true"
         style="margin: 0 0 14px; padding: 10px 12px; border-radius: 8px; border: 1px solid rgba(255, 120, 120, 0.45); background: rgba(255, 76, 76, 0.10); color: #ffd6d6; font-size: 0.85rem;">
        <strong>{{di.get('message') or _('Failed.')}}</strong>
%     tail = di.get('tail') or ''
%     if tail.strip():
        <pre style="margin: 6px 0 0; padding: 6px 8px; background: rgba(0,0,0,0.25); border-radius: 4px; font-size: 0.75rem; max-height: 8em; overflow: auto; white-space: pre-wrap; color: #ffd6d6;">{{tail}}</pre>
%     end
    </div>
% end

% det = config.detection
% tracking_state = 'off' if not det.enabled else ('assist' if det.pin_mode == 'assist' else 'replace')

    <form class="section {{'saved' if saved_section == 'tracking' else ''}}" data-fold-key="detection_tracking" data-help="detection"
          hx-post="/section/detection/tracking" hx-target="#detection-section" hx-swap="outerHTML" hx-trigger="submit">
        <div class="section-head">
            <h2>{{_('Tracking')}} <span class="badge-experimental">{{_('Experimental')}}</span></h2>
            <span class="section-note">{{_('Turn detection on and choose how it steers your markers.')}}</span>
        </div>
        <div class="row">
            <div class="field">
                <label>{{_('Tracking')}}</label>
                <div class="seg-toggle seg-toggle--3" role="radiogroup" aria-label="{{_('Tracking mode')}}">
                    <label class="seg-option">
                        <input type="radio" name="tracking_state" value="off" {{'checked' if tracking_state == 'off' else ''}}>
                        <span><strong>{{_('Off')}}</strong><small>{{_('No detection')}}</small></span>
                    </label>
                    <label class="seg-option">
                        <input type="radio" name="tracking_state" value="assist" {{'checked' if tracking_state == 'assist' else ''}}>
                        <span><strong>{{_('AI Assisted')}}</strong><small>{{_('Refines all your markers')}}</small></span>
                    </label>
                    <label class="seg-option">
                        <input type="radio" name="tracking_state" value="replace" {{'checked' if tracking_state == 'replace' else ''}}>
                        <span><strong>{{_('Fully Automatic')}}</strong><small>{{_('Auto-follows one person')}}</small></span>
                    </label>
                </div>
            </div>
        </div>
        <div class="group">
            <h3 class="group-title">{{_('Motion')}}</h3>
            <div class="row row--pair">
                <div class="field" data-replace-only {{'' if tracking_state == 'replace' else 'hidden'}}>
                    <label>{{_('Follow marker')}}</label>
%     saved_pin_id = det.pin_marker_id
%     pin_id_in_list = saved_pin_id in config.controlled_marker_ids
                    <select name="pin_marker_id">
                        <option value="-1" {{'selected' if saved_pin_id < 0 else ''}}>{{_('Currently selected (controller)')}}</option>
%     if saved_pin_id >= 0 and not pin_id_in_list:
                        <option value="{{saved_pin_id}}" selected disabled>{{_('Marker')}} {{saved_pin_id}} ({{_('unavailable')}})</option>
%     end
% for tid in config.controlled_marker_ids:
                        <option value="{{tid}}" {{'selected' if saved_pin_id == tid else ''}}>{{_('Marker')}} {{tid}}</option>
% end
                    </select>
                    <span class="field-note">{{_('Which marker the automatic tracker drives.')}}</span>
                </div>
                <div class="field">
                    <label>{{_('Track')}}</label>
                    <select name="pin_point">
                        <option value="top" {{'selected' if det.pin_point == 'top' else ''}}>{{_('Head (top of person)')}}</option>
                        <option value="bottom" {{'selected' if det.pin_point == 'bottom' else ''}}>{{_('Feet (floor position)')}}</option>
                    </select>
                    <span class="field-note">{{_('Which part of the person sets the marker.')}}</span>
                </div>
            </div>
            <details class="inline-advanced">
                <summary>{{_('Advanced motion')}}</summary>
                <div class="inline-advanced-content">
                    <div class="row row--pair">
                        <div class="field">
                            <label>{{_('Smoothing')}} (0–1)</label>
                            <input id="detection-smoothing" type="number" name="smoothing" value="{{det.smoothing}}" min="0.01" max="1" step="0.01"
                                   hx-get="/api/validate/detection/smoothing" hx-trigger="blur changed delay:200ms"
                                   hx-target="#detection-smoothing-error" hx-swap="innerHTML" hx-include="closest form"
                                   aria-describedby="detection-smoothing-error" aria-invalid="false">
                            <span id="detection-smoothing-error" class="field-error"></span>
                        </div>
                        <div class="field">
                            <label>{{_('Prediction')}}</label>
                            <input id="detection-prediction" type="number" name="prediction" value="{{det.prediction}}" min="0" max="20" step="0.5"
                                   hx-get="/api/validate/detection/prediction" hx-trigger="blur changed delay:200ms"
                                   hx-target="#detection-prediction-error" hx-swap="innerHTML" hx-include="closest form"
                                   aria-describedby="detection-prediction-error" aria-invalid="false">
                            <span id="detection-prediction-error" class="field-error"></span>
                        </div>
                    </div>
                    <div class="row row--pair">
                        <div class="field">
                            <label>{{_('Grace period')}} (ms)</label>
                            <input id="detection-grace-period-ms" type="number" name="grace_period_ms" value="{{det.grace_period_ms}}" min="0" max="10000" step="100"
                                   hx-get="/api/validate/detection/grace_period_ms" hx-trigger="blur changed delay:200ms"
                                   hx-target="#detection-grace-period-ms-error" hx-swap="innerHTML" hx-include="closest form"
                                   aria-describedby="detection-grace-period-ms-error" aria-invalid="false">
                            <span id="detection-grace-period-ms-error" class="field-error"></span>
                        </div>
                    </div>
                </div>
            </details>
        </div>

        <div class="group group--assist" data-assist-only {{'' if tracking_state == 'assist' else 'hidden'}}>
            <h3 class="group-title">{{_('Assisted Tracking')}}</h3>
            <div class="row row--pair">
                <div class="field">
                    <label>{{_('Assist radius')}} (m)</label>
                    <input id="detection-assist-radius-m" type="number" name="assist_radius_m" value="{{det.assist_radius_m}}" min="0.1" max="50" step="0.1"
                           hx-get="/api/validate/detection/assist_radius_m" hx-trigger="blur changed delay:200ms"
                           hx-target="#detection-assist-radius-m-error" hx-swap="innerHTML" hx-include="closest form"
                           aria-describedby="detection-assist-radius-m-error" aria-invalid="false">
                    <span id="detection-assist-radius-m-error" class="field-error"></span>
                </div>
                <div class="field">
                    <label>{{_('Anchor pull')}} (0–1)</label>
                    <input id="detection-assist-strength" type="number" name="assist_strength" value="{{det.assist_strength}}" min="0" max="1" step="0.05"
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

    <form class="section {{'saved' if saved_section == 'models' else ''}}" data-fold-key="detection_models" data-help="detection"
          hx-post="/section/detection/models" hx-target="#detection-section" hx-swap="outerHTML" hx-trigger="submit">
        <div class="section-head">
            <h2>{{_('Detection Model')}} <span class="badge-experimental">{{_('Experimental')}}</span></h2>
            <span class="section-note">{{_('The model that spots people. Higher quality sees better, costs more compute.')}}</span>
        </div>

% tiers = defined('detection_tiers') and detection_tiers or []
% available_models = defined('detection_available_models') and detection_available_models or []
% installed_models = defined('detection_installed_models') and detection_installed_models or []
% storage_info = defined('detection_storage_info') and detection_storage_info or {}
% saved_model = det.model
% tier_models = [t['model'] for t in tiers]
% tier_label_by_model = {t['model']: t['label'] for t in tiers}
% catalogue_unavailable = [(v, lbl) for v, lbl, avail in available_models if not avail]
% other_installed = [(v, lbl) for v, lbl, avail in available_models if avail and v not in tier_models]
% selected_is_tier = saved_model in tier_models
        <div class="group">
            <h3 class="group-title">{{_('Quality')}}</h3>
% if tiers:
            <div class="tier-list" role="radiogroup" aria-label="{{_('Detection quality')}}">
%     for t in tiers:
                <label class="tier-option">
                    <input type="radio" name="model" value="{{t['model']}}" {{'checked' if t['model'] == saved_model else ''}} {{'disabled' if not t['available'] else ''}}>
                    <span><strong>{{t['label']}}</strong><small>{{t['blurb']}}{{'' if t['available'] else _(' – download in Advanced')}}</small></span>
                </label>
%     end
            </div>
%     if not selected_is_tier:
            <span class="field-note">{{_('Using a custom model:')}} <code>{{saved_model}}</code> ({{_('change it under Advanced models')}}).</span>
%     end
% else:
            <input type="hidden" name="model" value="{{saved_model}}">
            <span class="field-note">{{_('No quality tiers available – install detection components first.')}}</span>
% end
        </div>

        <details class="inline-advanced">
            <summary>{{_('Advanced models')}}</summary>
            <div class="inline-advanced-content">

% if other_installed:
            <div class="group">
                <h3 class="group-title">{{_('Other installed models')}}</h3>
                <div class="tier-list" role="radiogroup" aria-label="{{_('Other installed models')}}">
%     if not selected_is_tier:
                    <label class="tier-option">
                        <input type="radio" name="model" value="{{saved_model}}" checked>
                        <span><strong>{{saved_model}}</strong><small>{{_('Current selection')}}</small></span>
                    </label>
%     end
%     for value, label in other_installed:
%         if value != saved_model:
                    <label class="tier-option">
                        <input type="radio" name="model" value="{{value}}">
                        <span><strong>{{label}}</strong><small>{{value}}</small></span>
                    </label>
%         end
%     end
                </div>
            </div>
% elif not selected_is_tier:
            <div class="group">
                <h3 class="group-title">{{_('Other installed models')}}</h3>
                <div class="tier-list" role="radiogroup" aria-label="{{_('Other installed models')}}">
                    <label class="tier-option">
                        <input type="radio" name="model" value="{{saved_model}}" checked>
                        <span><strong>{{saved_model}}</strong><small>{{_('Current selection')}}</small></span>
                    </label>
                </div>
            </div>
% end

% include('partials/detection_model_download.tpl', config=config, catalogue_unavailable=catalogue_unavailable, extras_installed=extras_installed, di_running=di_running)

%     if installed_models:
            <div class="group">
                <h3 class="group-title">{{_('Installed models')}}</h3>
                <div class="detection-installed-models">
%         for m in installed_models:
                    <div class="row" style="align-items: center; gap: 10px;">
                        <code style="flex: 1 1 auto;">{{m['name']}}</code>
%             if m['name'] in tier_label_by_model:
                        <span class="tier-tag">{{tier_label_by_model[m['name']]}}</span>
%             end
                        <span class="field-note" style="margin: 0;">{{m['size_h']}}</span>
                        <button type="button" class="broadcast-btn"
                                {{'disabled' if di_running else ''}}
                                hx-post="/section/detection/models/delete"
                                hx-vals='{"model": "{{m['name']}}"}'
                                hx-target="#detection-section"
                                hx-swap="outerHTML"
                                hx-confirm="{{_('Delete ')}}{{m['name']}}{{_('? This removes the file from disk.')}}">
                            {{_('Delete')}}
                        </button>
                    </div>
%         end
                </div>
            </div>
%     end

%     if storage_info:
            <p class="section-note" style="margin: 8px 0 0;">
                {{_('Models disk:')}} <strong>{{storage_info.get('free_h', '?')}} {{_('free')}}</strong> {{_('of')}} {{storage_info.get('total_h', '?')}}
                (<code>{{storage_info.get('path', '')}}</code>)
            </p>
%     end
            </div>
        </details>

        <div class="actions">
            <button type="submit" class="save-btn">{{_('Save')}}</button>
        </div>
    </form>

    <form class="section {{'saved' if saved_section == 'inference' else ''}}" data-fold-key="detection_inference" data-help="detection"
          hx-post="/section/detection/inference" hx-target="#detection-section" hx-swap="outerHTML" hx-trigger="submit">
        <div class="section-head">
            <h2>{{_('Sensitivity & Overlay')}} <span class="badge-experimental">{{_('Experimental')}}</span></h2>
            <span class="section-note">{{_('Detection sensitivity, and what the camera overlay draws.')}}</span>
        </div>
        <div class="group">
            <h3 class="group-title">{{_('Sensitivity')}}</h3>
            <div class="row row--pair">
                <div class="field">
                    <label>{{_('Detection sensitivity')}} (0–1)</label>
                    <input id="detection-confidence" type="number" name="confidence" value="{{det.confidence}}" min="0" max="1" step="0.05"
                           hx-get="/api/validate/detection/confidence" hx-trigger="blur changed delay:200ms"
                           hx-target="#detection-confidence-error" hx-swap="innerHTML" hx-include="closest form"
                           aria-describedby="detection-confidence-error" aria-invalid="false">
                    <span id="detection-confidence-error" class="field-error"></span>
                </div>
                <div class="field">
                    <label>{{_('Detection rate')}} (FPS)</label>
                    <select name="interval_ms">
                        <option value="1000" {{'selected' if det.interval_ms == 1000 else ''}}>{{_('1 FPS')}}</option>
                        <option value="500" {{'selected' if det.interval_ms == 500 else ''}}>{{_('2 FPS')}}</option>
                        <option value="200" {{'selected' if det.interval_ms == 200 else ''}}>{{_('5 FPS')}}</option>
                        <option value="100" {{'selected' if det.interval_ms == 100 else ''}}>{{_('10 FPS')}}</option>
                        <option value="67" {{'selected' if det.interval_ms == 67 else ''}}>{{_('15 FPS')}}</option>
                        <option value="33" {{'selected' if det.interval_ms == 33 else ''}}>{{_('30 FPS')}}</option>
                    </select>
                </div>
            </div>
            <div class="row row--pair">
                <div class="field">
                    <label>{{_('Maximum people')}}</label>
                    <input id="detection-max-persons" type="number" name="max_persons" value="{{det.max_persons}}" min="1" max="50" step="1"
                           hx-get="/api/validate/detection/max_persons" hx-trigger="blur changed delay:200ms"
                           hx-target="#detection-max-persons-error" hx-swap="innerHTML" hx-include="closest form"
                           aria-describedby="detection-max-persons-error" aria-invalid="false">
                    <span id="detection-max-persons-error" class="field-error"></span>
                </div>
            </div>
        </div>

        <div class="group">
            <h3 class="group-title">{{_('Overlay')}}</h3>
            <div class="row row--toggles">
                <div class="field checkbox-field">
                    <label>{{_('Show boxes')}}</label>
                    <div class="checkbox-wrap"><input type="checkbox" name="show_boxes" {{'checked' if det.show_boxes else ''}}></div>
                </div>
                <div class="field checkbox-field">
                    <label>{{_('Show labels')}}</label>
                    <div class="checkbox-wrap"><input type="checkbox" name="show_labels" {{'checked' if det.show_labels else ''}}></div>
                </div>
            </div>
            <div class="row row--pair">
                <div class="field">
                    <label>{{_('Box color')}}</label>
                    %# Native picker replaced by circle-swatch full-variant.
                    %# Picked up by color-picker.js via data-color-picker;
                    %# hidden input carries form value. Inline validator dropped
                    %# (see analogous crosshair field in marker.tpl).
                    <button id="detection-box-color" type="button" class="color-swatch-trigger"
                            data-color-picker="full" data-value="{{det.box_color}}"
                            aria-label="{{_('Detection box color')}}"></button>
                    <input type="hidden" name="box_color" value="{{det.box_color}}">
                </div>
                <div class="field">
                    <label>{{_('Box thickness')}} (px)</label>
                    <input id="detection-box-thickness" type="number" name="box_thickness" value="{{det.box_thickness}}" min="1" max="10" step="1"
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

% include('partials/detection_mask_editor.tpl', config=config)
</div>
