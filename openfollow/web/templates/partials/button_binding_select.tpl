% from openfollow.web.labels import pretty_label
% field_id = 'gamepad-' + name.replace('_', '-')
<div class="field">
    <label for="{{field_id}}">{{label}}</label>
    <select id="{{field_id}}" name="{{name}}"
            hx-get="/api/validate/gamepad/{{name}}" hx-trigger="change"
            hx-target="#{{field_id}}-error" hx-swap="innerHTML" hx-include="closest form"
            aria-describedby="{{field_id}}-error" aria-invalid="false">
        <option value="" {{'selected' if not value else ''}}>–</option>
        % for btn in buttons:
        <option value="{{btn}}" {{'selected' if value == btn else ''}}>{{pretty_label(btn)}}</option>
        % end
    </select>
    <span id="{{field_id}}-error" class="field-error"></span>
</div>
