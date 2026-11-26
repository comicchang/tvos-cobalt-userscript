# tvos-cobalt-userscript

[English](README.md) · **中文**

[![CI](https://github.com/comicchang/tvos-cobalt-userscript/actions/workflows/ci.yml/badge.svg)](https://github.com/comicchang/tvos-cobalt-userscript/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**面向 Apple TV（tvOS）Cobalt 网页壳应用的轻量开源油猴脚本（Userscript）补丁工具。**

支持将 [TizenTube](https://github.com/reisxd/TizenTube) / [TizenTube Cobalt](https://github.com/reisxd/TizenTubeCobalt)（集成 [SponsorBlock](https://github.com/ajayyy/SponsorBlock) 与 [DeArrow](https://github.com/ajayyy/DeArrow)）或任意自定义 `.js` 油猴脚本注入到 tvOS 版 YouTube 等 Cobalt 应用中，原生适配 [TrollStore Lite TV v2.1.6+](https://github.com/comicchang/trollstore-lite-tv) 的**本机离线热更新脚本托管（`http://127.0.0.1:8787/api/userscripts.js`）**以及 [TrollStore TV](https://github.com/comicchang/trollstore-tv) 免越狱冷重启方案，同时支持多开改 Bundle ID 与本机图标反色处理。

> **合规声明**：本仓库**不包含任何** Apple、Google 或 YouTube 的版权代码、二进制文件、`.ipa` 安装包、图标资源或 FairPlay 解密密钥。所有补丁与图标反色操作均在用户本机、针对用户本人合法获取的 `.ipa` 现场执行。详见 [NOTICE](NOTICE)。

---

## 原理说明

实测 tvOS 版 YouTube（`YouTubeUnstable`）**并非 `WKWebView`**，而是 Google 的 **Cobalt + Starboard + V8** HTML5 引擎（渲染 `https://www.youtube.com/tv`）。因此传统 ObjC `WKWebView` hook 无法生效。

本工具在用户本机解密后的 arm64 Mach-O 可执行文件 `__TEXT` 头后空隙中写入极简位置无关（PIC）ARM64 跳板：

```
官方 tvOS IPA（本人 Apple ID 通过 ipatool 下载）
   │
   ▼  本机 FairPlay 解密（cryptid 1 → 0）
未加密 IPA
   │
   ▼  cobalt-patcher（声明式 Profile + ARM64 跳板 + CSP 放行 + 可选反色图标/多开改 ID）
带油猴加载器的未签名 IPA
   │
   ▼  TrollStore Lite TV (:8787) 或 Sideloadly / Xcode 安装
Apple TV ──▶ 自动拉取 http://127.0.0.1:8787/api/userscripts.js（本地离线秒加载、免重打包热更新）
```

- **脚本注入（`HTMLScriptElement::Execute`）**：在主脚本执行前注入引导器，并补发 Cobalt 缺失的 `DOMContentLoaded` 事件。
- **CSP 放行（`DirectiveList::AddDirective`）**：自动放行本机 `http://127.0.0.1:8787`、SponsorBlock、`cdn.jsdelivr.net` 及自定义 `--extra-csp` 域名。
- **4K / VP9 HDR 保留**：可选修补重签后的 4K / HDR 门控。
- **多开与反色图标**：可选改写 `CFBundleIdentifier` / `CFBundleDisplayName`，并在 macOS 上直接对用户自己的 `Assets.car` 做 `CIColorInvert` 反色，与 App Store 原版共存一眼区分。

---

## 完整流程：从官方 IPA 到 Apple TV 跑起油猴脚本

### 前置依赖

- macOS + Xcode Command Line Tools（`xcrun --sdk appletvos clang`；使用 `--invert-icon` 需 `actool`）
- [`uv`](https://docs.astral.sh/uv/)（Python 3.11+）
- [`ipatool`](https://github.com/majd/ipatool)（`brew install ipatool`）
- 已越狱的 Apple TV（tvOS 17.x + Relaxin/roothide + [TrollStore Lite TV v2.1.6+](https://github.com/comicchang/trollstore-lite-tv)），或非越狱设备的 Sideloadly / Xcode

---

### 第 1 步：使用 [`majd/ipatool`](https://github.com/majd/ipatool) 下载本人合法拥有的官方 IPA

[`ipatool`](https://github.com/majd/ipatool) 是 GitHub 上的开源命令行工具（MIT 协议），用于登录你自己的 Apple ID 并下载你合法拥有的应用 `.ipa`（下载下来的是原版 FairPlay 加密包，`cryptid=1`）。

```sh
# 1) 安装 ipatool
brew install ipatool

# 2) 登录本人 Apple 账号
ipatool auth login

# 3) 下载官方 tvOS 版 YouTube IPA（免费应用需带 --purchase 绑定账号授权）
ipatool download --bundle-identifier com.google.ios.youtube --platform appletv --purchase -o YouTube-tvOS-official.ipa
```

> 如需下载与内置 Profile（`youtube-tvos-4.54.01`）精确匹配的历史版本，可在 `ipatool download` 中附加 `--external-version-id <版本ID>`（可用 `ipatool list-versions -b com.google.ios.youtube --platform appletv` 查询）。

---

### 第 2 步：使用 [`EthanArbuckle/fairplay-decrypt`](https://github.com/EthanArbuckle/fairplay-decrypt) 在本地解密 FairPlay

[`fairplay-decrypt`](https://github.com/EthanArbuckle/fairplay-decrypt) 是开源纯 Python 解密工具（GPL-3.0 协议，支持 macOS / Linux），配合从你自己越狱设备上提取的 16 字节账号绑定凭据 `credential.bin`，离线解密 arm64 Mach-O 页面并将 `cryptid` 清零为 `0`。

```sh
# 1) 获取并初始化 fairplay-decrypt
git clone https://github.com/EthanArbuckle/fairplay-decrypt.git
cd fairplay-decrypt
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt

# 2) 从你自己的越狱设备提取 16 字节账号凭据 credential.bin
#    （按 fairplay-decrypt 仓库内 credential-retrieval/README.md 说明操作）

# 3) 本地离线解密你合法获取的官方 IPA（输出文件不能预先存在）
python decrypt.py \
  /path/to/YouTube-tvOS-official.ipa \
  /path/to/YouTube-tvOS-decrypted.ipa \
  --credential /path/to/credential.bin
```

`cobalt-patcher` 在打补丁前会自动校验 `LC_ENCRYPTION_INFO_64` 的 `cryptid == 0`，若误传未解密包会直接报错提示。

---

### 第 3 步：打上油猴补丁（含多开 ID + 反色图标）

```sh
git clone https://github.com/comicchang/tvos-cobalt-userscript.git
cd tvos-cobalt-userscript

uv run cobalt-patcher \
  --in  /path/to/YouTube-tvOS-decrypted.ipa \
  --out ./YouTubeTVL-patched.ipa \
  --bundle-id com.google.ios.youtube.tvl \
  --display-name "YouTube TVL" \
  --app-name YouTubeTVL.app \
  --invert-icon
```

常用参数说明：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--profile` | `youtube-tvos-4.54.01` | 内置 Profile 名称或自定义 JSON 路径 |
| `--userscript-url` | `http://127.0.0.1:8787/api/userscripts.js` | 主脚本地址（默认直连 TrollStore Lite TV 本机托管） |
| `--fallback-url` | TizenTube jsDelivr 链接 | 主地址不可用时的回退链接（传 `""` 关闭） |
| `--extra-csp` | *(无)* | 额外放行的 CSP 域名/来源（可重复指定） |
| `--bundle-id` | *(保持原值)* | 修改 `CFBundleIdentifier` 以与原版并存 |
| `--display-name` | *(保持原值)* | 修改主屏显示名称 |
| `--app-name` | *(保持原值)* | 重命名 `Payload/<Name>.app` 目录名 |
| `--invert-icon` | 关闭 | 在 macOS 本机对原包图标执行反色处理（保留 tvOS 3D 视差） |
| `--no-hdr-patches` | 关闭 | 跳过 VP9/HDR 二进制门控补丁 |
| `--no-4k-patch` | 关闭 | 跳过 JS `MediaSource.isTypeSupported` 4K 补丁 |

---

### 第 4 步：安装到 Apple TV 并热更新管理油猴脚本

#### 方式 A：越狱 Apple TV + TrollStore Lite TV（推荐，永久免 7 天重签）

1. 在 Apple TV 上安装 [TrollStore Lite TV v2.1.6+](https://github.com/comicchang/trollstore-lite-tv)（v2.1.5+ 已自动补齐 Cobalt OpenGL ES 3 所需的 GPU IOKit entitlements，防止启动崩溃）。
2. 浏览器打开 `http://<AppleTV-IP>:8787/`：
   - 在页面底部的「**油猴脚本**」区域上传你的 `.js` 脚本（例如先从 npm/jsDelivr 下载 `@foxreis/tizentube/dist/userScript.js` 并以 `01-tizentube.js` 上传，也可继续上传任意自定义 `.js`）。
   - 将 `YouTubeTVL-patched.ipa` 拖进页面顶部上传安装。
3. 在 Apple TV 打开 **YouTube TVL**，回到网页商店即可看到「已拉取 N 次」计数上升，电视端可直接使用去广告、SponsorBlock 与 TizenTube 设置菜单。后续新增/停用/修改脚本只需在 `:8787` 操作并重启 App，**无需重新打包 IPA**。

#### 方式 B：非越狱 Apple TV（Sideloadly / Xcode）

如果没有运行本机 `:8787` 托管服务，可将 `--userscript-url` 直接指向在线 HTTPS 脚本地址（或局域网静态服务器），再用 Sideloadly / Xcode 签名安装：

```sh
uv run cobalt-patcher \
  --in YouTube-tvOS-decrypted.ipa \
  --out ./YouTubeTVL-cdn.ipa \
  --userscript-url https://cdn.jsdelivr.net/npm/@foxreis/tizentube/dist/userScript.js \
  --fallback-url ""
```

---

## 参考工具

以下外部开源工具**均未打包进本仓库**，按需单独安装使用：

| 工具 | 许可证 | 作用 | 简明用法 |
|---|---|---|---|
| [`majd/ipatool`](https://github.com/majd/ipatool) | MIT | 登录本人 Apple 账号，下载合法拥有的官方（加密）tvOS `.ipa` | `ipatool auth login && ipatool download -b com.google.ios.youtube --platform appletv --purchase -o official.ipa` |
| [`EthanArbuckle/fairplay-decrypt`](https://github.com/EthanArbuckle/fairplay-decrypt) | GPL-3.0 | 使用自己设备提取的 16 字节 `credential.bin` 离线解密 FairPlay 加密 IPA | `python decrypt.py official.ipa decrypted.ipa --credential credential.bin` |
| [`reisxd/TizenTube`](https://github.com/reisxd/TizenTube) | GPL-3.0 | 开源 YouTube TV 油猴脚本（npm/jsDelivr `@foxreis/tizentube`，提供去广告、SponsorBlock、DeArrow、倍速等） | 上传 `userScript.js` 到 TrollStore Lite TV `:8787` 或通过 `--userscript-url` 指定 |
| [`reisxd/TizenTubeCobalt`](https://github.com/reisxd/TizenTubeCobalt) | BSD-3-Clause | `reisxd` 维护的 Cobalt 姊妹项目 | 详见上游仓库 |
| [`ajayyy/SponsorBlock`](https://github.com/ajayyy/SponsorBlock) | GPL-3.0 / CC BY-NC-SA 4.0（API 数据库） | 社区广告/赞助片段跳过服务（[sponsor.ajay.app](https://sponsor.ajay.app)），由 TizenTube 在运行时集成（已放行 CSP） | 在 TizenTube 电视端设置中直接启用 |
| [`ajayyy/DeArrow`](https://github.com/ajayyy/DeArrow) | GPL-3.0 | 社区去标题党与缩略图净化服务（[dearrow.ajay.app](https://dearrow.ajay.app)），由 TizenTube 在运行时集成（已放行 CSP） | 在 TizenTube 电视端设置中按需开启 |
| [`comicchang/trollstore-tv`](https://github.com/comicchang/trollstore-tv) 与 [`trollstore-tv-tools`](https://github.com/comicchang/trollstore-tv-tools) | MIT / BSD-4-Clause（`uicache.m`） | tvOS 17.0 免越狱冷重启存活版 CoreTrust 安装器及配套 `scripts/build-youtube-tvos.sh` 一键脚本 | 使用设备端 `TrollStore TV` 直接安装 |
| [`comicchang/trollstore-lite-tv`](https://github.com/comicchang/trollstore-lite-tv) | MIT | 越狱 Apple TV 永久安装器，自动签 GPU entitlements 并内置 `:8787` 油猴脚本托管 | 浏览器打开 `http://<AppleTV-IP>:8787` 拖入补丁 `.ipa` 与 `.js` 脚本 |
| [`owngoal-dev/Relaxin`](https://github.com/owngoal-dev/Relaxin) | MIT | tvOS 17 RootHide 系越狱环境，提供内核信任缓存能力 | 按 Relaxin 官方渠道在受支持的 Apple TV 上运行 |
| [`Lakr233/vphone-cli`](https://github.com/Lakr233/vphone-cli) | MIT | 基于 Apple Silicon Mac `Virtualization.framework` 的虚拟化固件工具，适合安全研究与调试（按当前最新版本限制，其 tvOS 预设仅支持约 tvOS 26.6–27.x / tvOS 27 左右，不支持 tvOS 17.x） | 可选低风险调试环境，详见上游仓库 |

---

## 本地测试

所有单元测试均不依赖任何第三方 IPA：

```sh
uv run --extra dev pytest -v
```

---

## 许可证、版权与法律免责声明

- **许可证与原创声明**：本项目采用 **MIT License**（[LICENSE](LICENSE)），`Copyright (c) 2026 Comic Chang`。全部代码为洁净室独立编写，**未包含**任何 GPL 或无许可证第三方项目的源码。
- **零版权资产与无解密边界**：本仓库**不包含**任何 `.ipa` 安装包、第三方二进制、固件、图标资源、签名证书或 FairPlay 凭据，且**不实现**任何 DRM / FairPlay 解密功能（加密包 `cryptid != 0` 会被直接拒绝）。
- **商标与非官方声明**：Apple、Apple TV、tvOS、App Store、FairPlay 为 Apple Inc. 商标；Google、YouTube、Cobalt 为 Google LLC 商标。本项目与上述任何主体均无隶属或背书关系。
- **用途边界与免责豁免（AS-IS）**：本项目仅用于学习研究、互操作性分析及个人合法拥有的设备与软件的自定义。软件按**"现状"（AS-IS）**提供，不附带任何明示或默示担保。使用者须自行确保遵守所在司法辖区的法律法规及第三方服务条款；作者及贡献者不对因使用或误用本软件导致的任何设备损坏、数据丢失、账号风险或其他直接/间接损失承担责任。完整声明见 [NOTICE](NOTICE)。

<!--
  🍿 致每一位点开源码的同道中人：
  看个十分钟的视频，前面六十秒贴片，中间两分钟「本期视频由某某品牌赞助」……
  忍不了，真忍不了。装完这个，祝你今晚舒舒服服把片看完，一秒广告不带多看的。
-->
