# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目是什么

把刷过机的小米小爱触屏音箱 LX04 变成 AI 编码状态屏：Mac 上读取本地 Codex / Claude Code 会话日志，通过 HTTP `:8477` 提供一个单页 HTML，音箱上的 Via 浏览器全屏显示。无数据库、无账号体系、无构建步骤。

## 常用命令

```bash
./scripts/setup.sh [--device <音箱IP>] [--no-agent]  # 一键安装（幂等）：依赖检查、mibe 钉版本+打补丁、测试、连音箱、装自启

# 手动：拉上游 mibe 并打补丁（vendor/mibe/ 不入库，只有 vendor/mibe.patch 入库）
git clone https://github.com/yihong0618/mibe.git vendor/mibe
cd vendor/mibe && git apply ../mibe.patch && uv sync && cd ../..

./scripts/start.sh              # 前台跑状态服务（用 vendor/mibe/.venv 的 python）
./scripts/start.sh --speak      # 带小爱语音播报，需要 MI_USER / MI_PASS
./scripts/speak.sh on|off|toggle  # 运行中切播报（POST /api/speak）
./scripts/install-agent.sh      # 安装/重装两个 LaunchAgent（幂等）

# 测试（标准库 unittest）
python3 -m unittest discover -s scripts -p test_adb_bridge.py -v
vendor/mibe/.venv/bin/python -m unittest discover -s bridge -p 'test_*.py' -v  # 额度解析与 Mac 控制，run.py 依赖 mibe
python3 -m unittest scripts/test_adb_bridge.py -k test_three_confirmed_failures_rebuild_once  # 单个
python3 scripts/adb-bridge.py --self-test   # 不连设备的自检

# 改完代码后重启服务 / 在音箱当前页签重新加载页面
launchctl kickstart -k gui/$(id -u)/com.gerry.lx04-status
launchctl kickstart -k gui/$(id -u)/com.gerry.lx04-adb-bridge
python3 scripts/adb-bridge.py --open

# 日志
tail -f ~/Library/Logs/lx04-status.log ~/Library/Logs/lx04-adb-bridge.log
```

验证：Mac 浏览器打开 `http://127.0.0.1:8477/`，设置页 `http://127.0.0.1:8477/setting`（仅限 Mac 本机访问），数据接口 `/api/status`，视频诊断页 `/_v.html`，静态背景诊断 `/?stable=1`。

## 架构

```
~/.codex/sessions/*.jsonl ─→ mibe（事件跟读）─→ STATUS_OBSERVER → bridge/run.py observe()
~/.claude/projects/*.jsonl ─→ bridge/claude.py（独立线程每 2s 轮询）─────┘
                                                    ↓
                                  AGENTS 字典 → snapshot() → HTTP :8477
                                                    ↓
                     音箱 Via 打开 http://127.0.0.1:8477/（音箱本机 loopback）
                     ← scripts/adb-bridge.py 用 adb reverse 或音箱上 toybox netcat 代理转到 Mac
```

- **`bridge/run.py`**：进程入口。把 `sys.argv` 透传给 `mibe monitor`，在 mibe 的 asyncio 循环里跑 Codex 事件跟读；另起 HTTP 线程和 Claude 轮询线程。`mibe.STATUS_OBSERVER = observe` 是 `vendor/mibe.patch` 加的挂载点。
  - 两个 agent 共用同一形状的状态字典（`_new_agent`），`_agent_view` 一份代码服务两边——改字段时两边都要保持一致。
  - 一把 `_lock` 管全部 `AGENTS`。Claude 侧解析（含 `zstd` 子进程）必须在锁外完成，锁内只换指针。
  - 状态机：`Working / NeedsApproval / Done / Stopped / Ready`；`Working` 超过 15 分钟无事件显示为 `Stopped`，`NeedsApproval` 最长 12 小时。`_pick_active` 只在严格更优时切换显示的 agent（自带迟滞）。
  - 启动时 `_seed_codex` 直接扫最近的会话文件，而不是用 mibe 的 `--replay-existing`（`start.sh` 固定传 `none`，否则开播报会把历史事件全念一遍）。
