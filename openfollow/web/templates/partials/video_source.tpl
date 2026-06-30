<form id="video-source-section" class="section {{'saved' if defined('saved') and saved else ''}}" data-fold-key="video_source" data-help="video_source"
      hx-post="/section/video_source" hx-target="#video-source-section" hx-swap="outerHTML" hx-trigger="submit">
    <div class="section-head">
        <h2>Video Source</h2>
        <span class="section-note">Camera input and live preview</span>
    </div>

    <div class="group">
        <div class="row">
            <div class="field">
                <label>Source Type</label>
                <select name="video_source_type" id="video-source-type"
                        onchange="ofVideoSourceToggle(this.value)">
                    % for iid, iname in available_inputs:
                    <option value="{{iid}}" {{'selected' if config.video_source_type == iid else ''}}>{{iname}}</option>
                    % end
                </select>
            </div>
        </div>
        % for iid, html_fragment in input_html_fragments.items():
        <div data-input-type="{{iid}}" style="display:{{'none' if config.video_source_type != iid else ''}}">
            {{!html_fragment}}
        </div>
        % end
        <!-- Capture is for live sources only; hidden when the Media Gallery
             (testpattern) is selected, and toggled by the Source Type onchange. -->
        <div class="row" id="capture-frame-row" style="margin-top:0.5rem;display:{{'none' if config.video_source_type == 'testpattern' else ''}}">
            <div class="field wide">
                <button type="button" class="btn-link capture-btn"
                        hx-post="/video-input/testpattern/capture" hx-swap="none"
                        hx-on::after-request="window.openfollowCaptureFeedback(event)">Capture frame to gallery</button>
                <span id="capture-feedback" class="field-note" role="status" aria-live="polite"></span>
            </div>
        </div>
        <script>
        window.openfollowCaptureFeedback = function(evt){
            var fb = document.getElementById('capture-feedback');
            if(!fb) return;
            var msg = 'Capture failed.';
            try { var d = JSON.parse(evt.detail.xhr.responseText || '{}');
                  msg = d.ok ? 'Captured frame saved to the gallery.' : (d.error || msg); } catch(e){}
            fb.textContent = msg;
        };
        // Source Type drives which controls apply: capture (live sources only),
        // connection recovery (network sources only), preview (not the gallery).
        window.ofVideoSourceToggle = function(value){
            document.querySelectorAll('[data-input-type]').forEach(function(el){
                el.style.display = el.dataset.inputType === value ? '' : 'none';
            });
            var net = (value === 'rtsp' || value === 'srt' || value === 'rtp');
            function vis(id, on){ var el = document.getElementById(id); if(el) el.style.display = on ? '' : 'none'; }
            vis('capture-frame-row', value !== 'testpattern');
            vis('recovery-row', net);
            vis('preview-row', value !== 'testpattern');
        };
        </script>
        <!-- Connection recovery applies to network inputs only (RTSP/SRT/RTP). -->
        <div class="row" id="recovery-row" style="display:{{'' if config.video_source_type in ('rtsp', 'srt', 'rtp') else 'none'}}">
            <div class="field">
                <label>Stall Timeout (s)</label>
                <input type="number" name="stall_timeout" value="{{config.stall_timeout}}"
                       min="0" step="0.1" placeholder="3.0">
            </div>
            <div class="field">
                <label>Heal Interval (s)</label>
                <input type="number" name="heal_interval" value="{{config.heal_interval}}"
                       min="0" step="0.1" placeholder="5.0">
            </div>
        </div>
        <!-- Live preview is redundant for the Media Gallery's static content. -->
        <div class="row" id="preview-row" style="margin-top:0.5rem;display:{{'none' if config.video_source_type == 'testpattern' else ''}}">
            <div class="field checkbox-field inline">
                <input type="checkbox" id="show-preview-cb">
                <label for="show-preview-cb">Show Preview</label>
            </div>
        </div>
        <div id="video-preview-wrap" style="display:none;">
            <div class="row">
                <div class="field" style="min-width:100%;">
                    <img id="video-preview" class="video-preview" alt="Video preview">
                    <span id="video-preview-hint" class="video-preview-hint">No preview available – waiting for video source.</span>
                </div>
            </div>
        </div>
        <script>
        (function(){
            var cb = document.getElementById('show-preview-cb');
            var wrap = document.getElementById('video-preview-wrap');
            var img = document.getElementById('video-preview');
            var hint = document.getElementById('video-preview-hint');
            var timer = null;
            function isExpanded(){
                var sec = document.getElementById('video-source-section');
                return sec && !sec.classList.contains('is-collapsed');
            }
            function refresh(){
                if(!cb.checked || !isExpanded()) return;
                var next = new Image();
                next.onload = function(){
                    img.src = next.src;
                    img.style.display = 'block';
                    hint.style.display = 'none';
                };
                next.onerror = function(){
                    img.style.display = 'none';
                    hint.style.display = '';
                };
                next.src = '/api/video/snapshot?t=' + Date.now();
            }
            function toggle(){
                if(cb.checked){
                    wrap.style.display = '';
                    refresh();
                    if(!timer) timer = setInterval(refresh, 2000);
                } else {
                    wrap.style.display = 'none';
                    img.style.display = 'none';
                    hint.style.display = 'none';
                    if(timer){ clearInterval(timer); timer = null; }
                }
            }
            cb.addEventListener('change', toggle);
        })();
        </script>
    </div>

    <div class="actions">
        <button type="submit" class="save-btn">Save</button>
    </div>
</form>
