# Running the Indeed Collector on a Virtual Display (Xvfb)

This document explains how to start the collector on a machine you reach over
SSH, where there is no desktop to draw on.

It is written for a beginner. Read sections 1–4 and you can start the fleet;
the rest is reference for when something goes wrong.

---

## 1. Why the collector needs a display at all

`find_indeed_job.py` ships with `BROWSER_HEADLESS = False`, i.e. it drives a
**real, visible Chromium window** rather than a headless one. That is
deliberate and must not be "fixed" by switching to headless:

| Browser mode | What Indeed's edge returns |
|---|---|
| bundled headless Chromium | **403 Access Denied** |
| real Chromium, visible | **200 OK**, pages render |

A visible browser needs an X display. Over SSH there is none, so Playwright
fails with its own unmistakable error:

```
╔══════════════════════════════════════════════════════════════════════════════╗
║ Looks like you launched a headed browser without having a XServer running.   ║
║ Set either 'headless: true' or use 'xvfb-run <your-playwright-app>'.         ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

If you see that in `indeed.log`, the collector never collected anything — it
died at browser launch.

### Why not just use the desktop's display

Borrowing the logged-in desktop (`DISPLAY=:0`) appears to work, and then fails
silently later. `:0` only exists **while somebody is logged in**. A reboot, a
logout, or a locked screen at the wrong moment leaves the collector with
nowhere to draw. On **2026-10-25** every laptop in the fleet died overnight for
exactly this reason.

**Xvfb** solves it properly: a framebuffer in memory that belongs to the
collector alone. Chromium renders normally, and nobody has to be logged in.

---

## 2. Quick start — the command to use

```bash
cd /home/abhinav/indeed_script
INDEED_XVFB=1 setsid ./indeed start </dev/null
```

That is the whole thing. Expected output:

```
display : Xvfb :102 (1366x768x24, pid 5824)
Started (pid 5832). Log: /home/abhinav/indeed_script/indeed.log
```

Two parts of that command are not optional, and both are explained below:
`INDEED_XVFB=1` (section 3) and `setsid` (section 4).

---

## 3. The three display modes

`./indeed start` decides what display to use from one environment variable:

| Setting | Behaviour |
|---|---|
| `INDEED_XVFB=1` | **Always** start our own Xvfb, even if `DISPLAY` is set. |
| `INDEED_XVFB=0` | **Never** use Xvfb — inherit `DISPLAY`, and refuse to start if there is none. |
| *unset* (`auto`) | Use Xvfb only when `DISPLAY` is empty; otherwise inherit it. |

`auto` is the sensible default and is usually right. **Prefer `=1` anyway on
these laptops**, because `auto` inherits whatever `DISPLAY` your SSH session
happens to carry. If X11 forwarding is on, or you started from a desktop
terminal, `auto` quietly hands Chromium the *desktop's* display — and you are
back to the failure in section 1 without any warning. `=1` is unambiguous.

Use `=0` only when you genuinely want the browser on a screen you are watching,
e.g. debugging at the laptop's own keyboard.

To change the framebuffer size:

```bash
INDEED_XVFB=1 INDEED_XVFB_GEOMETRY=1920x1080x24 setsid ./indeed start </dev/null
```

The default is `1366x768x24`. It should match `BROWSER_WIDTH` /
`BROWSER_HEIGHT` near the top of `find_indeed_job.py`; a mismatch is harmless
but wastes memory.

---

## 4. Why `setsid` — do not drop it

The launcher starts the framebuffer as a plain background job:

```bash
Xvfb "$disp" -screen 0 "$XVFB_GEOMETRY" -nolisten tcp >>indeed_xvfb.log 2>&1 &
```

Note there is **no `nohup`** on that line. The collector itself is `nohup`'d,
but Xvfb is not. If the framebuffer ever did die while the collector kept
running, Chromium would lose its display mid-crawl — which reads as a baffling
browser crash rather than a display problem.

**How much does this actually matter?** Less than it looks, on these laptops.
By the time `./indeed start` returns, the launcher script has already exited, so
Xvfb has been reparented to `init` (PPID 1) with no controlling terminal — a
terminal hangup has nothing left to signal. And `logind` on this fleet is set
`KillUserProcesses=no`, so logging out does not sweep up stray user processes
either. A plain `./indeed start` over SSH does in practice survive the logout;
this was verified on .121 and .124.

So treat `setsid` as cheap insurance, not a rescue: it closes the narrow window
during startup and makes the outcome independent of a `logind` setting someone
could flip later. Use it, but if you find a collector that was started without
it, that alone is not a reason to restart a healthy run. `</dev/null` detaches
stdin in the same spirit.

---

## 5. Which display number you get

Display `:0` is the desktop, and the Naukri collectors already run their own
framebuffers on `:77`, `:99` and `:100`. So the launcher starts looking at
**`:101` and scans up to `:130`**, taking the first number where neither
`/tmp/.X<n>-lock` nor `/tmp/.X11-unix/X<n>` exists.

You do not choose the number, and it will differ between hosts — `:101` on a
quiet laptop, `:102` where Naukri got there first. That is expected.

If a previous framebuffer is still alive, `start` **reuses** it rather than
piling up a second one.

---

## 6. Files Xvfb adds to the project directory

| File | Contents |
|---|---|
| `indeed_xvfb.pid` | PID of the framebuffer process |
| `.indeed_xvfb_display` | The display in use, e.g. `:102` |
| `indeed_xvfb.log` | Xvfb's own stdout/stderr — read this if it dies on startup |

These are managed for you: `stop` kills the framebuffer and deletes the first
two. All three are run state rather than source, and all three are already
covered by `.gitignore` (`indeed_xvfb.pid` and `.indeed_xvfb_display`
explicitly, `indeed_xvfb.log` via the `*.log` rule) — nothing to add.

### A stopped collector can leave its framebuffer running

`xvfb_shutdown` is wired into `./indeed stop`, **not** into the collector
exiting by itself. When the collector gives up on its own — the usual case is
`STOPPING. HTTP 403 persisted through 3 cooldown(s)` — the Xvfb it was using
stays alive with nothing drawing on it. Seeing

```
collector=STOPPED   xvfb=ALIVE pid 5824   display=:102
```

is therefore normal, not a fault. It costs about **65 MB** of RAM and holds one
display number.

It does **not** accumulate: `xvfb_prepare` reuses a live framebuffer recorded in
`.indeed_xvfb_display`, so the next `start` adopts `:102` rather than opening
`:103`. The ceiling is one spare per host. To reclaim it immediately, run
`./indeed stop` on the already-stopped host — it reports `Not running.` and
cleans the display up anyway.

---

## 7. Checking that it actually worked

```bash
./indeed status
```

```
project : /home/abhinav/indeed_script
python  : /home/abhinav/indeed_script/venv/bin/python
state   : running (pid 5832)
display : Xvfb :102 (pid 5824)
excel   : 1 workbook(s) in the project root
crawl   : cycle 1 | keyword 1/85 | city 4/23 | page 1 | complete=False
```

Then confirm from the log that Chromium really launched — `status` only proves
the *display* is up, not that the browser attached to it:

```bash
grep "browser  " indeed.log | tail -1
```

```
browser            : Chromium VISIBLE on :102, 1366x768, slow_mo 350ms
```

That line is the proof. Seeing a display number there means the Xvfb path
worked end to end.

> **Reading `indeed.log` correctly.** The log is **append-only across runs** —
> a fresh start adds to the bottom of the old one. Yesterday's tracebacks and
> XServer errors are still in there and will match any naive `grep`. Always
> look at the tail, or bound your search by date, before concluding the current
> run is broken.
>
> Health greps must also skip the startup banner, which contains the literal
> phrases `a challenge, 403 or 429 STOPS that work` and `no CAPTCHA solving`.
> Counting those as events reports phantom blocks on every clean run.

---

## 8. Stopping

```bash
./indeed stop
```

This sends `SIGTERM`, waits up to 120 s for the collector to finish the job it
is on and write its checkpoint, then shuts the framebuffer down too. Output
ends with:

```
Stopped. The checkpoint in crawl_cycle_state.json is intact.
```

Restarting continues from that checkpoint — you do not lose a cycle. Never
`kill -9` the collector: it skips the checkpoint save, and the partially
crawled city is re-done from the top of the page.

---

## 9. Starting the whole fleet

The 110 crawl cities are split positionally by `rank` of `num_sys`, both set
near line 64 of `find_indeed_job.py`. **Both values must be right on every
host** — the slice is positional, so a wrong rank means two laptops silently
crawl the same cities while others are never visited.

| Host | rank | Cities |
|---|---|---|
| 192.168.1.121 | 1 | Delhi → Coimbatore (23) |
| 192.168.1.122 | 2 | Madurai → Anand (23) |
| 192.168.1.123 | 3 | Vapi → Meerut (23) |
| 192.168.1.124 | 4 | Agra → Patna (23) |
| 192.168.1.125 | 5 | Ranchi → Porvorim (18) |

Start them rank 1 → 5:

```bash
for ip in 121 122 123 124 125; do
    echo "=== .$ip ==="
    ssh abhinav@192.168.1.$ip \
      'cd /home/abhinav/indeed_script && INDEED_XVFB=1 setsid ./indeed start </dev/null'
