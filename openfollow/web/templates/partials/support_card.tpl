%# Support OpenFollow card: docked beside Continue in What's new (``compact``), in full on About.
%# Params: compact.
% from openfollow.runtime.overlay_links import SUPPORT, link_qr_svg
<aside class="support-card{{' support-card--compact' if compact else ''}}" aria-label="Support OpenFollow">
    {{!link_qr_svg(SUPPORT)}}
    <div class="support-card-text">
        <span class="support-card-eyebrow"><svg class="support-heart" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 21s-7.6-4.7-9.7-9.5C.8 8.1 3 4 6.9 4c2.1 0 3.6 1.1 5.1 3 1.5-1.9 3-3 5.1-3 3.9 0 6.1 4.1 4.6 7.5C19.6 16.3 12 21 12 21z"/></svg>Support OpenFollow</span>
        % if compact:
        %# Broken by hand so the docked card fits its text rather than a wrap width.
        <p>Free, with no outside funding.<br>Help us pay for test hardware and hosting.</p>
        % else:
        <p>OpenFollow is free and has no outside funding. Contributions pay for the hardware we test on and for hosting. If OpenFollow works for your shows, help us keep it going.</p>
        % end
        <a class="support-card-url" href="{{SUPPORT.url}}" target="_blank" rel="noopener noreferrer">{{SUPPORT.url.split('//', 1)[1]}}</a>
    </div>
</aside>
