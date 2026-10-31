%# The unsupported platform (platform_support.py), between a page's navigation and its panels.
% from openfollow.platform_support import STEP
% if platform_warning:
<div class="notice error platform-warning">
    <div>{{platform_warning.title}}</div>
    <div class="notice-sub">{{STEP}}</div>
</div>
% end
