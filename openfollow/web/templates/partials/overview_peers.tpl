% # Discovered-peer rows fragment – see partials/overview.tpl for why only this
% # region polls. Threaded context: ``local`` (this station's PeerInfo) + ``peers``.
<div class="peer-item local">
    <span class="peer-status">●</span>
    <span class="peer-name">{{local.name}} <em>(this station)</em></span>
    <span class="peer-address">{{local.ip}}:{{local.web_port}}</span>
</div>

% for peer in peers:
% # Every other station opens in a new window, offline ones at their last known address.
% host = '[' + peer.ip + ']' if ':' in peer.ip else peer.ip
<a class="peer-item {{'online' if peer.is_online else 'offline'}}" href="http://{{host}}:{{peer.web_port}}/" target="_blank" rel="noopener noreferrer">
    <span class="peer-status">{{'●' if peer.is_online else '○'}}</span>
    <span class="peer-name">{{peer.name}}<span class="visually-hidden"> (opens in a new window)</span></span>
    <span class="peer-address">{{peer.ip}}:{{peer.web_port}}</span>
</a>
% end

% if not peers:
<div class="no-peers">No other stations discovered yet. Make sure other OpenFollow instances are running on this network.</div>
% end
