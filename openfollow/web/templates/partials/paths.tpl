<form id="paths-section" data-feature="paths" class="section {{'saved' if defined('saved') and saved else ''}}" data-fold-key="paths" data-help="paths"
      hx-post="/section/paths" hx-target="#paths-section" hx-swap="outerHTML" hx-trigger="submit">
    <div class="section-head">
        <h2>Paths <span class="badge-experimental">Experimental</span></h2>
        <span class="section-note">Paths a marker can be locked onto</span>
    </div>

    <div class="group">
        <h3 class="group-title">Global</h3>
        <div class="row">
            <div class="field checkbox-field">
                <label for="paths-show-paths">Operator Screen</label>
                <div class="checkbox-wrap checkbox-field inline">
                    <input type="checkbox" id="paths-show-paths" name="show_paths" {{'checked' if config.paths.show_paths else ''}}>
                    <label for="paths-show-paths">Show paths</label>
                </div>
            </div>
            <div class="field">
                <label for="paths-slide-time">Slide time (s)</label>
                <input id="paths-slide-time" type="number" name="slide_time" value="{{config.paths.slide_time}}" min="0" max="10" step="0.1"
                       hx-get="/api/validate/paths/slide_time" hx-trigger="blur changed delay:200ms"
                       hx-target="#paths-slide-time-error" hx-swap="innerHTML" hx-include="closest form"
                       aria-describedby="paths-slide-time-error" aria-invalid="false">
                <span id="paths-slide-time-error" class="field-error"></span>
            </div>
        </div>
    </div>

    <div class="actions">
        <button type="submit" class="save-btn">Save</button>
    </div>
</form>
