<div class="section" id="overview-section" data-fold-key="overview" data-help="overview">
    <div class="section-head">
        <h2>Server Network</h2>
        <span class="section-note">Detected OpenFollow web peers</span>
    </div>
    %# Only the peer rows poll/swap (innerHTML) – the section shell stays put so
    %# the fold state + Refresh action don't flicker every 5s. The trigger gate
    %# reads ``is-collapsed`` off the enclosing section (not ``this``) so a
    %# collapsed section still skips the poll.
    <div class="peers-list" id="overview-peers"
         hx-get="/section/overview"
         hx-trigger="every 5s [!this.closest('.section').classList.contains('is-collapsed')]"
         hx-swap="innerHTML">
        % include('partials/overview_peers.tpl', local=local, peers=peers)
    </div>
    <div class="actions" style="margin-top: 12px;">
        <button type="button" onclick="htmx.ajax('GET','/section/overview',{target:'#overview-peers',swap:'innerHTML'})" class="secondary">Refresh</button>
    </div>

    %# Overview is strictly read-only peer discovery.
</div>
