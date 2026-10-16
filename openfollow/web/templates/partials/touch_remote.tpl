<form id="touch-remote-section" class="section {{'saved' if defined('saved') and saved else ''}}" data-fold-key="touch_remote" data-help="touch_remote"
      hx-post="/section/touch_remote" hx-target="#touch-remote-section" hx-swap="outerHTML" hx-trigger="submit">
    <div class="section-head">
        <h2>Touch Remote</h2>
        <span class="section-note">Drive a marker from a tablet during setup</span>
    </div>

    <div class="group">
        <div class="row">
            <div class="field checkbox-field">
                <label for="touch-remote-enabled">Touch remote</label>
                <div class="checkbox-wrap checkbox-field inline">
                    <input type="checkbox" id="touch-remote-enabled" name="enabled" {{'checked' if config.touch_remote.enabled else ''}}>
                    <label for="touch-remote-enabled">Allow browsers to drive markers</label>
                </div>
            </div>
        </div>
    </div>

    <div class="actions">
        <button type="submit" class="save-btn">Save</button>
    </div>
</form>
