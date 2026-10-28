v0.5.0
## Simple and Advanced network mode

**General → Network Interface Settings** opens with a **Network mode** switch. **Simple** runs everything over one interface, the one shown below it, as before. **Advanced** gives each function its own interface under **Network Interface Assignment**, and adds VLANs. Adapters can be labelled in both modes. A station whose config already pins a function to an interface starts in Advanced; every other station starts in Simple.

In Advanced, check the pins: USB Ethernet adapters are now named after their MAC (`enx…`), so a pin to one may have moved.

## Controller Slots are now stable on restart

Controllers now keep their slot by the USB socket they are plugged into, so one may drive a different marker than before. Check **Input → Controller Slots**.

## Controlled markers are always shown

A marker this station controls now always appears on the Operator Screen and counts in the trigger zones, even if it was hidden before.

## The Operator Screen follows the keyboard layout

The Operator Screen now uses the keyboard layout set in the station's system, where it used to assume a US keyboard. Choose another under **Input → Keyboard Input**.

## Features replace Show experimental features

The **Show experimental features** switch is now a **Features** table under **General → Station Settings → Advanced Settings**, and a change there applies on **Save**. A feature that is off is hidden and no longer runs. On any other station, check **RTTrPM output** before the next show: it starts off, so it no longer sends, even where **Send marker positions over RTTrPM** is ticked.

## Control frame replaces Invert control direction

Under **Markers & Zones → Marker Movement**, **Invert control direction** is now **Control frame**. A station with it ticked keeps its inverted controls as **Fixed heading** 180°; every other station steers in **Stage axes**, as before.

## The Setup Wizard has four steps

The **Setup Wizard** now runs Video Source, Lens, **Camera Calibration** and Review, and calculates the camera from marks pinned on the picture: **Simple** uses the four corners of a measured rectangle, **Advanced** known points and triangulated points. In Simple, **Save and finish** also saves the rectangle as the grid, with the point of origin you choose: centred between DSL and DSR, or **Custom**. Check that it matches the console's origin before saving. For a camera that looks almost straight down, the Setup Wizard asks for the exact height of the camera's lens.

## The camera is set in the Setup Wizard only

The **Camera & Lens Calibration** section of the Camera & Grid tab now shows position, orientation, field of view and lens read-only: to change them, run the **Setup Wizard** again. **Apply to all stations** copies the camera with its calibration. Ctrl/Cmd+S on the Operator Screen no longer saves the camera. Its **Calibration** column shows when the calibration was saved, from which video source, the lens rating and how closely the pinned points fit the saved camera.

## Pictures keep their shape

An image or clip in the **Media Gallery** that is not 16:9 now plays in its own shape, with black bars at the sides or at the top and bottom, where it used to be stretched to fill the screen. So does a **USB Camera** whose picture has another shape than its **Render resolution**, such as a 4:3 camera at `1080p`. A station calibrated on such a picture needs the **Setup Wizard** again.
