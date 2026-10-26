# Running the collector on the other laptops (Xvfb)

Added: 26 October 2026

---

## 1. The problem this solves

The collector drives a **headed** Chromium, which needs an X display. On the
machine it was built on that is the desktop's own display, so there is a
window to watch. The other laptops in the fleet usually have no desktop
session available to the run — it is started over SSH, or by systemd at
boot — and a headed browser then has nowhere to draw. Chromium's own error
for that is a bare *"Missing X server"*.

**Xvfb** ("X virtual framebuffer") is an X server that draws into memory
instead of a screen. Chromium launches exactly as it does on a desktop and
behaves identically; the only difference is that nobody is watching.

The collector now starts one **by itself** when it needs to. There is no
wrapper command to remember and no `xvfb-run` prefix: `python3
find_indeed_job.py` is still the whole command, on every machine.

> This is not an attempt to hide anything. Same browser build, same user
> agent, same behaviour as on the desktop — Xvfb only provides a place to
> draw. If you would rather not run a browser at all, `BROWSER_HEADLESS =
> True` is one setting away and needs no X server.

---

## 2. The settings (top of `find_indeed_job.py`, as always)

```python
XVFB_MODE = "auto"          # "auto" | "always" | "never"
XVFB_DISPLAY = ""           # "" = pick the first free display (:99 upward)
XVFB_COLOUR_DEPTH = 24      # the screen is BROWSER_WIDTH x BROWSER_HEIGHT
```

| `XVFB_MODE` | Behaviour |
|-------------|-----------|
| `"auto"` *(shipped)* | Use the real display when there is one; start Xvfb when there is not. The same configuration therefore works on the desktop machine **and** on every laptop, unchanged. |
| `"always"` | Always use Xvfb, even on a desktop. Useful on this machine to reproduce exactly what the fleet does. |
| `"never"` | Never start one. No display then becomes a hard error naming both ways out. |

The virtual screen is `BROWSER_WIDTH x BROWSER_HEIGHT x XVFB_COLOUR_DEPTH`,
so the browser window and the framebuffer cannot disagree — a test asserts
that.

### How the display number is chosen

With `XVFB_DISPLAY = ""` the collector takes the first free number from
`:99` upward, checking `/tmp/.X11-unix/X<n>`. That matters if you ever run
**two ranks on one laptop**: they will not collide. Set `XVFB_DISPLAY =
":105"` to pin one.

---

## 3. Installing on a laptop

```bash
# 1. the virtual display
sudo apt-get update && sudo apt-get install -y xvfb

# 2. the project (copy it over however you prefer; git, rsync, scp)
rsync -a --exclude venv --exclude '*.xlsx' --exclude '*.json' \
      ubuntu@<this-machine>:/home/ubuntu/check_indeed/ /home/ubuntu/check_indeed/
cd /home/ubuntu/check_indeed

# 3. its own virtual environment
python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install -r requirements.txt
python3 -m playwright install chromium        # ~170MB, needed once

# 4. check the display story before running anything
./indeed status | head -4
```

That last command prints one of:

```
display : :0 (this session)                        <- a desktop; a window will show
display : none -- a virtual one (Xvfb) will be started   <- a fleet laptop; correct
display : NONE and Xvfb is NOT installed           <- step 1 was skipped
display : not needed (BROWSER_HEADLESS = True)     <- no browser at all
```

### Two things that must be set per laptop

**1. The rank.** Each machine crawls its own slice of the 110 cities. Edit
the top of `find_indeed_job.py` on each one:

```python
rank = 1        # laptop 1 -> cities 1-23
rank = 2        # laptop 2 -> cities 24-46
rank = 3        # laptop 3 -> cities 47-69
rank = 4        # laptop 4 -> cities 70-92
rank = 5        # laptop 5 -> cities 93-110
num_sys = 5     # the same on all five
```

Two laptops with the same rank crawl the same cities and waste half the
fleet. `./indeed status` and the startup banner both print the rank and the
first/last city, so check them after the copy.

**2. The saved login (`indeed_auth.json`), if you want it.** It is optional —
the collector runs signed out and says so — but a signed-in session exposes
the "Apply" destination on more jobs, which is what `apply_url` needs.

You **cannot** create it on a laptop through Xvfb: `save_indeed_login.py`
opens a window for *you* to type the email, password and OTP into, and
there is no screen to see it on. So:

```bash
# on a machine WITH a screen (this one):
python3 save_indeed_login.py          # sign in by hand, press Enter

# then copy the file to each laptop and lock it down:
scp indeed_auth.json ubuntu@<laptop>:/home/ubuntu/check_indeed/
ssh ubuntu@<laptop> 'chmod 600 /home/ubuntu/check_indeed/indeed_auth.json'
```

It holds a live session — treat it exactly like a password. It is in
`.gitignore` and `reset.sh` never deletes it.

---

## 4. Running it

Interactively over SSH:

```bash
cd /home/ubuntu/check_indeed
source venv/bin/activate
python3 find_indeed_job.py
```

You should see, within a few seconds:

```
   [DISPLAY] virtual display :99 started (1366x768x24) -- the browser runs
             normally, there is just no screen to watch it on
   [BROWSER] Chromium VISIBLE (1366x768) -- ... LIVE pages at https://in.indeed.com
```

