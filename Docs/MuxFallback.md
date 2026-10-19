# Startup mux fallback

The controller can accept the active-mux handshake even though subsequent
commands to all four auxiliary streams fail. Previously `resetDevices()`
discarded every auxiliary ACK, then published four mouse nubs regardless.

Startup now checks whether any auxiliary device acknowledges the existing F5
(Set Defaults and Disable) command. If none does, it explicitly disables mux
mode. Only an acknowledged disable changes routing to the single legacy D4
port and changes the nub count to two (keyboard plus mouse). That legacy port
is probed once before the ordinary mouse/trackpad drivers get to identify it.
There is no retry loop if legacy probing also fails.

With startup reset disabled (`ps2rst` without its boot-reset bit), the check
uses F2 (Get ID) instead, consuming the ID byte after an ACK. It does not reset
the keyboard or auxiliary devices in that case. `ps2kbdonly` sends no auxiliary probes.

If disabling mux cannot be confirmed, the controller attempts to restore mux
mode and logs the outcome. It does not silently assume legacy routing.

## Scope

- This fallback validates the controller/device command path before publishing
  IOKit nubs. It is not a fallback triggered by a driver rejecting an otherwise
  responsive device: any auxiliary ACK preserves mux mode, even if no available
  driver recognizes that device later. That avoids changing a functioning mux
  simply because a device driver is absent or unsupported.
- Routing is chosen during startup, before drivers/interrupt handlers attach.
  Wake resets preserve the existing topology and do not attempt this fallback.
- The failed-mux attempt still incurs the bounded per-port read timeouts.
- Hardware behavior, successful recovery on the Samsung and sleep/wake remain
  to be tested. A successful build or scripted test does not establish that.

## Validation

Run `python Tests/mux-startup.py` on a Mac with clang++ available. It compiles
the actual controller method bodies and startup decision block against scripted
I/O. Tests cover command routing, ACK handling, ID-byte consumption, bounded
retries, disable/restoration failure and manual boot-argument modes.

To exercise automatic fallback on the Samsung, boot a build containing this
change. Capture the controller log and IORegistry; expect
`mux handshake succeeded but no auxiliary device acknowledged; retrying legacy
mode` and `legacy auxiliary probe acknowledged; using 2 device nubs` if this
failure occurs.
