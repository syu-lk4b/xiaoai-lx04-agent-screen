# LX04 Status Display

[中文](README.md) | English

Turn an idle Xiaomi Xiao AI touchscreen speaker (LX04) into a desk status display for AI coding agents.

![On the desk](docs/images/hero.jpg)

What Codex and Claude are doing, how much quota is left, which task is stuck waiting for you —
one glance tells you, no need to switch back to the terminal and dig through windows.

## What this fork adds

This project builds on [cenzihao1987-svg/lx04-status-display](https://github.com/cenzihao1987-svg/lx04-status-display) (the Codex / Claude status pages) and adds three main features:

- **Mac controls**: a page of 12 icon buttons — tap to put the Mac's display to sleep, start the screen saver, keep it awake, or open everyday apps (Music, terminals, Claude, VS Code, Chrome, WeChat, Obsidian, and more). Only whitelisted, harmless actions run — never arbitrary commands; button icons come straight from the apps themselves
- **Mac performance monitoring**: live CPU, GPU, memory, and network throughput with charts of the last 4 minutes. Everything is collected through system interfaces that need no extra permissions, and nothing is sampled while nobody is looking
- **Photos**: shows a random slideshow of photos with people in them from the past two years of your macOS Photos library — no more copying photos onto the device. Read-only; the library is never modified

| Mac performance | Mac controls | Photos |
| --- | --- | --- |
| ![Mac Status page](docs/images/page-mac-status.jpg) | ![Mac Controls page](docs/images/page-mac-control.jpg) | ![Photos page](docs/images/page-photos.jpg) |

Plus some supporting improvements:

- **Photo-frame mode**: once both agents have been idle for 10 minutes, it parks on the Photos page; as soon as a task starts, it jumps back to that agent's page
- **Night screen-off**: the screen turns off 23:00–07:00 by default and wakes back to the status page in the morning
- **LAN access control**: only the Mac itself and the speaker can connect; every other device on the LAN is refused (they're family photos)
- **One-command install**: `./scripts/setup.sh`; README in Chinese and English
- Fixed Claude quota reading: prefers the claude-hud statusline plugin's cache

---

## What it looks like

Swipe left and right between five pages: Codex, Claude, Mac Status, Mac Controls, and Photos. Left alone, it rotates on its own — the Photos page stays 5 minutes, every other page 1 minute (change with `?photo=N` / `?rotate=N`, in seconds). When a task is waiting for your approval, it stays on that page. Once neither Codex nor Claude has been busy for 10 minutes, it parks on the Photos page as a photo frame (`?idle=N` to change, `?idle=0` to turn off); as soon as a task starts running, it jumps back to that agent's page and resumes rotating.

**The Photos page** reads the macOS Photos library directly: a random pick of photos with people in them from the past two years (using Photos' own face detection, skipping pets, strangers in the background and photos of ID documents), changing every 12 seconds, with portrait photos shown in pairs and crops centered on faces. No more copying photos onto the speaker. Read-only — the library is never modified; photos that live only in iCloud and aren't downloaded to this Mac are skipped. Tune with `LX04_PHOTO_YEARS` (default 2) and `LX04_PHOTO_COUNT` (default 300).

**Screen off at night**: 23:00–07:00 by default; in the morning the screen wakes and returns to the status page. To change the window, add `"sleep": "23:30-07:00"` to `~/Library/Application Support/lx04-status/adb-endpoint.json`, or `"off"` to disable.

| Codex | Claude |
| --- | --- |
| ![Codex page](docs/images/page-codex.jpg) | ![Claude page](docs/images/page-claude.jpg) |

Each agent page has three blocks, top to bottom:

- **Header** — whose page it is, plus the current time (the colon blinks every second, so you can tell the screen isn't frozen)
- **Quota** — usage in both windows and the next reset time. Codex shows what's *left*, Claude shows what's *used*
- **Last three sessions** — project name, state, and total time. Just-finished sessions get a green tint for 10 minutes

The background is a looping video, not a still image.

---

## What you need

**Hardware**

- A Xiaomi Xiao AI touchscreen speaker LX04 (cheap second-hand). **It must be flashed first** — the stock system has no usable browser. Flashing steps are in [docs/刷机手册.md](docs/刷机手册.md) (Chinese). There is a risk of bricking it; decide for yourself
- A Mac on the same Wi-Fi as the speaker

**Software**

- Codex CLI or Claude Code (at least one; both is best)
- [uv](https://docs.astral.sh/uv/) (`brew install uv`) — it fetches the Python 3.12 that's needed
- Android Platform Tools on the Mac (`brew install --cask android-platform-tools`), with wireless debugging of the LX04 allowed from the Mac
- A browser on the speaker that can go full screen. [Via](https://viayoo.com/) is recommended (tiny, can hide the address bar)

> The status display only reads local files. It connects to no cloud service and needs no Xiaomi account.
> (Unless you turn on voice announcements — see [below](#optional-voice-announcements).)

---

## Getting it running

### One-command install

```bash
git clone https://github.com/syu-lk4b/xiaoai-lx04-agent-screen.git
cd xiaoai-lx04-agent-screen
./scripts/setup.sh --device 192.168.x.x   # the speaker's LAN IP
```

`setup.sh` does the following in order, ticking off each step; when something fails it says why and how to fix it:

1. Checks for `git`, `uv`, `adb`, and the system Python
2. Clones [yihong0618/mibe](https://github.com/yihong0618/mibe) (it follows Codex's session logs) into `vendor/`, pinned to the commit the patch was made against, then applies `vendor/mibe.patch`
3. Installs Python dependencies and runs the tests
4. Connects to the speaker over wireless ADB, confirms it's an LX04, remembers its address, and checks that Via is installed
5. Installs two login LaunchAgents, waits for the status service to come up, and opens the status page on the speaker

It's safe to run again. If you don't know the speaker's IP, leave out `--device` and the background bridge scans the current subnet on its own. To just try it on the Mac first, add `--no-agent` and then run `./scripts/start.sh` yourself. `./scripts/setup.sh --help` lists every option.

Afterwards, open `http://127.0.0.1:8477/` in a browser on the Mac to check the page renders. Via on the speaker opens the fixed address `http://127.0.0.1:8477/` — that `127.0.0.1` is the speaker itself, and a proxy forwards it to the Mac, so you never have to update the speaker when the Mac's DHCP address changes. When the connection drops, only the proxy is repaired; the page retries on its own, without reloading or opening new tabs.

By default the proxy uses the Mac's Wi-Fi IPv4. If your Wi-Fi isn't `en0`, set `mac_ip` as described in the [usage guide](docs/使用说明.md#指定-mac-的局域网地址可选) (Chinese).

### Manual install

If you'd rather go step by step, `setup.sh` is equivalent to:

```bash
git clone https://github.com/yihong0618/mibe.git vendor/mibe
cd vendor/mibe && git checkout 2c73600 && git apply ../mibe.patch && uv sync && cd ../..
./scripts/start.sh            # run in the foreground; check the page in a browser on the Mac first
./scripts/install-agent.sh    # install the login agents and open the page on the speaker
```

The patch adapts mibe for status events, running without a speaker login, announcement control, and low-overhead polling. Details are under "我们改了 mibe 什么" in [docs/使用说明.md](docs/使用说明.md) (Chinese).

---

## Keeping it running

`setup.sh` already installs the login agents (reinstall them alone with `./scripts/install-agent.sh`). From then on it starts when you log in to macOS, restarts if a process dies, and survives reboots.

| Task | Command |
| --- | --- |
| Status service log | `tail -f ~/Library/Logs/lx04-status.log` |
| ADB bridge log | `tail -f ~/Library/Logs/lx04-adb-bridge.log` |
| Restart after changing the status code | `launchctl kickstart -k gui/$(id -u)/com.gerry.lx04-status` |
| Restart the ADB bridge | `launchctl kickstart -k gui/$(id -u)/com.gerry.lx04-adb-bridge` |

The installer creates two LaunchAgents, one for the status service and one for the ADB bridge. To uninstall, unload both `com.gerry.lx04-status` and `com.gerry.lx04-adb-bridge`.

---

## Reading the states

| Shown | Meaning |
| --- | --- |
| **Running** | Working |
| **Your turn** | Stopped and waiting for you — asking a question or requesting approval. **This one pulses orange; it's the one to watch** |
| Done | Finished. Green tint for the first 10 minutes |
| Stopped | Interrupted by you, or no activity for more than 15 minutes |

Quota numbers are normally white, turn orange when running low, and red when nearly gone. **It stays quiet until it matters, so you'll notice when it doesn't.**

When the Mac can't be reached, the whole screen turns grey and the clock's colon stops blinking — so you can tell "no tasks" from "disconnected" at a glance.

---

## Using your own background

By default it uses `meili-lite.mp4` (snow mountain) and `canyon-lite.mp4` (canyon) in `display/`: 800×480, 12fps, H.264 Baseline, no B-frames, at the original playback speed to keep decoding cheap. The original `meili.mp4` and `canyon.mp4` are kept for rollback.

If you swap in your own footage, mind these — each one was learned the hard way:

**Make it exactly 800×480** to match the screen. Don't count on `object-fit` to rescue you.

**Bake the color grade into the video file; don't use CSS filters.** An extra color matrix on every frame is more than this GPU can handle.

**Encode as H.264 Baseline, pixel format `yuv420p`, ideally 12fps with no B-frames.** With a video background, keep decoding cost down first.

**The loop must start and end on the same frame**, or it jumps once per cycle. Two ways: play forward then backward (ping-pong), or move the first few frames to the end and dissolve them back in.

A ready-to-use command (adjust `crop` for framing):

```bash
ffmpeg -i your-footage.mov \
  -filter_complex "[0:v]fps=12,crop=3600:2160:120:0,scale=800:480,format=yuv420p[v];\
[v]split[a][b];[b]reverse,trim=start_frame=1,setpts=PTS-STARTPTS[r];[a][r]concat=n=2:v=1[out]" \
  -map "[out]" -an -c:v libx264 -profile:v baseline -level:v 3.0 -pix_fmt yuv420p \
  -crf 21 -g 24 -movflags +faststart -y display/your-background.mp4
```

Then update `SRC` in `display/index.html`.

**If the video can't play, it falls back to a still image** (`display/*.jpg`), so the screen never goes black.

---

## How it works

```
Codex session logs  ┐
~/.codex/            ├→ bridge/run.py ─→ HTTP :8477 ─→ speaker browser
Claude session logs ┘   (snapshot every 2s)          (polls every 2s)
~/.claude/
```

- **Codex**: mibe follows the event stream in `~/.codex/sessions/`
- **Claude**: `bridge/claude.py` reads the `.jsonl` files in `~/.claude/projects/` directly; quota comes from the claude-hud statusline plugin's cache, falling back to Claude Desktop's local cache
- **Mac Status / Photos**: `bridge/system.py` reads CPU, memory, GPU, and network throughput; `bridge/photos.py` opens the Photos library read-only
- **Who can connect**: the service listens on the LAN but only lets in the Mac itself, the registered speaker, and addresses in `LX04_CONTROL_ALLOW`; every other device on the LAN gets 403 (the Photos page shows family photos). The speaker is recognized by its hardware serial number, so other LAN devices can't pose as it; after replacing the speaker, re-run `./scripts/setup.sh --device <new-ip>`. To view it on your phone, put the phone's IP in the `LX04_CONTROL_ALLOW` environment variable (comma-separated), e.g. in the foreground `LX04_CONTROL_ALLOW=192.168.x.x ./scripts/start.sh` (stop the login agent first, or the port will conflict)
- **Page**: a single HTML file, no framework, no build step. The speaker's WebView is **Chromium 61** (2017), so `inset`, flex `gap`, `backdrop-filter`, and variable fonts are all unsupported

No database, no accounts — a restart just reads everything again.

---

## Troubleshooting

**Repeated crashes, black screen, or duplicate tabs**
Only the visible page loads its video; page requests time out after five seconds and never pile up; losing ADB never reloads the browser, and an explicit open reuses the current tab. After updating, run `python3 scripts/adb-bridge.py --open` on the Mac to load the new page, and restart the bridge agent. Test results, log notes, and rollback steps are in the [stability notes](docs/稳定性说明.md) (Chinese). These fixes reduce known risks; they don't rule out every system reboot or Wi-Fi issue.

**Only the top-left corner shows, with huge text**
The viewport is locked. The screen is physically 800×480, but the browser sees only 534×320 (dpr=1.5). Check that the viewport line in `index.html` hasn't been changed.

**Background doesn't move / shows a still image**
The video failed to decode and fell back to the image layer. Open `http://<your-mac>:8477/_v.html`; it lists `readyState`, `error`, and element sizes.

**Stops moving after a reload**
One we hit ourselves: Chromium 61 caches range responses unreliably and can keep half a file. That's why the server always sends `no-store` for videos. If you changed the caching policy, change it back.

**Swiping up pushes the whole page up**
The browser's address bar is grabbing the gesture. The page restricts this with `touch-action: pan-y` and snapping, but browsers differ; use Via with full screen turned on.

More details in [docs/使用说明.md](docs/使用说明.md) (Chinese).

---

## Credits

**Projects this builds on**

- **[cenzihao1987-svg/lx04-status-display](https://github.com/cenzihao1987-svg/lx04-status-display)** — the upstream project this repo is forked from
- **[yihong0618/mibe](https://github.com/yihong0618/mibe)** (MIT) — Codex session-log following and Xiao AI speaker announcements
- **[LX04 flashing tutorial (SMZDM, Chinese)](https://post.smzdm.com/p/akk3k89r/)** — the source of the [flashing guide](docs/刷机手册.md); firmware links are in its comments

**Assets**

- **[MediaStorm (影视飓风)](https://www.ysjf.com/material)** — the background videos are derived from free sample footage they published.

  `display/meili.mp4`, `display/canyon.mp4`, and the matching `*-lite.mp4` files in this repo **are not the originals**: they were cropped and scaled from 4K
  to 800×480, color-graded (color temperature and saturation heavily changed), transcoded to H.264, and re-cut into seamless loops,
  for use only as this project's UI background. Get the original footage from the [MediaStorm library](https://www.ysjf.com/material).
  If the rights holder considers this use inappropriate, please open an issue and it will be removed right away.
- **[Unsplash](https://unsplash.com/)** — fallback still backgrounds
- `display/night.jpg` and `display/dusk.jpg` (Mac Status and Mac Controls backgrounds) are generated by `scripts/backgrounds.html`
  with no third-party material; after changing parameters, run `./scripts/render-backgrounds.sh` to re-render
- **Roboto** (Apache 2.0) — UI font. It's Android's native font, so the Mac preview matches the device

---

## Optional: voice announcements

Besides the screen, the speaker can read states aloud. This needs a Xiaomi account (via mibe):

```bash
export MI_USER=your-xiaomi-account
export MI_PASS=your-password
./scripts/start.sh --speak
```

The account is used only locally to sign in to Xiaomi's cloud service and never goes through any third party. **Auto-start doesn't announce by default** — LaunchAgents can't see your shell's environment variables, and putting the account in the config file would store it on disk in plain text.

---

## License

MIT, see [LICENSE](LICENSE).

Background videos and images are not covered by this license; copyright belongs to their respective authors — see Credits above.