As a service, so it survives a logout and starts at boot:

```bash
./install_service.sh          # warns if Xvfb is missing
./indeed start
./indeed status
./indeed log                  # follow it
./indeed stop                 # graceful: finishes the job in hand
```

The unit needs **no** `DISPLAY` configuration — the collector handles the
display itself. Ctrl+C on an interactive run stops immediately, saves the
checkpoint, kills the browser and shuts the virtual display down.

---

## 5. Verifying it actually works

Proven on this machine by stripping the desktop session out of the
environment, which is exactly what a fleet laptop looks like:

```
$ env -u DISPLAY -u WAYLAND_DISPLAY python3 find_indeed_job.py

  browser            : Chromium VISIBLE on <no DISPLAY set>, 1366x768, slow_mo 350ms
   [DISPLAY] virtual display :107 started (1366x768x24)
   [BROWSER] Chromium VISIBLE (1366x768) -> LIVE pages at https://in.indeed.com
     [BROWSER] opening https://in.indeed.com/jobs?q=Cloud+Support+Engineer&l=Chandigarh&start=0&sort=date
     [BROWSER] declined non-essential cookies
     page 1: 16 listing(s) (16 new)
```

Checks made on the virtual display:

* the browser is genuinely **headed** — `navigator.userAgent` contains no
  `Headless` marker;
* the page sees the configured screen: `screen.width/height` = 1366x768;
* listing cards and job pages are read normally;
* stopping removes the socket (`/tmp/.X11-unix/X107`) and restores `DISPLAY`;
* a failed launch releases the display too, so repeated restarts cannot
  leave a pile of orphaned Xvfb servers behind.

A quick self-check you can run on any laptop:

```bash
cd /home/ubuntu/check_indeed && source venv/bin/activate
python3 -m unittest tests.test_visible_browser.TestHeadedBrowserWithoutADesktop -v
```

Six tests; they strip `DISPLAY` and drive a real browser on a virtual
display. They pass on a desktop and on a headless laptop alike.

---

## 6. If something goes wrong

| Symptom | Cause | Fix |
|---------|-------|-----|
| `a virtual display was requested but Xvfb is not installed` | step 1 skipped | `sudo apt-get install -y xvfb` |
| `BROWSER_HEADLESS is False … and XVFB_MODE is "never"` | the mode was changed | set `XVFB_MODE = "auto"`, or `BROWSER_HEADLESS = True` |
| `Chromium could not be launched … run python -m playwright install chromium` | the browser binary was never downloaded | `python3 -m playwright install chromium` |
| `Xvfb exited immediately` | a stale socket, or no write access to `/tmp/.X11-unix` | check `ls -la /tmp/.X11-unix`; remove a stale `X99` only if no X server owns it |
| Two laptops collecting the same cities | the same `rank` on both | give each laptop its own `rank` |
| `the browser is gone (crashed or was killed) -- relaunching it` | Chromium died, usually memory pressure | nothing: the collector relaunches it and carries on. If frequent, lower `BROWSER_WIDTH`/`HEIGHT`, or set `BROWSER_HEADLESS = True` (headless uses noticeably less memory) |

### Memory

A headed Chromium on Xvfb needs roughly 400–700MB. On a laptop with 4GB
that is fine for one rank; it is **not** fine for three ranks at once. If a
machine is tight, `BROWSER_HEADLESS = True` drops both the Xvfb process and
the rendering overhead, and the collector behaves the same otherwise.

---

## 7. What changed in the code

| File | Change |
|------|--------|
| `source_file/indeed_client.py` | new `VirtualDisplay` class (start/stop Xvfb, pick a free display, restore `DISPLAY`); `BrowserTransport._ensure_display()` decides real vs virtual vs error; the display is released by `stop()`, by `kill()`, and by a failed launch |
| `find_indeed_job.py` | `XVFB_MODE`, `XVFB_DISPLAY`, `XVFB_COLOUR_DEPTH` in the configuration block, passed through `build_client_config()` |
| `indeed` | `status` now reports which display a headed browser would use |
| `install_service.sh` | warns when `BROWSER_HEADLESS = False` and Xvfb is missing |
| `indeed.service` | documents that no `DISPLAY` setting is needed, with the desktop-display option still available commented out |
| `tests/test_visible_browser.py` | 19 new tests: `TestVirtualDisplay`, `TestVirtualDisplayConfiguration`, `TestHeadedBrowserWithoutADesktop`, and a rewritten `TestDisplayGuard` |

### Two bugs found while building this

**A kill left the process unable to open another browser.** Killing
Playwright's driver leaves its thread-local event loop behind, so the next
`sync_playwright().start()` failed with *"you are using Playwright Sync API
inside the asyncio loop"*. `kill()` now calls `playwright.stop()` after
killing the driver; measured before and after — the relaunch failed, then
worked. This mattered beyond the tests: a Ctrl+C path that could not reopen
a browser would have broken the self-healing relaunch.

**A test leaked a started transport**, which broke four later test classes in
the same process with that same error. That is very likely the intermittent
suite error recorded as unresolved in `docs/PERFORMANCE_FIXES.md` §9: the
full browser suite now passes 97/97 repeatedly where it previously failed
about one run in three.
