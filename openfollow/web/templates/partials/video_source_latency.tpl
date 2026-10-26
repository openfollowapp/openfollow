%# Camera Latency: the last successful run, or why a run cannot start, and the
%# button that opens the Measure dialog. The source-status poll sends its key and
%# swaps it in out of band when what it shows changed, such as video arriving.
% lat = latency
<div id="video-source-latency" class="row latency-row"
     style="display:{{'none' if lat['gallery'] else ''}}"
% if defined('oob') and oob:
     hx-swap-oob="true"
% end
     >
%# Owned by no form (no such form id), so Save never sends it.
    <input type="hidden" id="video-source-latency-key" name="latency" value="{{lat['key']}}"
           form="video-source-latency-unsaved">
    <div class="field wide">
        <label>Camera Latency</label>
% if lat['unavailable']:
        <div class="notice">
            <div>{{lat['unavailable']['text']}}</div>
            <div class="notice-sub">{{lat['unavailable']['action']}}</div>
        </div>
% elif lat['last']:
%     r = lat['last']
        <div class="latency-value">Round trip {{'%.0f' % r['median_ms']}} ms ({{'%.0f' % r['min_ms']}} to {{'%.0f' % r['max_ms']}} ms)<small>{{r['when']}}</small></div>
% else:
        <div class="latency-value">Not measured yet.</div>
% end
    </div>
    <button type="button" class="secondary" onclick="ofLatencyDialog()"
            {{'disabled' if lat['unavailable'] else ''}}>Measure latency</button>
</div>
