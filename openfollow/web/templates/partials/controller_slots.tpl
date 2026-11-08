%# Which controller holds which slot (C1, C2, ...), from the main loop's stats
%# snapshot. The shell renders once; only the table polls, only while the
%# section is open on the visible tab, and not while a marker picker is open.
<div class="section" id="controller-slots-section" data-fold-key="controller_slots" data-help="controller_slots">
    <div class="section-head">
        <h2>Controller Slots</h2>
    </div>
    <div id="controller-slots-content"
         hx-get="/section/controller_slots"
         hx-trigger="every 1s [pollShown(this) && !this.querySelector('select:focus')], poll-shown[pollShown(this)] from:body"
         hx-swap="innerHTML">
        % include('partials/controller_slots_table.tpl', controllers=controllers)
    </div>
</div>
