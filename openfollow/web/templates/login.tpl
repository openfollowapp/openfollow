% rebase('base.tpl')

<div class="section" style="max-width:400px;margin:0 auto 1rem;">
    <div class="section-head">
        <h2>Login</h2>
        <span class="section-note">Enter PIN to access configuration</span>
    </div>
    % if error:
    <div style="margin-bottom:0.72rem;padding:8px 12px;border-radius:0.8rem;border:1px solid rgba(255,140,140,0.35);background:rgba(255,140,140,0.13);color:#ffd7d7;font-weight:600;font-size:0.88rem;">Incorrect PIN</div>
    % end
    <form method="POST" action="/login">
        <div class="row">
            <div class="field wide">
                <label for="pin">PIN</label>
                <input type="password" id="pin" name="pin" placeholder="Enter PIN" autofocus>
            </div>
        </div>
        <div class="actions" style="margin-top:0.72rem;">
            <button type="submit" class="save-btn btn-block">Unlock</button>
        </div>
    </form>
</div>

<div class="section" id="statistics-section" data-fold-key="statistics" data-fold-default="expanded">
    <div class="section-head">
        <h2>Live Statistics</h2>
        <span class="section-note">Core status first, advanced diagnostics on demand</span>
    </div>
    <div id="statistics-section-content"
         hx-get="/section/statistics"
         hx-trigger="every 1s"
         hx-swap="innerHTML">
        % include('partials/statistics.tpl', stats=stats)
    </div>
</div>
