%# Length inputs render in active unit system. Imperial mode uses free-text
%# input (number input can't hold "16 ft 4.85 in"); Stored: X.XXX m echo
%# shows canonical metric value. Metric rendering unchanged.
% from openfollow.units import UnitSystem, format_length, metric_echo, unit_suffix_length
% _us = UnitSystem(config.ui.unit_system)
% _imp = _us is UnitSystem.IMPERIAL
% _len = unit_suffix_length(_us)
<form id="grid-section" class="section {{'saved' if defined('saved') and saved else ''}}" data-fold-key="grid" data-help="grid"
      data-template-form="1"
      hx-post="/section/grid" hx-target="#grid-section" hx-swap="outerHTML" hx-trigger="submit">
    <div class="section-head">
        <h2>{{_('Grid')}}</h2>
        <span class="section-note">{{_('Stage reference plane settings')}}</span>
    </div>

    <div class="group">
        <h3 class="group-title">{{_('Dimensions')}}</h3>
        <div class="row">
            <div class="field">
                <label for="grid-width">{{_('Width')}} ({{_len}})</label>
                <input id="grid-width" type="{{'text' if _imp else 'number'}}" name="width" value="{{format_length(config.grid.width, _us) if _imp else config.grid.width}}" min="0.1" step="any"
                       hx-get="/api/validate/grid/width" hx-trigger="blur changed delay:200ms"
                       hx-target="#grid-width-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="grid-width-error" aria-invalid="false">
                <span id="grid-width-error" class="field-error"></span>
                % if _imp:
                <small class="metric-echo">Stored: {{metric_echo(config.grid.width)}}</small>
                % end
            </div>
            <div class="field">
                <label for="grid-depth">{{_('Depth')}} ({{_len}})</label>
                <input id="grid-depth" type="{{'text' if _imp else 'number'}}" name="depth" value="{{format_length(config.grid.depth, _us) if _imp else config.grid.depth}}" min="0.1" step="any"
                       hx-get="/api/validate/grid/depth" hx-trigger="blur changed delay:200ms"
                       hx-target="#grid-depth-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="grid-depth-error" aria-invalid="false">
                <span id="grid-depth-error" class="field-error"></span>
                % if _imp:
                <small class="metric-echo">Stored: {{metric_echo(config.grid.depth)}}</small>
                % end
            </div>
            <div class="field">
                <label for="grid-max-height">{{_('Maximum Height')}} ({{_len}}) ({{_('optional')}})</label>
                <input id="grid-max-height" type="{{'text' if _imp else 'number'}}" name="max_height" value="{{format_length(config.grid.max_height, _us) if _imp else config.grid.max_height}}" step="any"
                       hx-get="/api/validate/grid/max_height" hx-trigger="blur changed delay:200ms"
                       hx-target="#grid-max-height-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="grid-max-height-error" aria-invalid="false">
                <span id="grid-max-height-error" class="field-error"></span>
                % if _imp:
                <small class="metric-echo">Stored: {{metric_echo(config.grid.max_height)}}</small>
                % end
            </div>
            <div class="field">
                <label for="grid-spacing">{{_('Spacing')}} ({{_len}})</label>
                <input id="grid-spacing" type="{{'text' if _imp else 'number'}}" name="spacing" value="{{format_length(config.grid.spacing, _us) if _imp else config.grid.spacing}}" min="0.1" step="any"
                       hx-get="/api/validate/grid/spacing" hx-trigger="blur changed delay:200ms"
                       hx-target="#grid-spacing-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="grid-spacing-error" aria-invalid="false">
                <span id="grid-spacing-error" class="field-error"></span>
                % if _imp:
                <small class="metric-echo">Stored: {{metric_echo(config.grid.spacing)}}</small>
                % end
            </div>
        </div>
    </div>

    <div class="group">
        <h3 class="group-title">{{_('Appearance')}}</h3>
        <div class="row">
            <div class="field">
                <label for="grid-color">{{_('Line Color')}}</label>
                %# Native picker replaced by circle-swatch greys-variant.
                %# Hidden input carries form value; color-picker.js auto-attaches
                %# via data-color-picker. Inline validator dropped (see marker.tpl).
                <button id="grid-color" type="button" class="color-swatch-trigger"
                        data-color-picker="greys" data-value="{{config.grid.color}}"
                        aria-label="Grid line colour"></button>
                <input type="hidden" name="color" value="{{config.grid.color}}">
            </div>
            <div class="field">
                <label for="grid-thickness">{{_('Line Thickness')}} (px)</label>
                <input id="grid-thickness" type="number" name="thickness" value="{{config.grid.thickness}}" min="1" max="20" step="1"
                       hx-get="/api/validate/grid/thickness" hx-trigger="blur changed delay:200ms"
                       hx-target="#grid-thickness-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="grid-thickness-error" aria-invalid="false">
                <span id="grid-thickness-error" class="field-error"></span>
            </div>
            <div class="field">
                <label for="grid-transparency">{{_('Transparency')}} (0–1)</label>
                <input id="grid-transparency" type="number" name="transparency" value="{{config.grid.transparency}}" min="0" max="1" step="any"
                       hx-get="/api/validate/grid/transparency" hx-trigger="blur changed delay:200ms"
                       hx-target="#grid-transparency-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="grid-transparency-error" aria-invalid="false">
                <span id="grid-transparency-error" class="field-error"></span>
            </div>
        </div>
    </div>

    <div class="group">
        <h3 class="group-title">{{_('Offset Position')}}</h3>
        <div class="row">
            <div class="field">
                <label for="grid-x-offset">{{_("X Offset")}} ({{_len}})</label>
                <input id="grid-x-offset" type="{{'text' if _imp else 'number'}}" name="x_offset" value="{{format_length(config.grid.x_offset, _us) if _imp else config.grid.x_offset}}" step="any"
                       hx-get="/api/validate/grid/x_offset" hx-trigger="blur changed delay:200ms"
                       hx-target="#grid-x-offset-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="grid-x-offset-error" aria-invalid="false">
                <span id="grid-x-offset-error" class="field-error"></span>
                % if _imp:
                <small class="metric-echo">Stored: {{metric_echo(config.grid.x_offset)}}</small>
                % end
            </div>
            <div class="field">
                <label for="grid-y-offset">{{_("Y Offset")}} ({{_len}})</label>
                <input id="grid-y-offset" type="{{'text' if _imp else 'number'}}" name="y_offset" value="{{format_length(config.grid.y_offset, _us) if _imp else config.grid.y_offset}}" step="any"
                       hx-get="/api/validate/grid/y_offset" hx-trigger="blur changed delay:200ms"
                       hx-target="#grid-y-offset-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="grid-y-offset-error" aria-invalid="false">
                <span id="grid-y-offset-error" class="field-error"></span>
                % if _imp:
                <small class="metric-echo">Stored: {{metric_echo(config.grid.y_offset)}}</small>
                % end
            </div>
            <div class="field">
                <label for="grid-z-offset">{{_("Z Offset")}} ({{_len}})</label>
                <input id="grid-z-offset" type="{{'text' if _imp else 'number'}}" name="z_offset" value="{{format_length(config.grid.z_offset, _us) if _imp else config.grid.z_offset}}" step="any"
                       hx-get="/api/validate/grid/z_offset" hx-trigger="blur changed delay:200ms"
                       hx-target="#grid-z-offset-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="grid-z-offset-error" aria-invalid="false">
                <span id="grid-z-offset-error" class="field-error"></span>
                % if _imp:
                <small class="metric-echo">Stored: {{metric_echo(config.grid.z_offset)}}</small>
                % end
            </div>
        </div>
    </div>

    <div class="group">
        <h3 class="group-title">{{_('Origin Marker')}}</h3>
        <div class="row">
            <div class="field checkbox-field">
                <label>{{_('Show Origin')}}</label>
                <div class="checkbox-wrap"><input type="checkbox" name="origin_visible" {{'checked' if config.grid.origin_visible else ''}}></div>
            </div>
            <div class="field">
                <label for="grid-origin-length">{{_('Length')}} ({{_len}})</label>
                <input id="grid-origin-length" type="{{'text' if _imp else 'number'}}" name="origin_length" value="{{format_length(config.grid.origin_length, _us) if _imp else config.grid.origin_length}}" min="0.1" step="any"
                       hx-get="/api/validate/grid/origin_length" hx-trigger="blur changed delay:200ms"
                       hx-target="#grid-origin-length-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="grid-origin-length-error" aria-invalid="false">
                <span id="grid-origin-length-error" class="field-error"></span>
                % if _imp:
                <small class="metric-echo">Stored: {{metric_echo(config.grid.origin_length)}}</small>
                % end
            </div>
            <div class="field">
                <label for="grid-origin-thickness">{{_('Thickness')}} (px)</label>
                <input id="grid-origin-thickness" type="number" name="origin_thickness" value="{{config.grid.origin_thickness}}" min="1" max="20"
                       hx-get="/api/validate/grid/origin_thickness" hx-trigger="blur changed delay:200ms"
                       hx-target="#grid-origin-thickness-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="grid-origin-thickness-error" aria-invalid="false">
                <span id="grid-origin-thickness-error" class="field-error"></span>
            </div>
        </div>
    </div>

    <div class="actions">
        <button type="submit" class="save-btn">{{_('Save')}}</button>
        <button type="button" class="broadcast-btn" onclick="broadcastSection('grid', this.form)">{{_('Apply to all stations')}}</button>
        <!-- Save / Load template buttons (same as camera section).
             Both bind to same ``camera_grid`` modal flow (template captures
             both sections). Helpers on window (base.tpl) so onclick doesn't
             depend on load order. -->
        <button type="button" class="secondary"
                data-template-save
                data-template-deps="#camera-section, #grid-section"
                onclick="window.cameraGridSaveTemplate()">Save as template…</button>
        <button type="button" class="secondary"
                onclick="window.cameraGridLoadTemplate()">Load template…</button>
    </div>
</form>