- **`bridge/claude.py`**：只暴露 `read_tasks()` / `read_usage()` 两个纯函数，不认识锁和 HTTP。会话状态读 jsonl 尾部 128KB，按最后一条有意义的行判定（`NOISE` 集合里的类型要跳过，尤其 `last-prompt`）。项目名靠从 `cwd` 逐级上溯匹配编码后的目录名（目录名不可逆）。额度优先读 claude-hud 状态栏插件落盘的 `~/.claude/plugins/claude-hud/.usage-cache.json`（Claude Code 运行时持续刷新），回退到 Claude Desktop 的磁盘缓存（`~/Library/Application Support/Claude/Cache`，只有 Desktop 在跑才新鲜）；两者都按超过 6 小时视为陈旧返回 `None`。
- **额度形状**：两边统一为 `{"primary"|"secondary": {remaining, window_minutes, resets_at}}`。Codex 的 `rate_limits` 会混入其他 `limit_id`（如 `premium`，窗口为 null），`_usage()` 对没有窗口的记录返回 `None`，不能让它覆盖已有额度。
- **`bridge/access.py`**：所有请求（含页面、静态文件、`/api/status`）在 `Handler._permitted()` 里先过它。服务必须监听 `0.0.0.0`（无线 ADB 下音箱代理经局域网连 Mac），所以靠请求本身把关：来源 IP 只能是 loopback、Mac 自己的网卡地址、`adb-endpoint.json` 登记的音箱或 `LX04_CONTROL_ALLOW`；`Host` 只能是 `localhost` 或 IP 字面量（挡 DNS 重绑定）；POST 另需请求头 `X-LX04-Action: 1`（挡 CSRF，页面里所有写请求都要带）。局域网其他设备一律 403——想在手机上看状态页要加进 `LX04_CONTROL_ALLOW`。
- **`bridge/mac.py`**：第三页「Mac 控制」的动作，`POST /api/action?name=<动作>`。只执行 `COMMANDS` 白名单、`awake`（`caffeinate -w` 绑定服务进程）、音量控制（`volume_down` / `volume_mute` / `volume_up`，优先调系统输出音量，DisplayPort/HDMI 外接显示器为 `missing value` 时自动回退调 `Music.app` 音量）以及用户在 `/setting` 登记的本机 `.app`（限制在 `/Applications`、`/System/Applications`、`~/Applications` 下）。按钮列表存在 `settings.json` 的 `mac_items`（最多 12 个，4×3 网格），可在 `/setting` 直接扫描本机应用增删、拖拽排序与改名。按钮图标 `GET /icons/<动作>.png`（或 `/api/mac/app-icon?path=`）取应用 `Info.plist` 的 `CFBundleIconFile`（兼容 iOS Wrapper 应用及通用图标兜底），经 `sips` 现转并缓存到 `~/Library/Caches/lx04-status/icons`。
- **ADB 调试注意**：`adb shell input text` 的 URL 含 `&` 时必须整体加引号（`adb shell "input text 'http://...?a=1&b=2'"`），否则被设备 shell 当后台符截断；Via 对同一 URL 回车不会重新加载，换个查询参数才会。
- **`bridge/system.py`**：第三页「Mac 状态」的数据，进 `/api/status` 的 `system` 字段。只用免授权接口：CPU 用 `ctypes` 调 mach `host_statistics` 取 tick 做差（**别换成 `top -l 2`**，一次吃 0.7 秒 CPU）；内存按活动监视器口径（App + 联动 + 压缩，不含文件缓存）；网速只算 `en*` 物理网卡的 `<Link#>` 行（VPN 会重复计数）。GPU 读 `ioreg -c IOAccelerator` 的 `Device Utilization %`（免 root；功耗/温度要 sudo powermetrics，拿不到）。惰性采集、缓存 2 秒，没人看就不采；CPU/网速是区间速率，首次采样为 `None`。`history` 保留最近 120 个点（约 4 分钟）给页面画图，采样间隔超过 `HISTORY_GAP` 就清空，避免把两段不相连的时间连成一条线。
- **状态页图表**：手写 SVG（无图表库），规格按 dataviz skill——2px 线、约 12% 面积底、末端点带底色环、发丝网格；CPU/GPU/内存固定 0–100 同一把尺子，网速取 1/2/5×10ⁿ 上限。网络两条线的颜色 `#3987e5`/`#d95926` 已对卡片底色验证过；换卡片底色要重跑 dataviz 的 `validate_palette.js`。触屏无悬停，故不做 tooltip。
- **`bridge/photos.py`**：第五页「相册」。只读打开「照片」图库的 `Photos.sqlite`（`mode=ro`），从最近 `LX04_PHOTO_YEARS`（默认 2）年里随机抽 `LX04_PHOTO_COUNT`（默认 300）张有人物的照片，每 10 分钟重抽。支持在 `/setting` 通过 `ZPERSON` + `ZFACECROP` 人物头像墙勾选特定家人（存于 `settings.json` 的 `photo_people`），勾选后仅抽取包含指定家人的照片且裁切时只对焦家人的脸；未勾选时则抽取所有 `ZDETECTIONTYPE=1`（人；3/4 是狗/猫）且 `ZSIZE ≥ 0.05` 的脸（挡背景路人），另要求 `ZOVERALLAESTHETICSCORE ≥ 0.3` 排除证件翻拍。地点/日期取 `ZMOMENT`（很多没标题，只显示日期）。服务端把竖图排成相邻的，页面相邻两张竖图并排。时间是 Core Data 纪元（+978307200）。只在 iCloud、本地没文件的照片在列表阶段就剔掉。出图优先用图库现成的 `resources/derivatives/…_1_105_c.jpeg`，`sips` 缩放后按人脸中心（`ZCENTERX/Y`，摆正后坐标、原点左下）裁成 800×480（竖图 400×480），人脸放在窗口上方 40%；缩放和 `--cropOffset` 必须分两次 `sips` 调用，写在一条里偏移会被忽略。改了裁切逻辑要清缓存，缓存在 `~/Library/Caches/lx04-status/photos`。接口 `GET /api/photos`（不进 `/api/status`）和 `GET /photos/<uuid>.jpg`，后者只认当前列表里的 id；`/api/photos/people` 与 `/api/photos/person-avatar` 仅限 Mac 本机 `/setting` 访问。LaunchAgent 起的 python 实测能读图库，不需要额外授权。
- **背景图**：Codex/Claude 页是照片 + 视频；状态页 `night.jpg`、控制页 `dusk.jpg` 由 `scripts/backgrounds.html` 程序生成（固定种子），`scripts/render-backgrounds.sh` 用无头 Chrome 渲染成 800×480 JPG。这两页静态铺底，不放视频、不运镜。
- **`bridge/settings.py` & `display/setting.html`**：Mac 本机专用的设置页（`/setting`，通过 `access.mac_only` 限制仅 loopback 与 Mac 自身网卡 IP 可访问，音箱与 `LX04_CONTROL_ALLOW` 均返回 403）。配置落盘在 `~/Library/Application Support/lx04-status/settings.json`，管理五页的启用开关、顺序、各页停留秒数、自动轮播/等待授权锁定/空闲相框模式策略、相册抽样参数及夜间熄屏时段（同步写入 `adb-endpoint.json` 的 `sleep`），并提供 `POST /api/focus?page=<id>` 远程切页。配置随 `/api/status` 的 `settings` 字段下发，前端 `applySettings()` 动态重排 `.page` 的 `left`、`.deck` 宽度与底部 `.dots`，2 秒内无刷新生效。
- **`display/index.html`**：单文件、无框架、无构建。五页横排在 `.deck` 上，页序与启用状态由 `settings.pages` 动态决定（默认 codex / claude / sys / mac / photo）；隐藏页通过 `.page:not(.active) .photo` 暂停动画。状态页、控制页、相册页不在 `PAGES` 里（没有 agent 数据和视频），时钟由 `EXTRA` 统一刷新。翻页逻辑在 `follow()`：每页停 `dwell(k)`（在 `/setting` 按页配置，URL 查询参数 `?photo=N` / `?rotate=N` 仍可覆盖）；相册没照片时轮播跳过它；两边都没有 `Working` 的任务超过 `IDLE_MS`（默认 10 分钟）就进「相框模式」停在相册页不轮播，一有任务跑起来就跳到那个 agent 的页并恢复轮播；手动滑动/点按钮或设置页远程切页钉住 `PIN_MS`（默认 60 秒）；有 agent 处于 `NeedsApproval` 时停在那页不轮播。后端的 `active` 字段前端已不用。`__V__` 由服务端 `_stamp()` 替换为 mp4 指纹做缓存破坏。视频源在文件内 `SRC` 常量。
- **`scripts/adb-bridge.py`**：常驻 LaunchAgent，每 10s 检查一次：找到/重连 LX04（保存的 endpoint → 扫网段）、确保音箱 `127.0.0.1:8477` 转到 Mac、必要时打开 Via。设计原则（有回归测试保护）：ADB 状态未知不算失败；连续三次确认失效才重建代理；杀旧代理前核对进程身份；ADB 重连不刷新页面、打开时复用当前页签。优先使用 `.local-tools/platform-tools/adb`。认过的音箱钉住 `ro.serialno`（存在 `adb-endpoint.json` 的 `serialno`），之后扫描、重连、USB 都只认它——`access.py` 放行登记的音箱地址，不钉的话局域网里假 adbd 自称 `mi_lx04` 就能被登记；换音箱用 `setup.sh --device` 重钉。
  - 夜间熄屏：`adb-endpoint.json` 的 `"sleep"`（默认 `23:00-07:00`，`"off"` 关）。时段开始发 `KEYCODE_SLEEP`(223) 一次，结束发 `KEYCODE_WAKEUP`(224) 并打开状态页；判定在纯函数 `sleep_step()`：同一次开机只关一次（夜里有人点亮就不再关），半夜重启再关一次，没关过就不叫醒。熄屏时段里不自动开页面（输入按键会把屏幕叫醒）。音箱自身熄屏时间是「永不」，熄屏后系统会杀掉 Via，早上由叫醒流程重开。小米锁屏相册（`micolauncher` 的 `LockScreenActivity`）盖住 Via 是正常行为，不要去抢回前台。

