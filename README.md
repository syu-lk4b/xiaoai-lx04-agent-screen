# LX04 状态屏

中文 | [English](README.en.md)

把一台闲置的小米小爱触屏音箱，变成桌上的 AI 编码状态屏。

![放在桌上的效果](docs/images/hero.jpg)

Codex 和 Claude 正在干什么、额度还剩多少、哪个任务卡住了需要你回去看一眼——
抬头一瞥就知道，不用切回终端翻窗口。

## 在上游基础上加了什么

本项目基于 [cenzihao1987-svg/lx04-status-display](https://github.com/cenzihao1987-svg/lx04-status-display)（Codex / Claude 状态页），在它之上增加了三块主要功能：

- **Mac 控制**：一页 12 个图标按钮，在音箱上点一下就能让 Mac 熄屏、开屏保、保持不睡眠，或打开常用应用（音乐、终端、Claude、VS Code、Chrome、微信、Obsidian 等）。只执行白名单里的无害动作，不能跑任意命令；按钮图标直接取自应用本身
- **Mac 性能监控**：CPU、GPU、内存、网速的实时数值和最近 4 分钟曲线。全部用免授权的系统接口采集，没人看就不采
- **照片**：直接读 Mac「照片」图库，随机轮播过去两年里有人物的照片，不用再往音箱里导照片。只读、不改图库

| Mac 性能监控 | Mac 控制 | 照片 |
| --- | --- | --- |
| ![Mac 性能监控页](docs/images/page-mac-status.jpg) | ![Mac 控制页](docs/images/page-mac-control.jpg) | ![相册页](docs/images/page-photos.jpg) |

还有一些配套改进：

- **相框模式**：两个 agent 都闲了 10 分钟以上，停在相册页；一有任务开跑就跳回对应页面
- **夜间熄屏**：默认 23:00–07:00 关屏，早上自动亮屏并回到状态页
- **局域网访问控制**：只有 Mac 自己和音箱能访问，局域网其他设备一律拒绝（照片是家里的）
- **一键安装**：`./scripts/setup.sh`；README 中英双语
- 修复 Claude 额度读取：优先读 claude-hud 状态栏插件的缓存

---

## 它长什么样

左右滑动切换五页：Codex、Claude、Mac 状态、Mac 控制、相册。不碰它时自动轮播——相册页停 5 分钟，其他页各 1 分钟（`?photo=N` / `?rotate=N` 改，单位秒）；有任务在等你授权时停在那一页。Codex 和 Claude 都闲了 10 分钟以上，就停在相册页当相框（`?idle=N` 改，`?idle=0` 关）；一有任务跑起来，跳回那个 agent 的页继续轮播。

**相册页**直接读 Mac 上「照片」App 的图库：从过去两年里随机抽有人物的照片（用「照片」App 自己的人脸识别，排除宠物、背景路人和证件翻拍），每 12 秒换一张，竖图两张并排，裁切时对准人脸。音箱不拉原图：Mac 先用 `sips` 把照片裁成屏幕尺寸（横图 800×480、竖图 400×480，人脸放在窗口上方 40%），经状态页同一条 HTTP 隧道传给音箱，裁好的图会缓存——音箱 GPU 弱，拿到手即用，轮播才不卡。不用再往音箱里导照片。只读，不改图库；只在 iCloud 里、本机没下载的照片会跳过。年数和数量可用环境变量 `LX04_PHOTO_YEARS`（默认 2）、`LX04_PHOTO_COUNT`（默认 300）调整。

**夜里自动熄屏**：默认 23:00–07:00，早上自动亮屏并回到状态页。改时段在 `~/Library/Application Support/lx04-status/adb-endpoint.json` 里加 `"sleep": "23:30-07:00"`，`"off"` 关掉。

| Codex | Claude |
| --- | --- |
| ![Codex 页](docs/images/page-codex.jpg) | ![Claude 页](docs/images/page-claude.jpg) |

每页从上到下是三块：

- **抬头** —— 谁的页面，加当前时间（冒号每秒闪一下，一眼看出屏没死）
- **额度** —— 两个窗口的用量和下一次重置时间。Codex 显示「还剩多少」，Claude 显示「已用多少」
- **最近三个会话** —— 项目名、状态、累计耗时。刚完成的会亮一层绿底，持续 10 分钟

背景是循环播放的视频，不是静态图。

---

## 你需要准备

**硬件**

- 小米小爱触屏音箱 LX04（闲鱼上很便宜），**必须先刷机**——原系统没有可用的浏览器。刷机步骤见 [docs/刷机手册.md](docs/刷机手册.md)，有变砖风险，自己掂量
- 一台 Mac，和音箱在同一个 Wi-Fi 下

**软件**

- 你在用 Codex CLI 或者 Claude Code（至少一个，两个都用效果最好）
- [uv](https://docs.astral.sh/uv/)（`brew install uv`），它会自动准备需要的 Python 3.12
- Mac 上的 Android Platform Tools（`brew install --cask android-platform-tools`），并已允许 Mac 无线调试 LX04
- 音箱上装一个能全屏的浏览器，推荐 [Via](https://viayoo.com/)（体积小，能隐藏地址栏）

> 状态屏只读本地文件，不连任何云服务，也不需要小米账号。

---

## 跑起来

### 一键安装

```bash
git clone https://github.com/syu-lk4b/xiaoai-lx04-agent-screen.git
cd xiaoai-lx04-agent-screen
./scripts/setup.sh --device 192.168.x.x   # 换成音箱的局域网 IP
```

`setup.sh` 依次做完这些事，每一步打勾，失败会说明原因和怎么修：

1. 检查 `git`、`uv`、`adb`、系统 Python
2. 拉取 [yihong0618/mibe](https://github.com/yihong0618/mibe)（负责盯 Codex 的会话日志）到 `vendor/`，钉在补丁对应的版本上，再打上 `vendor/mibe.patch`
3. 装 Python 依赖，跑一遍测试
4. 无线 ADB 连上音箱，确认是 LX04、记住地址、检查 Via 装了没有
5. 装两个开机自启的 LaunchAgent，等状态服务起来，并在音箱上打开状态页

重复执行是安全的。不知道音箱 IP 就省掉 `--device`，后台代理会自己扫当前网段；只想先在 Mac 上看看效果，加 `--no-agent`，再手动跑 `./scripts/start.sh`。`./scripts/setup.sh --help` 看全部参数。

装好后在 Mac 浏览器打开 `http://127.0.0.1:8477/` 确认能看到画面。音箱上的 Via 打开的是固定地址 `http://127.0.0.1:8477/`——这里的 `127.0.0.1` 指音箱本机，由代理转到 Mac，所以 Mac 的 DHCP 地址变了也不用改音箱。断线恢复时只修代理，页面自己重试，不会刷新整页或新开页签。

代理默认读 Mac 的 Wi-Fi IPv4。Wi-Fi 不在 `en0` 的话，按 [使用说明](docs/使用说明.md#指定-mac-的局域网地址可选) 配置 `mac_ip`。

### 手动安装

想一步步来，`setup.sh` 做的事等价于：

```bash
git clone https://github.com/yihong0618/mibe.git vendor/mibe
cd vendor/mibe && git checkout 2c73600 && git apply ../mibe.patch && uv sync && cd ../..
./scripts/start.sh            # 前台运行，先在 Mac 浏览器确认画面
./scripts/install-agent.sh    # 装开机自启并在音箱上打开
```

补丁为状态事件、免音箱登录、播报控制和低占用轮询做了适配，具体内容见 [docs/使用说明.md](docs/使用说明.md) 的「我们改了 mibe 什么」。

---

## 让它一直在

`setup.sh` 已经装好了开机自启（单独重装用 `./scripts/install-agent.sh`）。之后：登录 macOS 自动起、进程挂了自动拉起、重启不用管。

| 干什么 | 命令 |
| --- | --- |
| 看日志 | `tail -f ~/Library/Logs/lx04-status.log` |
| 看 ADB 代理日志 | `tail -f ~/Library/Logs/lx04-adb-bridge.log` |
| 改完状态代码重启 | `launchctl kickstart -k gui/$(id -u)/com.gerry.lx04-status` |
| 重启 ADB 代理 | `launchctl kickstart -k gui/$(id -u)/com.gerry.lx04-adb-bridge` |

安装脚本会创建状态屏和 ADB 代理两个 LaunchAgent。卸载时需要同时卸载 `com.gerry.lx04-status` 和 `com.gerry.lx04-adb-bridge`。

---

## 状态怎么看

| 显示 | 含义 |
| --- | --- |
| **Running** | 正在干活 |
| **Your turn** | 停下来等你——在问问题或请求授权。**这个会橙色呼吸，是最该看的** |
| Done | 完成。刚完成的 10 分钟内有绿底 |
| Stopped | 被你中断，或者超过 15 分钟没动静 |

额度数字平时是白的，剩得不多时转橙、见底转红。**平时不喊，喊了才有人听。**

连不上 Mac 时整屏转灰、时钟的冒号停住——一眼能分清是「没任务」还是「断了」。

---

## 换成你自己的背景

默认使用 `display/` 下的 `meili-lite.mp4`（雪山）和 `canyon-lite.mp4`（峡谷）：800×480、12fps、H.264 Baseline、无 B 帧，保持原播放速度以降低解码负担。原来的 `meili.mp4` 和 `canyon.mp4` 仍保留，便于回退。

想换成自己的素材，注意这几条——都是踩出来的：

**尺寸做成 800×480**，和屏幕严格一致。不要指望 `object-fit` 帮你兜底。

**调色烘进视频文件，别用 CSS filter。** 每帧多跑一次色彩矩阵，这台机器的 GPU 扛不住。

**编码用 H.264 Baseline，像素格式 `yuv420p`，建议 12fps、无 B 帧**。保持视频背景时，优先降低解码负担。

**循环必须首尾同帧**，否则每转一圈跳一下。两个办法：正放接倒放（pingpong），或者把开头几帧挪到片尾用溶解接回来。

一条能直接用的命令（按需改 `crop` 取景）：

```bash
ffmpeg -i 你的素材.mov \
  -filter_complex "[0:v]fps=12,crop=3600:2160:120:0,scale=800:480,format=yuv420p[v];\
[v]split[a][b];[b]reverse,trim=start_frame=1,setpts=PTS-STARTPTS[r];[a][r]concat=n=2:v=1[out]" \
  -map "[out]" -an -c:v libx264 -profile:v baseline -level:v 3.0 -pix_fmt yuv420p \
  -crf 21 -g 24 -movflags +faststart -y display/你的背景.mp4
```

然后改 `display/index.html` 里的 `SRC`。

**视频放不出来时会自动退回静态图**（`display/*.jpg`），所以不会黑屏。

---

## 它是怎么工作的

```
~/.codex/sessions/*.jsonl ──→ mibe 事件跟读 ──┐
~/.claude/projects/*.jsonl ─→ claude.py 轮询 ─┤→ AGENTS 字典 → snapshot()
        （每 2 秒）                            │        ↓
 system.py（CPU/内存/GPU/网速）────────────────┤   HTTP :8477
 photos.py（相册，每 10 分钟重抽）─────────────┘  access.py 校验来源
                                                      ↓
                     adb reverse（USB）或 toybox nc 代理（无线 ADB，走 WiFi）
                                                      ↓
             音箱 Via 浏览器打开 http://127.0.0.1:8477/（音箱本机 loopback，每 2 秒拉一次）

 scripts/adb-bridge.py ←──── ADB（WiFi 或 USB）────→ 音箱：找设备、维持转发、开关屏、打开页面
```

- **Codex 侧**：mibe 盯 `~/.codex/sessions/` 的事件流
- **Claude 侧**：`bridge/claude.py` 直接读 `~/.claude/projects/` 的 `.jsonl`；额度来自 claude-hud 状态栏插件的缓存，没有就退到 Claude Desktop 的本地缓存
- **Mac 状态 / 相册**：`bridge/system.py` 读 CPU、内存、GPU、网速；`bridge/photos.py` 只读「照片」图库
- **谁能访问**：服务监听局域网，但只放行 Mac 自己、登记过的音箱和 `LX04_CONTROL_ALLOW` 里的地址，局域网其他设备一律 403（相册是家里的照片）。音箱按硬件序列号认，局域网里别的设备冒充不了；换了音箱重跑 `./scripts/setup.sh --device <新IP>`。想在手机上看，把手机 IP 放进环境变量 `LX04_CONTROL_ALLOW`（逗号分隔），前台运行时例如 `LX04_CONTROL_ALLOW=192.168.x.x ./scripts/start.sh`（先停掉自启的服务，否则端口冲突）
- **页面**：一个 HTML 文件，无框架无构建。因为音箱的 WebView 是 **Chromium 61**（2017 年），`inset`、flex `gap`、`backdrop-filter`、可变字体全都不支持

没有数据库，没有账号体系，重启就是重新读一遍。

---

## License

MIT，见 [LICENSE](LICENSE)。

背景视频和图片不适用本许可证，版权归各自作者所有。
