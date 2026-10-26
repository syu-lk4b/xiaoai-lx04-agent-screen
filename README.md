# LX04 状态屏

中文 | [English](README.en.md)

把刷过机的小米小爱触屏音箱（LX04）变成桌上的 AI 编码状态屏、Mac 性能监控台、应用启动器与电子相框。

![放在桌上的效果](docs/images/hero.jpg)

本项目基于上游 [cenzihao1987-svg/lx04-status-display](https://github.com/cenzihao1987-svg/lx04-status-display)（Codex / Claude 状态页）开发。基础刷机、背景视频转码等通用说明可直接参考上游仓库或 [docs/刷机手册.md](docs/刷机手册.md)、[docs/使用说明.md](docs/使用说明.md)。

---

## 本仓库新增与改进了什么

| Mac 性能监控 | Mac 控制（含音量条） | 相册（人物识别） |
| --- | --- | --- |
| ![Mac 性能监控页](docs/images/page-mac-status.jpg) | ![Mac 控制页](docs/images/page-mac-control.jpg) | ![相册页](docs/images/page-photos.jpg) |

| Codex | Claude |
| --- | --- |
| ![Codex 页](docs/images/page-codex.jpg) | ![Claude 页](docs/images/page-claude.jpg) |

### 1. Mac 本机可视化设置中心（`/setting`）

在 Mac 浏览器打开 **`http://127.0.0.1:8477/setting`**，无需改代码或重启服务，保存后音箱在 **2 秒内自动热同步**：

![设置页](docs/images/page-setting.jpg)

- **实时遥控切页**：在 Mac 上点一下即可让音箱立即跳到指定页面并临时停留
- **页面编排与停留时间**：拖拽或点 `↑` `↓` 调整五页轮播顺序，单独开关任意页面（如只保留 Codex + 相册），并为每一页独立设置停留秒数
- **Mac 控制页应用管理**：自动扫描本机 `/Applications`、`/System/Applications`、`~/Applications` 下的全部应用，自由添加、删除、拖拽排序、点击改名（最多 12 个按钮，图标自动从 `.app` 提取缓存）
- **智能轮播策略开关**：可独立开关「自动轮播」「等待授权时锁定页面（Your turn 优先）」「空闲相框模式」「任务开跑自动跳回」及「手动操作后钉住时长」
- **相册与夜间熄屏配置**：直接调整相册单张切换间隔、选取年限、每轮抽样张数，以及夜间自动熄屏时段（自动同步至 `adb-endpoint.json`）
- **仅限 Mac 本机访问**：`/setting` 与 `/api/settings` 经 `access.mac_only` 严格限制仅本机 loopback 或 Mac 自身网卡可访问，音箱端与局域网其他设备访问一律 `403`

### 2. Mac 控制页（应用启动台 + 音量 / Mute 控制）

- **4×3 图标按钮**：一键让 Mac 熄屏、启动屏保、切换 `caffeinate` 防休眠，或打开任意在 `/setting` 中配置的本机应用
- **底部音量与静音栏**：新增 `音量 -`、`静音 (Mute)`、`音量 +` 三个按钮（可在 `/setting` 开关显示）。优先调节 macOS 系统输出音量；当外接 DisplayPort/HDMI 显示器不支持系统音量调节时，自动回退调节 `Music.app` 音量

### 3. Mac 实时性能监控页

- **实时指标与 4 分钟曲线**：展示 CPU、GPU、内存（按活动监视器口径：App + 联动 + 压缩）、物理网卡上下行网速及高占用进程
- **零权限、按需采集**：CPU 用 `ctypes` 调 mach `host_statistics`、GPU 读 `ioreg -c IOAccelerator`，无需 `sudo`，无人查看时不采样

### 4. 智能人物相册与空闲相框模式

- **直读 Mac「照片」图库**：只读打开 `Photos.sqlite`，随机抽取过去 N 年里包含真实人物的照片（利用「照片」自带人脸识别与美学评分，自动剔除宠物、背景路人与证件照翻拍）
- **服务端预裁切对焦人脸**：Mac 用 `sips` 将横图裁成 `800×480`、竖图裁成 `400×480`（两张竖图并排一屏），裁切框自动对准人脸中心，经同一条 HTTP 隧道传给音箱并缓存，减轻音箱弱 GPU 负担
- **空闲相框模式**：当 Codex 与 Claude 都闲置超过设定时长（默认 10 分钟），自动停在相册页当电子相框；一旦有新任务开始运行，立即跳回对应 Agent 页面

### 5. 其他改进

- **夜间自动熄屏与晨间唤醒**：默认 `23:00–07:00` 自动发 `KEYCODE_SLEEP` 关屏，早上自动唤醒并重开状态页；同一晚若有人手动点亮屏幕不再抢关
- **局域网访问控制与设备序列号钉死**：仅放行 Mac 本机、已登记序列号（`ro.serialno`）的音箱及 `LX04_CONTROL_ALLOW` 白名单 IP；所有写接口强制校验 `X-LX04-Action: 1` 防跨站请求与 DNS 重绑定
- **Claude 额度读取修复**：优先读取 `claude-hud` 状态栏插件实时刷新的 `.usage-cache.json`，回退到 Claude Desktop 缓存
- **一键安装脚本**：`./scripts/setup.sh` 自动完成依赖检查、拉取 `mibe` 并打补丁、跑测试、无线 ADB 连音箱与注册开机自启 LaunchAgent

---

## 快速开始

> 音箱首次刷机、安装 Via 浏览器与开启无线 ADB 的步骤请参考上游仓库及 [docs/刷机手册.md](docs/刷机手册.md)。

```bash
git clone https://github.com/syu-lk4b/xiaoai-lx04-agent-screen.git
cd xiaoai-lx04-agent-screen
./scripts/setup.sh --device 192.168.x.x   # 换成音箱的局域网 IP（省略则自动扫描网段）
```

安装完成后：

- **状态屏预览**：`http://127.0.0.1:8477/`（音箱端 Via 同样打开该地址，由 `adb-bridge` 转发）
- **Mac 设置页**：`http://127.0.0.1:8477/setting`（仅 Mac 本机可打开）

常用维护命令：

| 操作 | 命令 |
| --- | --- |
| 前台运行（调试） | `./scripts/start.sh` |
| 重装开机自启 LaunchAgent | `./scripts/install-agent.sh` |
| 重启状态屏服务 | `launchctl kickstart -k gui/$(id -u)/com.gerry.lx04-status` |
| 重启 ADB 代理服务 | `launchctl kickstart -k gui/$(id -u)/com.gerry.lx04-adb-bridge` |
| 查看日志 | `tail -f ~/Library/Logs/lx04-status.log ~/Library/Logs/lx04-adb-bridge.log` |

---

## 架构简述

```
~/.codex/sessions/*.jsonl ──→ mibe 事件跟读 ──┐
~/.claude/projects/*.jsonl ─→ claude.py 轮询 ─┤→ AGENTS 字典 → snapshot()
 system.py（CPU/内存/GPU/网速）────────────────┤        ↓
 photos.py（相册，每 10 分钟重抽）─────────────┤   HTTP :8477
 settings.py（页面编排/控制项/熄屏配置）────────┘  access.py 校验来源（/setting 仅限 Mac）
                                                      ↓
                     adb reverse（USB）或 toybox nc 代理（无线 ADB，走 WiFi）
                                                      ↓
             音箱 Via 浏览器打开 http://127.0.0.1:8477/（每 2 秒轮询自动同步状态与配置）
```

---

## License

MIT，见 [LICENSE](LICENSE)。背景视频和图片版权归各自作者所有。