done
```

If a host is down, **leave `num_sys` at 5 and skip it**. Dropping `num_sys` to
4 reshards every slice, so the surviving hosts' `crawl_cycle_state.json`
checkpoints no longer line up with the cities they describe. The missing rank's
cities simply wait for it to come back.

### Expect 403s when several hosts run together

The laptops share one office egress IP, so five collectors look like one very
busy client. Indeed answers with `HTTP 403`, and the script responds by
checkpointing and sleeping — `PAUSE: HTTP 403 access break 1/3`. That is the
designed, respectful backoff, **not** a bug and not something to work around.
If throughput matters more than host count, stagger the starts or run fewer
collectors rather than trying to defeat the block.

---

## 10. The systemd trap

`./indeed start` has two completely different code paths:

```
is an "indeed" systemd unit installed?
├── yes → sudo systemctl start indeed     ← xvfb_prepare is NEVER called
└── no  → start_standalone()              ← calls xvfb_prepare, uses Xvfb
```

So **installing the unit with `install_service.sh` silently disables
everything in this document.** `indeed.service` as shipped has its
`Environment=DISPLAY=:0` line commented out, giving the service no display at
all — the exact overnight failure from section 1.

No host in the fleet currently has the unit installed, which is why the Xvfb
path is the one that runs. Keep it that way unless you first teach the unit to
bring up its own framebuffer.

---

## 11. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Xvfb is not installed, so there is no display for Chromium.` | `sudo apt-get install -y xvfb` |
| `No free X display number between :101 and :130.` | Stale locks. With the collector stopped: `rm -f /tmp/.X1??-lock` |
| `Xvfb died on startup` | Read `indeed_xvfb.log`; usually a bad `INDEED_XVFB_GEOMETRY`. |
| `Xvfb on :1xx did not become ready in 10s.` | Machine heavily loaded. Retry; install `x11-utils` so readiness is probed with `xdpyinfo` instead of guessed. |
| `INDEED_XVFB=0 but DISPLAY is empty` | You asked for no Xvfb over SSH. Drop the `=0`. |
| Browser error mid-crawl, collector still alive | Check the framebuffer first: `kill -0 $(cat indeed_xvfb.pid)`. If it is gone, `./indeed restart`. Note a missing `setsid` is rarely the cause — see section 4. |
| `status` shows running but log is frozen | Likely a `403` break; check the last `PAUSE` line. Breaks last 10 minutes. |
| `no saved login ... continuing signed out` | No `indeed_auth.json`. Run `save_indeed_login.py` **on that host**. Do not copy one session file to every laptop — the same cookie from several IPs reads as abuse. |

---

## 12. One-line summary

```bash
cd /home/abhinav/indeed_script && INDEED_XVFB=1 setsid ./indeed start </dev/null
```

Verify with `./indeed status`, confirm `Chromium VISIBLE on :1xx` in
`indeed.log`, and stop with `./indeed stop` so the checkpoint survives.
