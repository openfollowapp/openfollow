%# The display unit choice, shared by General and the Setup Wizard's Grid Setup.
<label for="{{select_id}}">Displayed unit system</label>
<select id="{{select_id}}" name="unit_system">
    <option value="metric" {{'selected' if unit_system == 'metric' else ''}}>Metric (m, m/s)</option>
    <option value="imperial" {{'selected' if unit_system == 'imperial' else ''}}>Imperial (ft / in, ft/s)</option>
</select>
