# OTP Output

> OTP output is experimental. Test it with your receiver before a show.

Sends marker positions as ANSI E1.59 Object Transform Protocol (OTP) multicast, alongside any other output. Marker ID 1 appears as OTP point 1, and so on.

## OTP Network

- **OTP output** – master toggle. Off by default.
- **System Number** – OTP system, 1–200. It sets the multicast addresses. Default `1`.
- **Multicast addresses** – read-only: **Transform** carries positions (`239.159.1.1` for System Number 1), **Advertisement** carries discovery (`239.159.2.1`).
- **UDP Port** – 1–65535. Default `5568`.
- **Priority** – 0–200. Receivers prefer the source with the higher value. Default `100`.
- **Source Interface** – read-only: the interface OTP leaves from, set under **General → Network Interface Assignment**. Not shown in Simple network mode. A pinned interface that is down stops OTP until it returns.

**Save** applies live.
