%# The Measure Camera Latency dialog, in the shared modal (#modal-root): its body
%# here, its footer out of band, and the modal locked while a run lasts. A run
%# ends by itself within seconds, so it has no way out but its end.
% d = dialog
% running = d['state'] == 'running'
% warning = str(defined('warning') and warning or '')
% if defined('progress_only') and progress_only:
%     include('partials/video_source_latency_progress.tpl', d=d)
% else:
<div id="latency-dialog" tabindex="-1">
% if d['state'] == 'intro':
%     if warning:
%#    Renews the Operator Screen's warning card while these warnings show.
    <div hidden hx-get="/section/video_source/latency/warning?token={{warning}}"
         hx-trigger="every 1s [!document.hidden], poll-shown from:body" hx-swap="none"></div>
%     end
    <div class="notice warning">
        <div class="latency-hazard">WARNING: PHOTOSENSITIVITY / EPILEPSY SEIZURES</div>
        <div class="notice-sub">The Operator Screen flashes black and white several times. Flashing light can cause seizures in some people, including people with no history of epilepsy. Make sure nobody is watching the Operator Screen or a display that shows it.</div>
    </div>
    <div class="notice warning">
        <div>Never during a show.</div>
        <div class="notice-sub">For about 5 seconds the Operator Screen shows no video and no markers, so nobody can follow a performer on it.</div>
    </div>
    <p>The station flashes its Operator Screen and times each flash in the camera's picture. The result is the round trip from the screen, through the camera and the stream, back to the station.</p>
    <ol>
        <li>Point the camera at this station's Operator Screen, so the screen fills a good part of the picture.</li>
        <li>Press Start and keep the camera still until the result shows.</li>
    </ol>
% elif running:
%     include('partials/video_source_latency_progress.tpl', d=d)
    <p class="modal-note">The Operator Screen is flashing. It shows the video again when the measurement ends.</p>
% elif d['state'] == 'done':
%     r = d['result']
    <div class="notice {{d['result_class']}}">
        <div>Round trip {{'%.0f' % r['median_ms']}} ms ({{'%.0f' % r['min_ms']}} to {{'%.0f' % r['max_ms']}} ms).</div>
%     if r['level'] == 'success':
        <div class="notice-sub">Measured over {{r['samples']}} changes.</div>
%     else:
        <div class="notice-sub">Lower the buffering of the camera and the stream, then measure again.</div>
%     end
    </div>
% else:
%     f = d['failure'] or {}
    <div class="notice error">
        <div>{{f.get('text', '')}}</div>
%     if f.get('action'):
        <div class="notice-sub">{{f['action']}}</div>
%     end
    </div>
% end
</div>
<div class="modal-footer" id="modal-footer" hx-swap-oob="true">
% if d['state'] == 'intro':
    <button type="button" onclick="closeModal()">Cancel</button>
    <button type="button" class="primary" hx-post="/section/video_source/latency"
            hx-target="#latency-dialog" hx-swap="outerHTML" hx-vals='{"token": "{{warning}}"}'
            data-error-lead="Not started.">Start</button>
% elif not running:
    <button type="button" hx-post="/section/video_source/latency"
            hx-target="#latency-dialog" hx-swap="outerHTML"
            data-error-lead="Not started.">{{'Measure again' if d['state'] == 'done' else 'Try again'}}</button>
    <button type="button" class="primary" onclick="closeModal()">Close</button>
% end
</div>
<script>ofLatencyDialogShown({{'true' if running else 'false'}}, '{{warning}}');</script>
% end
