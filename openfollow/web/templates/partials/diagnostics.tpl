% # Diagnostics page. Two layers: (1) live summary cards (polled every 5s on
% # their own, see ``#diagnostics-live`` below) show live state without
% # leaving Overview tab; (2) action buttons
% # (Download bundle / Test peers) trigger one-shot endpoints.
% # Avoids JS on debug surface (network/discovery broken → htmx broken too);
% # Download button is plain <a> that works without JS; output in <pre>
% # slots survives partial degradation. Restart button is operator action
% # (confirm dialog + reload poll) through the shared ``openfollowRestart()``
% # in ``base.tpl``, where the ``#top-restart-notice`` banner also lives.
% #
% # Only the live cards poll/swap (see ``#diagnostics-live`` below + the
% # ``partials/diagnostics_cards`` fragment); the section shell, fold state,
% # and the bundle/probe tools stay put across the 5s tick.
% #
% # Provider data threaded from the route handler:
% # - ``cards``: dict of card values (configured/display port, beacon
% #   sender + receiver health, peer count)

<div class="section" id="diagnostics-section" data-fold-key="diagnostics" data-help="diagnostics">
    <div class="section-head">
        <h2>Diagnostics</h2>
    </div>

    %# Live cards refresh on their own every 5s. Keeping the poll target inside
    %# the section (rather than wrapping the whole section) means the head +
    %# tools below never get rebuilt, so the section no longer flickers. The
    %# trigger skips the poll while the section is collapsed (matches overview).
    <div id="diagnostics-live"
         hx-get="/section/diagnostics"
         hx-trigger="every 5s [!this.closest('.section').classList.contains('is-collapsed')]"
         hx-swap="innerHTML">
        % include('partials/diagnostics_cards.tpl', cards=cards)
    </div>

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
        %# Operator-loaded peer probe results. The 5s poll only swaps
        %# ``#diagnostics-live`` above, so this slot keeps whatever the
        %# operator loaded without needing ``hx-preserve``.
        <div id="diagnostics-probe-results" class="row" style="margin-top: 0.6rem;"></div>
    </div>
</div>
