%# A run's progress, polled while it lasts; once it ends the answer is the whole dialog in its outcome.
<div id="latency-progress" class="notice network-banner-busy"
     hx-get="/section/video_source/latency/dialog?poll=1"
     hx-trigger="every 1s [!document.hidden], poll-shown from:body"
     hx-target="this"
     hx-swap="outerHTML">
    <span class="modal-spinner" aria-hidden="true"></span>
    <div>Measuring: {{d['measured']}} of {{d['total']}} changes seen.</div>
    <div class="notice-sub">Keep the camera pointed at the Operator Screen.</div>
</div>
