%# Measure latency: one status box and the button. Polls only while a run the
%# operator started lasts, and swaps only itself, never the form around it.
%# Between runs the source-status poll sends its key and swaps it in out of band
%# when what it shows changed, such as video arriving.
% lat = latency
% running = lat['state'] == 'running'
<div id="video-source-latency" class="row"
     style="display:{{'none' if lat['gallery'] else ''}}"
% if defined('oob') and oob:
     hx-swap-oob="true"
% end
% if running:
     hx-get="/section/video_source/latency"
     hx-trigger="every 1s [!document.hidden], poll-shown from:body"
     hx-target="this"
     hx-swap="outerHTML"
% end
     >
%# Owned by no form (no such form id), so Save never sends it.
    <input type="hidden" id="video-source-latency-key" name="latency" value="{{lat['key']}}"
           form="video-source-latency-unsaved">
    <div class="field wide" data-error-box>
        <label>Camera Latency</label>
% if running:
        <div class="notice network-banner-busy">
            <span class="modal-spinner" aria-hidden="true"></span>
            <div>Measuring: {{lat['measured']}} of {{lat['total']}} changes seen.</div>
            <div class="notice-sub">Keep the camera pointed at the Operator Screen.</div>
        </div>
% elif lat['unavailable']:
        <div class="notice">
            <div>{{lat['unavailable']['text']}}</div>
            <div class="notice-sub">{{lat['unavailable']['action']}}</div>
        </div>
% elif lat['result']:
%     r = lat['result']
        <div class="notice {{lat['result_class']}}">
            <div>Round trip {{'%.0f' % r['median_ms']}} ms ({{'%.0f' % r['min_ms']}} to {{'%.0f' % r['max_ms']}} ms).</div>
%     if r['level'] != 'success':
            <div class="notice-sub">Lower the buffering of the camera and the stream, then measure again.</div>
%     end
        </div>
% elif lat['failure']:
%     f = lat['failure']
%#    Stopping is the operator's own choice, not a fault.
        <div class="notice {{'' if f['reason'] == 'stopped' else 'error'}}">
            <div>{{f['text']}}</div>
%     if f['action']:
            <div class="notice-sub">{{f['action']}}</div>
%     end
        </div>
% end
        <div>
% if running:
            <button type="button" class="secondary" hx-post="/section/video_source/latency/stop"
                    hx-target="#video-source-latency" hx-swap="outerHTML" hx-params="none"
                    data-error-lead="Not stopped.">Stop</button>
% else:
            <button type="button" class="secondary" hx-post="/section/video_source/latency"
                    hx-target="#video-source-latency" hx-swap="outerHTML" hx-params="none"
                    data-error-lead="Not started." {{'disabled' if lat['unavailable'] else ''}}>Measure latency</button>
% end
        </div>
    </div>
</div>
