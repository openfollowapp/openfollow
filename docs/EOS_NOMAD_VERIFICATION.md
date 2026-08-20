# Verifying OpenFollow against an ETC Eos console

How to prove, on real hardware, that the bundled **ETC Eos** OSC templates put
marker positions on the axes Eos expects. Run this when the templates change,
when Eos ships a major version, or when a site reports positions landing wrong.

Unit tests pin the wire bytes (`tests/test_osc_template.py`). They cannot catch a
wrong **axis convention**: a template that sends `[x] [z] [y]` is byte-perfect and
still puts the performer's height on the depth axis. Only a console can settle
that, which is what this procedure is for.

**ETCnomad counts.** It is the same Eos software and every part of this path is
software-side. OSC needs no dongle, Augment3d is included free on Mac since
Nomad v3.0, and offline mode removes only DMX/sACN output, which this does not
touch. A green Nomad run is legitimate closure.

---

## 1. Console setup

### Network: one interface per subnet

Do this **first**. Eos flags two interfaces sharing a subnet as duplicates, and
OSC arriving on a duplicate interface can be **discarded silently** - no error, no
log line, nothing in Diagnostics, after the kernel has already delivered the
packet to Eos's socket. It is indistinguishable from a wrong template address and
will burn hours if you do not rule it out first.

Open `Setup > Device > Network` and disable every interface you are not using, so
one remains on the subnet. Duplicate interfaces do **not** all behave the same
way - one may work while another drops - so never infer from the status label.

### OSC RX (required)

`Setup > System > Show Control > OSC`:

- **OSC RX** on.
- **OSC UDP RX Port** = `8000`. The toggle alone does nothing; with the port
  field blank Eos binds no socket at all.

Eos binds the **wildcard** (`UDP *:8000`), not the selected interface, so
loopback and every local address reach the socket. The interface still matters,
but at Eos's processing layer.

### OSC TX (required for `verify`)

Same page. This is what lets the console report back, turning the check from
"watch Augment3d and judge" into a machine-readable assertion:

- **OSC TX** on.
- **OSC UDP TX Port** = `8001` (must match `--rx-port`).
- **OSC UDP TX IP Adresse** = the address of the machine running the probe.

### A channel to drive

Either works, and testing both is worth the two minutes:

| Target | Patch as | What `/eos/chan/N/xyz` does |
|---|---|---|
| **Scenic Element Movable** | `ETC Fixtures > Scenic Element > Scenic Element Movable` | Sets the object's position |
| **Moving light** | any automated fixture | Sets its focus XYZ |

A Scenic Element Movable has **no DMX footprint**; `>>Fehler: Dieses Gerat
benutzt keine DMX-Adressen` when assigning an address is expected, not a problem.
The channel number must equal the OpenFollow marker id, because the template
renders `[markerid]` straight into the address.

> The stock **Augment3d demo show** has no Scenic Element Movable patched, and
> only 4 movers of its 143 channels (Releve Spots, 301-304). Patch a spare
> channel; 401 is clear of everything the demo show uses.

---

## 2. Run it

```sh
poetry run python scripts/hw_validation/eos_console_probe.py \
    --host <EOS_IP> --channel <CHANNEL> verify
```

Both bundled templates are checked unless `--template` narrows it. The probe
drives the **deployed** `OscTransmitterManager`, so what goes on the wire is what
the app sends, not a re-implementation. Exits `0` (pass) / `1` (fail).

```
  link OK  : console answered /eos/ping

--- etc  (/eos/chan/[markerid]/xyz)
    before  X=-2.25  Y=6.5  Z=3.25
    sent    /eos/chan/401/xyz  [-5.5, 4.25, 2.75]  ,fff
    after   X=-5.5  Y=4.25  Z=2.75
    PASS    1:1 in metres  (X->X Focus  Y->Y Focus  Z->Z Focus)
```

`PASS` means each argument landed on the identically-named Eos parameter, in
metres, unconverted.

### Options worth knowing

| Flag | Default | Why |
|---|---|---|
| `--rx-port` | `8001` | Must match the console's OSC UDP TX Port |
| `--park-channel` | `1` | Selection is parked here to force a fresh report |
| `--tolerance` | `0.05` | Metres of slack on the comparison |
| `--settle` | `2.0` | Seconds to wait for replies; raise on a loaded console |

---

## 3. When it fails

| Message | Means | Fix |
|---|---|---|
| `SETUP FAIL: no /eos/out/ping reply` | Eos is not processing input from this address | OSC TX off or misaddressed, **or** a duplicate-subnet interface is eating it |
| `SETUP: channel N reports no X/Y/Z Focus parameters` | Channel cannot hold a position | Patch a Scenic Element Movable, or aim `--channel` at a mover |
| `FAIL: Y Focus = 1.25, sent -6.25` | **Real finding.** Wrong axis mapping | Fix the template; the named axes say which pair is transposed |
| `FAIL: transmitter did not send` | Never reached the wire | Fault is in OpenFollow, not Eos |

