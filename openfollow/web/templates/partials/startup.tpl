%# Start-at-boot switch. Rendered into the #startup-settings region (heading
%# and fold live in general.tpl), swapped in place after every change so the
%# switch shows what systemd reports rather than a stored flag.
%#
%# Tolerant of missing context: callers without ``startup`` get the
%# unavailable state instead of a NameError.
% _s = startup if defined('startup') else {"available": False, "enabled": False, "reason": ""}
% _banner = _s.get("banner")
% _failed = bool(_banner) and _banner.get("kind") == "error"
% if _banner:
<div class="notice {{'error' if _failed else ''}}"
     role="{{'alert' if _failed else 'status'}}"
     aria-live="{{'assertive' if _failed else 'polite'}}"
     aria-atomic="true">{{_banner.get('text', '')}}</div>
% end
% if not _s.get("available"):
<p class="muted">{{_s.get('reason') or 'Starting at boot cannot be changed on this host.'}}</p>
% else:
<div class="row">
    <div class="field checkbox-field wide">
        <label for="general-autostart">Start OpenFollow at boot</label>
        <div class="checkbox-wrap">
            <input type="checkbox" id="general-autostart" name="autostart"
                   {{'checked' if _s.get('enabled') else ''}}
                   onchange="onAutostartToggle(this)">
        </div>
    </div>
</div>
% end
