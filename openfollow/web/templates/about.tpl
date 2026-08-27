% rebase('base.tpl')
%# Single About page with tabs (main-UI tab-bar mechanism). third_party_html /
%# written_offer_html are mistune output (escape=True) – safe to inject unescaped.
% from openfollow import __commit__, __version__

<style>
  .about-back-link {
    display: inline-block;
    margin: 0 0 1rem;
    color: var(--muted);
    text-decoration: none;
    font-size: 0.88rem;
    font-weight: 600;
  }
  .about-back-link:hover { color: var(--text); }
</style>

<div style="max-width:760px;margin:0 auto;">

<a class="about-back-link" href="/">{{_('&larr; Back to overview')}}</a>

<div class="tab-bar">
    <button type="button" class="tab-btn active" data-tab="about-info">{{_('About')}}</button>
    <button type="button" class="tab-btn" data-tab="about-license">{{_('License')}}</button>
    <button type="button" class="tab-btn" data-tab="about-third-party">{{_('Third-party notices')}}</button>
    <button type="button" class="tab-btn" data-tab="about-written-offer">{{_('Written offer')}}</button>
</div>

<div class="tab-content active" id="tab-about-info">
    <div class="section">
        <div class="section-head">
            <h2>{{_('About OpenFollow')}}</h2>
            <span class="section-note">{{_('Version &amp; license information')}}</span>
        </div>

        <div class="group">
            <div class="metric-row">
                <span class="metric-label">{{_('Version')}}</span>
                <span class="metric-value">v{{__version__}}{{ ' (' + __commit__ + ')' if __commit__ else '' }}</span>
            </div>
            <div class="metric-row">
                <span class="metric-label">{{_('License')}}</span>
                <span class="metric-value">AGPL-3.0-or-later</span>
            </div>
            <div class="metric-row">
                <span class="metric-label">{{_('Source code')}}</span>
                <span class="metric-value"><a href="https://github.com/openfollowapp/openfollow" target="_blank" rel="noopener noreferrer">github.com/openfollowapp/openfollow</a></span>
            </div>
        </div>

        <div class="group">
            <p style="margin:0 0 0.8rem;">
                Copyright (C) 2026 The OpenFollow Project – Paul Hermann, Michel Honold, Vinzenz Schultz
            </p>
            <p style="margin:0 0 0.8rem;color:var(--muted);line-height:1.55;">
                OpenFollow is free software: you can redistribute it and/or modify it under the terms
                of the GNU Affero General Public License as published by the Free Software Foundation,
                either version 3 of the License, or (at your option) any later version.
            </p>
            <p style="margin:0;color:var(--muted);line-height:1.55;">
                This program is distributed in the hope that it will be useful, but
                <strong>{{_('WITHOUT ANY WARRANTY')}}</strong>; without even the implied warranty of
                MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero General Public
                License for more details.
            </p>
        </div>

        <div class="group">
            <div class="group-title">{{_('License texts')}}</div>
            <p style="margin:0;color:var(--muted);line-height:1.55;">
                OpenFollow's own license is the GNU AGPL v3 – read it under the License tab. The
                third-party and operating-system components are catalogued under Third-party notices.
                On the appliance image the complete license texts are installed on the device under
                <code>/usr/share/common-licenses/</code> and
                <code>/usr/share/doc/&lt;package&gt;/copyright</code>.
            </p>
        </div>

        <div class="group">
            <div class="group-title">{{_('License &amp; source (mere aggregation)')}}</div>
            <p style="margin:0 0 0.6rem;color:var(--muted);line-height:1.55;">
                OpenFollow (control software and the integrated YOLO models) is licensed
                AGPL-3.0-or-later; its complete corresponding source is at
                <a href="https://github.com/openfollowapp/openfollow" target="_blank" rel="noopener noreferrer">github.com/openfollowapp/openfollow</a>.
            </p>
            <p style="margin:0;color:var(--muted);line-height:1.55;">
                On the appliance image the operating system (Debian GNU/Linux, predominantly GPL-2.0)
                is a separate work aggregated on the same medium &ndash; it keeps its own licenses.
                See the Written offer tab for the operating-system source.
            </p>
        </div>

        <div class="group" style="border-bottom:0;padding-bottom:0;margin-bottom:0;">
            <div class="group-title">{{_('Name &amp; logo')}}</div>
            <p style="margin:0;color:var(--muted);line-height:1.55;">
                The AGPL applies to the OpenFollow code only. The OpenFollow name, logo, and all
                OpenFollow branding are &copy; Michel Honold, Paul Hermann, Vinzenz Schultz &ndash;
                all rights reserved and are not covered by the GPL.
            </p>
        </div>
    </div>
</div>

<div class="tab-content" id="tab-about-license">
    <div class="section">
        <div class="section-head">
            <h2>{{_('GNU Affero General Public License v3')}}</h2>
        </div>
        % if license_text:
        <div class="help-drawer-body"><pre class="license-text">{{license_text}}</pre></div>
        <div class="group" style="border-bottom:0;padding:0.6rem 0 0;margin:0;">
            <a href="/about/license.txt" target="_blank" rel="noopener noreferrer">{{_('Open as plain text &#8599;')}}</a>
        </div>
        % else:
        <div class="group">
            <p style="margin:0;color:var(--muted);line-height:1.55;">
                The full license text isn't bundled with this build. Read it at
                <a href="https://www.gnu.org/licenses/agpl-3.0.txt" target="_blank" rel="noopener noreferrer">gnu.org/licenses/agpl-3.0.txt</a>.
            </p>
        </div>
        % end
    </div>
</div>

<div class="tab-content" id="tab-about-third-party">
    <div class="section">
        <div class="section-head">
            <h2>{{_('Third-party notices')}}</h2>
        </div>
        <div class="group">
            <p style="margin:0;color:var(--muted);line-height:1.55;">
                A Software Bill of Materials (SPDX) is included with the distributed image.
            </p>
        </div>
        % if third_party_html:
        <div class="help-drawer-body">{{! third_party_html }}</div>
        % else:
        <div class="group">
            <p style="margin:0;color:var(--muted);line-height:1.55;">
                This document isn't bundled with this build. View it at
                <a href="https://openfollow.app" target="_blank" rel="noopener noreferrer">openfollow.app</a>.
            </p>
        </div>
        % end
    </div>
</div>

<div class="tab-content" id="tab-about-written-offer">
    <div class="section">
        <div class="section-head">
            <h2>{{_('Written offer for source code')}}</h2>
        </div>
        % if written_offer_html:
        <div class="help-drawer-body">{{! written_offer_html }}</div>
        % else:
        <div class="group">
            <p style="margin:0;color:var(--muted);line-height:1.55;">
                This document isn't bundled with this build. View it at
                <a href="https://openfollow.app" target="_blank" rel="noopener noreferrer">openfollow.app</a>.
            </p>
        </div>
        % end
    </div>
</div>

</div>
