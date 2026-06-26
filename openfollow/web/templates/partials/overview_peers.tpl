% # Discovered-peer rows fragment – see partials/overview.tpl for why only this
% # region polls. Threaded context: ``local`` (this server's PeerInfo) + ``peers``.
<div class="peer-item local">
    <span class="peer-status">●</span>
    <span class="peer-name">{{local.name}} <em>({{_('this server')}})</em></span>
    <span class="peer-address">{{local.ip}}:{{local.web_port}}</span>
</div>

% for peer in peers:
<div class="peer-item {{'online' if peer.is_online else 'offline'}}">
    <span class="peer-status">{{'●' if peer.is_online else '○'}}</span>
    <span class="peer-name">{{peer.name}}</span>
    <span class="peer-address">{{peer.ip}}:{{peer.web_port}}</span>
</div>
% end

% if not peers:
<div class="no-peers">{{_('No other servers discovered yet. Make sure other OpenFollow instances are running on this network.')}}</div>
% end
