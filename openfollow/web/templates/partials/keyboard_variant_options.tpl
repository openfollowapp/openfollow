%# The Variant picker's options, also served alone when the layout changes.
% for value, label in variants:
<option value="{{value}}" {{'selected' if value == selected else ''}}>{{label}}</option>
% end
