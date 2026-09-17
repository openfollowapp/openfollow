%# The one video-failure box. Rendered on Live Statistics and on Camera & Grid,
%# which is where an operator looks first when the picture is missing, so both
%# must say the same thing rather than one carrying a reduced version.
%#
%# Two lines: what the station saw, then the one thing to try. The pipeline's
%# own wording ("Could not open resource for reading and writing.") is not here
%# - it reads as a second, unrelated fault and there is nothing an operator can
%# do with it. It stays in /api/stats and the diagnostics bundle, which is where
%# a support conversation reads it from. Where there is no classification it is
%# all we have, so it leads instead.
%#
%# ``scope`` namespaces the element id. Both hosts render this box on the same
%# page, and an id repeated in one document makes ``hx-preserve`` resolve to
%# whichever came first - so the Statistics poll moved the Video Source box's
%# node out from under it once a second.
%#
%# Everything sits INSIDE the preserved node, keyed on what the box says. Both
%# hosts poll, and content left outside is rebuilt on every swap, which the eye
%# reads as a flicker.
%#
%# The reconnect counter is deliberately absent: it changed on every attempt, so
%# it both resized the box and defeated the preserve. Statistics already
%# distinguishes a source still retrying from one that has given up, in its
%# Pipeline row, without anything that moves.
%#
%# Params: failure_text, error_message, action, token, scope, assertive.
%
        <div class="notice error">
            <div id="video-error-{{scope}}-{{token}}" hx-preserve="true"
                 role="{{'alert' if assertive else 'status'}}"
                 aria-live="{{'assertive' if assertive else 'polite'}}"
                 aria-atomic="true">
                <div>{{failure_text or error_message}}</div>
% if action:
                <div class="notice-sub">{{action}}</div>
% end
            </div>
        </div>
