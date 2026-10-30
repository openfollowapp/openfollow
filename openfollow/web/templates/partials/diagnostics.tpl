% # Status & Diagnostics. Two layers: (1) the live boxes (polled every second on
% # their own, see ``#diagnostics-live`` below) show live state without
% # leaving the Overview tab; (2) action buttons
% # (Download bundle / Test peers) trigger one-shot endpoints.
% # Avoids JS on debug surface (network/discovery broken → htmx broken too);
% # Download button is plain <a> that works without JS; output in <pre>
% # slots survives partial degradation. Restart button is operator action
% # (confirm dialog + reload poll) through the shared ``openfollowRestart()``
% # in ``base.tpl``.
% #
% # Only the live boxes poll/swap (see ``#diagnostics-live`` below + the
% # ``partials/diagnostics_cards`` fragment); the section shell, fold state,
% # and the bundle/probe tools stay put across the 1 s tick.
% #
% # Threaded from the route handler: ``stats`` (the runtime snapshot),
% # ``cards`` (configured/display port, uptime, beacon health, peer count),
% # and ``local`` / ``peers`` / ``station_down`` for the Station Network box.
% detection_shown = config.ui.feature_on("detection")

<div class="section" id="diagnostics-section" data-fold-key="diagnostics" data-help="diagnostics">
    <div class="section-head">
        <h2>Status &amp; Diagnostics</h2>
    </div>

    %# Read-only peer discovery. Only its rows poll, every 5 s, apart from the boxes'
    %# 1 s swap: each swap rebuilds the station links and drops keyboard focus.
    <section class="stat-panel stations-box">
        <div class="stat-panel-head">
            <h3 class="stat-panel-title">Station Network</h3>
        </div>
        <div class="peers-list" id="overview-peers"
             hx-get="/section/overview"
             hx-trigger="every 5s [pollShown(this)], poll-shown[pollShown(this)] from:body"
             hx-swap="innerHTML">
            % include('partials/overview_peers.tpl', local=local, peers=peers, station_down=station_down)
        </div>
    </section>

    %# The poll target sits inside the section, so the head and the tools
    %# below are never rebuilt and the section does not flicker.
    <div id="diagnostics-live"
         hx-get="/section/diagnostics"
         hx-trigger="every 1s [pollShown(this)], poll-shown[pollShown(this)] from:body"
         hx-swap="innerHTML">
        % include('partials/diagnostics_cards.tpl', stats=stats, cards=cards, detection_shown=detection_shown)
    </div>
    % include('partials/diagnostics_alerts.tpl', stats=stats, detection_shown=detection_shown)

    <div class="group" style="margin-top: 1.5rem;">
        <h3 class="group-title">Bundle &amp; tools</h3>
        <div class="row">
            <a class="save-btn btn-link" href="/api/diagnostics/bundle" download>
                Download diagnostics bundle
            </a>
            %# Its dialog's steps live in ``base.tpl``, like Restart's confirm below.
            <button type="button" class="secondary" onclick="openfollowSaveToDrive()">
                Save to USB storage device
            </button>
            <button type="button" class="secondary"
                    hx-post="/api/diagnostics/test-peers"
                    hx-target="#diagnostics-probe-results"
                    hx-swap="innerHTML">
                Test peer connectivity
            </button>
            %# Restart lives here (not the top chrome) – it's rare-use
            %# and shouldn't take a permanent action slot.
            <button type="button" class="danger"
                    onclick="openfollowRestart({title: 'Restart application?', message: 'Video and tracking stop until the station is back.'})">
                Restart application
            </button>
        </div>
        %# Operator-loaded peer probe results. The 1 s poll only swaps
        %# ``#diagnostics-live`` above, so this slot keeps whatever the
        %# operator loaded without needing ``hx-preserve``.
        <div id="diagnostics-probe-results" class="row" style="margin-top: 0.6rem;"></div>
    </div>
</div>
