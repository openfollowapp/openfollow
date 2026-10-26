%# Measure latency: one status box and the button. Polls only while a run the
%# operator started lasts, and swaps only itself, never the form around it.
% lat = latency
% running = lat['state'] == 'running'
<div id="video-source-latency" class="row" data-error-lead="Not started."
     style="display:{{'none' if lat['gallery'] else ''}}"
% if running:
     hx-get="/section/video_source/latency"
     hx-trigger="every 1s [!document.hidden], poll-shown from:body"
     hx-target="this"
     hx-swap="outerHTML"
% end
     >
    <div class="field wide">
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
                    hx-target="#video-source-latency" hx-swap="outerHTML" hx-params="none">Stop</button>
% else:
            <button type="button" class="secondary" hx-post="/section/video_source/latency"
                    hx-target="#video-source-latency" hx-swap="outerHTML" hx-params="none"
                    {{'disabled' if lat['unavailable'] else ''}}>Measure latency</button>
% end
        </div>
    </div>
</div>
