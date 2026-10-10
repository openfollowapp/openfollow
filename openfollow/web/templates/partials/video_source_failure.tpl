%# The video-failure box at the top of a Video Source form: the Camera & Grid tab
%# and the Setup Wizard's Video Source step. Both are where the fix gets typed in,
%# so they carry the same box Live Statistics does rather than a reduced version.
%# Already credential-free - the status marker redacts on the way in.
% from openfollow.web.labels import video_error_token
% video_failure_text = str(defined("video_failure_text") and video_failure_text or "")
% video_error_message = str(defined("video_error_message") and video_error_message or "")
% video_failure_action = str(defined("video_failure_action") and video_failure_action or "")
%# Keyed on what there is to show. Several receiver paths publish an error
%# with no classification, and Statistics renders that raw text; suppressing it
%# here would leave this box empty while the other answered the question.
% show_failure = bool(video_failure_text or video_error_message)
%# Polled on its own so a failure that starts while this page is open still
%# shows up. Only the box is swapped - re-rendering the form would discard
%# whatever the operator is part-way through typing, which is what they came
%# here to do. Only while it shows: a hidden tab or wizard step asks nothing.
<div id="video-source-failure"
     hx-get="/section/video_source/failure"
     hx-trigger="every 3s [this.offsetParent !== null]"
     hx-target="this"
     hx-swap="innerHTML">
% if show_failure:
%     token = video_error_token(video_failure_text, video_error_message, video_failure_action)
%     include('partials/video_error_box.tpl', failure_text=video_failure_text, error_message=video_error_message, action=video_failure_action, token=token, scope='source', live='status')
% end
</div>