## 设备硬约束（改前端前必读）

- 音箱 WebView 是 **Chromium 61**：不支持 `inset`、flex `gap`、`backdrop-filter`、可变字体、`AbortController`。字体只能用静态字重 woff2。
- 屏幕物理 800×480，dpr=1.5。viewport 必须保持 `width=800, viewport-fit=cover`，**不能加 `initial-scale`**，否则只显示左上角。
- GPU 很弱：调色烘进视频文件，不用 CSS filter；隐藏页释放视频；视频规格 800×480、12fps、H.264 Baseline、`yuv420p`、无 B 帧、首尾同帧循环（ffmpeg 命令见 README）。
- 服务端对 `.html` 和 `.mp4` 一律 `Cache-Control: no-store`，且必须用 `HTTP/1.1` 回 206 Range 响应——Chromium 61 会把分片缓存成残缺文件，导致 `MEDIA_ERR_SRC_NOT_SUPPORTED`。不要改回缓存。
- 页面 `fetch` 最多一个在途、五秒超时，连续三次失败才显示离线。

## 约定

- 代码注释、文档、日志均为中文，注释重在解释「为什么」（踩过的坑），保持这个风格。README 有中英两份（`README.md` / `README.en.md`），改一份要同步另一份。
- `setup.sh` 的 `MIBE_REF` 钉着 `vendor/mibe.patch` 对应的上游提交；升级 mibe 重新导出补丁时一起改它（README 手动安装那段的 `git checkout` 也要改）。
- 改 mibe 时在 `vendor/mibe` 里改完后重新导出 `vendor/mibe.patch`（`cd vendor/mibe && git diff > ../mibe.patch`），否则改动不会进版本库。
- 开机自启默认不带播报：LaunchAgent 读不到 shell 环境变量，不要把小米账号写进 plist。
- `.local-tools/`（刷机工具、固件备份、mtkclient、本机 adb）和 `LOCAL_SETUP.md` 是本机私有内容，不要提交、不要在仓库文档里引用其中的实际地址或路径。刷机/写分区相关操作风险极高，不要主动执行。
