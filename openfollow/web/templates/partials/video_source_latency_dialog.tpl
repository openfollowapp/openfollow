%# The Measure Camera Latency dialog, in the shared modal (#modal-root): its body
%# here with its footer, which ofLatencyDialogShown moves into the modal's once
%# this state is the open dialog's, and the modal locked while a run lasts. A run
%# ends by itself (about 5 s, never past about 35 s), so it has no way out but its end.
% d = dialog
% running = d['state'] == 'running'
% warning = str(defined('warning') and warning or '')
% if defined('progress_only') and progress_only:
%     include('partials/video_source_latency_progress.tpl', d=d)
% else:
<div id="latency-dialog" tabindex="-1" data-warning="{{warning}}">
% if d['state'] == 'intro':
%     if warning:
%#    Puts the Operator Screen's warning card up and keeps it there while these warnings show in an
%#    open dialog; closing the dialog removes it. A failed renewal is no one's to act on.
    <div id="latency-warning-renewal" hidden data-error-quiet
         hx-post="/section/video_source/latency/warning" hx-vals='{"token": "{{warning}}"}' hx-swap="none"
         hx-trigger="load[ofLatencyWarningLive(this)], every 1s [!document.hidden && ofLatencyWarningLive(this)], poll-shown[ofLatencyWarningLive(this)] from:body"></div>
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
%# A start locks the modal at once, so no close slips in before the run's answer; a refused one releases it.
    <div hidden data-latency-footer>
% if d['state'] == 'intro':
        <button type="button" onclick="closeModal()" data-autofocus>Cancel</button>
        <button type="button" class="primary" hx-post="/section/video_source/latency"
                hx-target="#latency-dialog" hx-swap="outerHTML" hx-vals='{"token": "{{warning}}"}'
                hx-on::before-request="modalLock(true)"
                hx-on::after-request="if (!event.detail.successful) modalLock(false)"
                data-error-lead="Not started.">Start</button>
% elif not running:
        <button type="button" hx-post="/section/video_source/latency"
                hx-target="#latency-dialog" hx-swap="outerHTML"
                hx-on::before-request="modalLock(true)"
                hx-on::after-request="if (!event.detail.successful) modalLock(false)"
                data-error-lead="Not started.">{{'Measure again' if d['state'] == 'done' else 'Try again'}}</button>
        <button type="button" class="primary" onclick="closeModal()" data-autofocus>Close</button>
% end
    </div>
</div>
<script>ofLatencyDialogShown({{'true' if running else 'false'}}, '{{warning}}');</script>
% end
