<form id="mouse-section" class="section experimental-feature {{'saved' if defined('saved') and saved else ''}}" data-fold-key="mouse" data-help="mouse"
      hx-post="/section/mouse" hx-target="#mouse-section" hx-swap="outerHTML" hx-trigger="submit">
    <div class="section-head">
        <h2>Mouse Input <span class="badge-experimental">Experimental</span></h2>
        <span class="section-note">Mouse controls for the on-display UI</span>
    </div>

    <div class="group">
        <div class="row">
            <div class="field checkbox-field">
                <label>Enabled</label>
                <div class="checkbox-wrap"><input type="checkbox" name="mouse_enabled" {{'checked' if config.controller.mouse_enabled else ''}}></div>
            </div>
        </div>
    </div>

    <div class="actions">
        <button type="submit" class="save-btn">Save</button>
    </div>
</form>
