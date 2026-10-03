%# The Network Interface Assignment poll: every Address cell, out of band.
% for row in assignment_rows:
% include('partials/interface_assignment_address.tpl', row=row, oob=True)
% end
<span id="ia-options-fp" data-fp="{{options_fingerprint}}" hidden hx-swap-oob="true"></span>
