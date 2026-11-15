%# Speaks the Status & Diagnostics alert boxes, which carry no role of their own.
%# It sits outside the boxes' 1 s swap, and its poll answers 204 while the
%# text is unchanged, so each change is announced once (web/live_alerts.py).
% from openfollow.web.live_alerts import diagnostics_alerts
% alerts = diagnostics_alerts(stats)
<div id="diagnostics-alerts" class="visually-hidden" role="alert" aria-live="assertive" aria-atomic="true"
     hx-get="/section/diagnostics/alerts?key={{alerts.key()}}" hx-trigger="every 1s [pollShown(this)], poll-shown[pollShown(this)] from:body" hx-target="this" hx-swap="outerHTML">
% for line in alerts.spoken():
    <p>{{line}}</p>
% end
</div>
