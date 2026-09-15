% # Discovered-peer rows fragment – see partials/overview.tpl for why only this
% # region polls. Threaded context: ``local`` (this station's PeerInfo) + ``peers``.
%# ``station_down`` means the pinned interface has no address. The displayed
%# address keeps its last known good value (the UI is usually still reachable
%# there), so the row says the identity behind it is down rather than showing a
%# number whose meaning changed with nothing to mark it.
% station_down = get('station_down', False)
<div class="peer-item local {{'offline' if station_down else ''}}">
    <span class="peer-status">{{'○' if station_down else '●'}}</span>
    <span class="peer-name">{{local.name}} <em>(this station)</em></span>
    % if station_down:
    <span class="peer-address">station interface down</span>
    % else:
    <span class="peer-address">{{local.ip}}:{{local.web_port}}</span>
    % end
</div>

% for peer in peers:
<div class="peer-item {{'online' if peer.is_online else 'offline'}}">
    <span class="peer-status">{{'●' if peer.is_online else '○'}}</span>
    <span class="peer-name">{{peer.name}}</span>
    <span class="peer-address">{{peer.ip}}:{{peer.web_port}}</span>
</div>
% end

% if not peers:
<div class="no-peers">No other stations discovered yet. Make sure other OpenFollow instances are running on this network.</div>
% end
