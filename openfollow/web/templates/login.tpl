% rebase('base.tpl', host_refusal_note="You can't log in or save here.")

<div class="section" style="max-width:400px;margin:0 auto 1rem;">
    <div class="section-head">
        <h2>Login</h2>
        <span class="section-note">Enter PIN to access configuration</span>
    </div>
    % if error:
    <div class="notice error" role="alert">{{error}}</div>
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
