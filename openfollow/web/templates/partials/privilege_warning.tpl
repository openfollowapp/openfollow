%# Privileged actions that can't run (privilege/readiness.py), beside the unsupported-platform box.
% if privilege_warning:
<div class="notice error privilege-warning">
    <div>
        <div>{{privilege_warning.title}}</div>
        <div class="notice-sub">{{privilege_warning.step}}</div>
    </div>
    <button type="button" class="section-help-btn" data-help-open="system-permissions" aria-label="How to fix this over SSH">?</button>
</div>
% end
