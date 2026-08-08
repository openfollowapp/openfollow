<form id="operator-messages-section" class="section {{'saved' if defined('saved') and saved else ''}}" data-fold-key="operator_messages" data-help="operator_messages"
      hx-post="/section/operator_messages" hx-target="#operator-messages-section" hx-swap="outerHTML" hx-trigger="submit">
    <div class="section-head">
        <h2>{{_('Operator Messages')}}</h2>
        <span class="section-note">{{_('OSC-driven next-cue text shown on the Operator Screen')}}</span>
    </div>

    <div class="group">
        <div class="row">
            <div class="field checkbox-field">
                <label>{{_('Enabled')}}</label>
                <div class="checkbox-wrap"><input type="checkbox" name="enabled" {{'checked' if config.operator_messages.enabled else ''}}></div>
            </div>
            <div class="field checkbox-field">
                <label>{{_('Route by marker')}}</label>
                <div class="checkbox-wrap"><input type="checkbox" name="route_by_marker" {{'checked' if config.operator_messages.route_by_marker else ''}}></div>
            </div>
            <div class="field">
                <label>{{_('Placement')}}</label>
                <select name="position">
                    <option value="bottom" {{'selected' if config.operator_messages.position == 'bottom' else ''}}>{{_('Bottom-center')}}</option>
                    <option value="top" {{'selected' if config.operator_messages.position == 'top' else ''}}>{{_('Top-center')}}</option>
                </select>
            </div>
            <div class="field">
                <label>{{_('Max visible')}}</label>
                <input id="operator-messages-max-visible" type="number" name="max_visible"
                       value="{{config.operator_messages.max_visible}}" min="1" max="20" step="1"
                       hx-get="/api/validate/operator_messages/max_visible" hx-trigger="blur changed delay:200ms"
                       hx-target="#operator-messages-max-visible-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="operator-messages-max-visible-error" aria-invalid="false">
                <span id="operator-messages-max-visible-error" class="field-error"></span>
            </div>
            <div class="field">
                <label>{{_('Scale')}}</label>
                <select name="scale">
                    <option value="1.0" {{'selected' if config.operator_messages.scale == 1.0 else ''}}>{{_('1×')}}</option>
                    <option value="1.25" {{'selected' if config.operator_messages.scale == 1.25 else ''}}>{{_('1.25×')}}</option>
                    <option value="1.5" {{'selected' if config.operator_messages.scale == 1.5 else ''}}>{{_('1.5×')}}</option>
                    <option value="1.75" {{'selected' if config.operator_messages.scale == 1.75 else ''}}>{{_('1.75×')}}</option>
                    <option value="2.0" {{'selected' if config.operator_messages.scale == 2.0 else ''}}>{{_('2×')}}</option>
                </select>
            </div>
        </div>
    </div>

    <div class="actions">
        <button type="submit" class="save-btn">{{_('Save')}}</button>
    </div>
</form>
