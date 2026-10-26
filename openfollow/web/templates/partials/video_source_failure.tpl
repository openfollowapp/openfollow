%# The video-failure box at the top of a Video Source form: the Camera & Grid tab
%# and the Setup Wizard's Video Source step. Both are where the fix gets typed in,
%# so they carry the same box Live Statistics does rather than a reduced version.
%# Already credential-free - the status marker redacts on the way in.
% video_failure_text = str(defined("video_failure_text") and video_failure_text or "")
% video_error_message = str(defined("video_error_message") and video_error_message or "")
% video_failure_action = str(defined("video_failure_action") and video_failure_action or "")
% video_limits_text = str(defined("video_limits_text") and video_limits_text or "")
% video_limits_action = str(defined("video_limits_action") and video_limits_action or "")
%# Polled on its own so a failure that starts while this page is open still
%# shows up. Only the box is swapped - re-rendering the form would discard
%# whatever the operator is part-way through typing, which is what they came
%# here to do. Only while it shows: a hidden tab or wizard step asks nothing.
<div id="video-source-failure"
     hx-get="/section/video_source/failure"
     hx-trigger="every 3s [pollShown(this)], poll-shown[pollShown(this)] from:body"
     hx-target="this"
     hx-swap="innerHTML">
% include('partials/video_source_boxes.tpl', failure_text=video_failure_text, error_message=video_error_message, failure_action=video_failure_action, limits_text=video_limits_text, limits_action=video_limits_action)
</div>
