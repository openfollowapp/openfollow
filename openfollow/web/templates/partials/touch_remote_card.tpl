<button type="button" id="remote-launcher" class="remote-launcher" aria-label="Open the touch remote" aria-expanded="false" aria-controls="remote-card">
    <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle><circle cx="12" cy="12" r="3"></circle><path d="M12 1v4M12 19v4M1 12h4M19 12h4"></path></svg>
</button>

<section id="remote-card" class="remote-card" aria-label="Touch remote" hidden>
    <div class="notice error remote-warning">Don't have this open during a show – for setup only</div>
    <div class="notice warning" id="remote-detection-off" hidden>Person detection is switched off: the remote has the marker.</div>
    <div class="remote-chips" id="remote-chips" role="group" aria-label="Marker"></div>
    <div class="remote-status">
        <span class="stat-chip warn" id="remote-held" hidden></span>
        <span class="stat-chip off" id="remote-lost" hidden>Taken by another remote</span>
        <span class="stat-chip off" id="remote-offline" hidden>Station not answering</span>
        <span class="stat-chip info" id="remote-on-path" hidden>On path</span>
        <span class="remote-spacer"></span>
        <button type="button" class="remote-round" id="remote-flip" aria-pressed="false" aria-label="Facing FOH">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12a8 8 0 0 1 14-5.3M20 12a8 8 0 0 1-14 5.3"></path><path d="M18 3v4h-4M6 21v-4h4"></path></svg>
        </button>
        <button type="button" class="remote-hand" id="remote-hand">Right hand</button>
        <button type="button" class="remote-round" id="remote-close" aria-label="Close the touch remote">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"></path></svg>
        </button>
    </div>
    <div class="remote-drive">
        <div class="remote-stick" id="remote-stick" aria-label="Move stick">
            <span class="remote-stick-label remote-stick-up" id="remote-up">UPSTAGE</span>
            <span class="remote-stick-label remote-stick-down" id="remote-down">DOWNSTAGE</span>
            <div class="remote-knob" id="remote-knob"></div>
        </div>
        <div class="remote-z" id="remote-z">
            <span class="remote-z-cap">Z</span>
            <div class="remote-drum" id="remote-drum" aria-label="Z wheel"><div class="remote-drum-ticks" id="remote-ticks"></div><div class="remote-drum-mid"></div></div>
            <span class="remote-z-cap" id="remote-notch"></span>
        </div>
    </div>
    <div class="remote-actions">
        <button type="button" class="btn-secondary remote-act" id="remote-reset">Reset</button>
        <button type="button" class="btn-neutral remote-act" id="remote-pathfinder" hidden>Pathfinder</button>
        <button type="button" class="btn-neutral remote-act" id="remote-xyz" aria-pressed="false" aria-controls="remote-xyz-row">X Y Z</button>
    </div>
    <div class="remote-xyz-row" id="remote-xyz-row" hidden>
        <label for="remote-x">X<input id="remote-x" type="text" inputmode="decimal" autocomplete="off"></label>
        <label for="remote-y">Y<input id="remote-y" type="text" inputmode="decimal" autocomplete="off"></label>
        <label for="remote-z-in">Z<input id="remote-z-in" type="text" inputmode="decimal" autocomplete="off"></label>
        <button type="button" class="btn-primary remote-go" id="remote-go">Glide</button>
    </div>
    <div class="remote-readout" id="remote-readout"></div>
</section>
