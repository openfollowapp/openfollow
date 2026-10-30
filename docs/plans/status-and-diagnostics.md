# Plan: one Status & Diagnostics section

Live Statistics and Diagnostics on the Overview tab show the same kind of thing: how this station is doing right now. They become one section, **Status & Diagnostics**, and it shows only what applies to the features switched on.

## Decisions

| Topic | Decision |
|---|---|
| Section | One section, **Status & Diagnostics**, in place of both. It keeps the `diagnostics` id, fold key, help drawer and `/section/diagnostics` route; the `statistics` ones go. |
| Refresh | The boxes refresh every second while in view (the discovery figures used to refresh every 5 s). The announcer stays outside the swap. |
| Video | Unchanged. |
| This Station | The Device box plus the Web server box's **Web port** and **Uptime**. The port row shows the port served on, with a caution **Fallback** chip when it is not the configured one. |
| Station network discovery | The two beacon boxes in one: Stations seen, Last sent, Last received, Send errors, Beacons sent, Beacons received. Its chip is the worse of the two: **Stopped** (both), **Sender stopped** / **Receiver stopped** (error), **Send errors** (caution), else **Running**. |
| Person Detection | Shown only while the `detection` feature is on, with its **Off** chip when the feature is on but detection is not running. With the feature off its missing-packages alert is not spoken either. |
| Layout | Bento: each box keeps its own height. This Station takes the right column; Video and Station network discovery stack beside it. With Person Detection, it stacks under Video and Station network discovery runs full width with its rows in three columns. One column on a phone. |
| Screenless stations | Output resolution and Overlay redraw rate stay as they are. |
| Tools | Bundle & tools unchanged, below the boxes. |

## Steps

- [ ] `partials/diagnostics.tpl`: the section shell (title, the live region polled every second, the announcer, Bundle & tools).
- [ ] `partials/diagnostics_cards.tpl`: the four boxes from the runtime stats, the cards and the detection feature; `partials/statistics.tpl` goes.
- [ ] `partials/diagnostics_alerts.tpl` and `GET /section/diagnostics/alerts` in place of the statistics announcer; `live_alerts.diagnostics_alerts(stats, detection_shown)`.
- [ ] `_build_diagnostics_cards`: the network chip and status; the per-beacon chips go.
- [ ] `index.tpl`: one include; `/section/statistics` and `/section/statistics/alerts` go.
- [ ] Bento CSS in `base.tpl`.
- [ ] Help: one `help/diagnostics.md`; `help/statistics.md` goes, and `docs/help-reference/` follows.
- [ ] Docs: the web, video and diagnostics guides.
- [ ] Tests: the boxes and their rows, the detection gate (box and spoken alert), the port chip, the network chip's precedence, the bento class, the routes and their poll gating, the help key.
