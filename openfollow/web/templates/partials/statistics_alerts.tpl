%# Speaks the Live Statistics alert boxes, which carry no role of their own.
%# It sits outside the panel's 1 s swap, and its poll answers 204 while the
%# text is unchanged, so each change is announced once (web/live_alerts.py).
% from openfollow.web.live_alerts import statistics_alerts
% alerts = statistics_alerts(stats)
<div id="statistics-alerts" class="visually-hidden" role="alert" aria-live="assertive" aria-atomic="true"
     hx-get="/section/statistics/alerts?key={{alerts.key()}}" hx-trigger="every 1s" hx-target="this" hx-swap="outerHTML">
% for line in alerts.spoken():
    <p>{{line}}</p>
% end
</div>
