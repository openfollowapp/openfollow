v0.5.0
## Simple and Advanced network mode

**General → Network Interface Settings** opens with a **Network mode** switch. **Simple** runs everything over one interface, the one shown below it, as before. **Advanced** gives each function its own interface under **Network Interface Assignment**, with adapter labels and VLANs. A station whose config already pins a function to an interface starts in Advanced; every other station starts in Simple.

In Advanced, check the pins: USB Ethernet adapters are now named after their MAC (`enx…`), so a pin to one may have moved.

## Controller Slots are now stable on restart

Controllers now keep their slot by the USB socket they are plugged into, so one may drive a different marker than before. Check **Input → Controller Slots**.

## Controlled markers are always shown

A marker this station controls now always appears on the Operator Screen and counts in the trigger zones, even if it was hidden before.
