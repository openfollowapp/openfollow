%# What the Video Source section's poll swaps in: the failure box, then the box
%# for a source over the station's limits. Keyed on what there is to show:
%# several receiver paths publish an error with no classification, and
%# Statistics renders that raw text, so this box must too.
%# Params: failure_text, error_message, failure_action, limits_text, limits_action.
% from openfollow.web.labels import video_error_token
% if failure_text or error_message:
%     include('partials/video_error_box.tpl', failure_text=failure_text, error_message=error_message, action=failure_action, token=video_error_token(failure_text, error_message, failure_action), scope='source', live='status')
% end
% if limits_text:
%     include('partials/video_error_box.tpl', failure_text=limits_text, error_message='', action=limits_action, token=video_error_token(limits_text, '', limits_action), scope='source-limits', live='status')
% end