A ping control runs before anything else precisely so silence is never misread
as a template fault.

---

## 4. Manual modes

`verify` is the assertion. These are for looking at the console with your own
eyes, which is still the way to answer questions about feel rather than values.

| Command | Use it for |
|---|---|
| `test` | One packet. Fastest check that a message is accepted at all |
| `sweep` | Drives one axis at a time with the other two at zero, pausing between. Watch which way the object travels |
| `stream` | Continuous 30 Hz. Judges smoothness and whether the console stays responsive to manual input |

```sh
# one axis at a time, waits for Enter between each
... --host <EOS_IP> --channel 401 sweep

# 30 Hz soak: operate the console while this runs
... --host <EOS_IP> --channel 401 --duration 30 stream
```

---

## 5. Eos behaviour that will mislead you

- **Eos reports only changes.** Re-sending a value the channel already holds
  produces silence identical to rejection. So does re-selecting an
  already-selected channel. `verify` parks the selection and swaps to a second
  probe value set to work around this; a hand-run test must do the same.
- **Diagnostics (Tab 99) does not log inbound OSC messages.** It logs the
  UdpReceiver lifecycle and command-line activity only. Do not read an empty
  Diagnostics feed as "nothing arrived" - use the ping control or a readback.
- **Delivery is not processing.** `netstat -s -p udp` showing zero "dropped due
  to no socket", and `lsof -nP -iUDP:8000` showing Eos bound, together prove only
  that packets reached the socket. Eos can still discard them above it.
- **`/eos/cmd` is the canary.** If `/eos/cmd "Chan N#"` does nothing, the problem
  is not your template - it is the most basic command Eos accepts.

---

## 6. The templates

| Template | Address | Args |
|---|---|---|
| **ETC Eos** | `/eos/chan/[markerid]/xyz` | `[x] [y] [z]` -> `,fff` |
| **ETC Eos (User 99)** | `/eos/user/99/chan/[markerid]/xyz` | `[x] [y] [z]` -> `,fff` |

Both carry position in **decimal metres** on the same axes OpenFollow uses. The
user-scoped variant addresses Eos user 99 rather than whichever user is current,
which keeps a continuous stream off the operator's command line.

Height is the one value that needs thought: `[z]` is measured from the OpenFollow
origin and Augment3d from its own. A non-zero **Grid** *Z offset* wants a matching
Augment3d origin height, or objects float by the difference.

---

## 7. Known-good baseline

Recorded 2026-08-20 against ETCnomad on macOS with the Augment3d demo show, so a
future run has something to compare against:

- Both templates map **1:1**: argument 1 -> `X Focus`, 2 -> `Y Focus`,
  3 -> `Z Focus`, in metres, no transposition. Confirmed on a Scenic Element
  Movable and on a moving light, and observed in Augment3d.
- Corroborated by the demo show's own patch data: channel 1 sits at
  `4.953, -4.572, 7.62`, and 7.62 m is a trim height - so the third coordinate is
  height, matching OpenFollow's Z.
- A 30 Hz stream held for 25 s (616 messages) left the console tracking exactly,
  with the readback landing on the driven circle.
- Eos accepts `,fffiii` as readily as `,ffffff` where a message carries integer
  literals; it does not require floats.

Not yet judged: console responsiveness under a sustained stream, from an
operator's seat rather than the data.

---

## 8. If you change the probe

`verify` is only worth having if it can fail. After editing it, mutate a template
and confirm it is caught:

```sh
# transpose Y and Z in openfollow/osc/template.py, then
... --host <EOS_IP> --channel 401 --template etc verify   # must exit 1
```

Both a Y/Z transposition and a wrong address are caught as of the baseline above.

---

## Sources

- [Can I Output OSC in ETCnomad Offline Mode?](https://support.etcconnect.com/ETC/Consoles/Eos_Family/Software_and_Programming/Can_I_Output_OSC_in_ETCnomad_Offline_Mode%3F)
- [Eos OSC Setup](https://www.etcconnect.com/WebDocs/Controls/EosFamilyOnlineHelp/en/Content/23_Show_Control/08_OSC/Using_OSC_with_Eos/Eos_OSC_Setup.htm)
- [OSC Dictionary](https://www.etcconnect.com/WebDocs/Controls/EosFamilyOnlineHelp/en/Content/23_Show_Control/08_OSC/OSC_Dictionary.htm)
- [Controlling a Scenic Object in Eos](https://support.etcconnect.com/ETC/Consoles/Augment3d/Controlling_a_Scenic_Object_in_Eos)
- [Diagnostics [Tab 99]](https://www.etcconnect.com/WebDocs/Controls/EosFamilyOnlineHelp/en/Content/07_Setup/Diagnostics_%5BTab_99%5D.htm)
- [ETCnomad Features](https://www.etcconnect.com/ETCnomad/)
