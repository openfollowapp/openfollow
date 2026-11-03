%# A missing optional package, and the release's install article as the next step, by link and QR.
%# Params: lead.
% from openfollow.runtime.overlay_links import INSTALL_PACKAGES, link_qr_svg
<div class="notice warning qr-notice" role="status">
    <div class="qr-notice-text">
        <div>{{lead}}</div>
        <div class="notice-sub">Install it over SSH: <a href="{{INSTALL_PACKAGES.url}}" target="_blank" rel="noopener noreferrer">{{INSTALL_PACKAGES.url.split('//', 1)[1]}}</a></div>
    </div>
    {{!link_qr_svg(INSTALL_PACKAGES)}}
</div>
