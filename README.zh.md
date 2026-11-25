# tvos-cobalt-userscript

[English](README.md) · **中文**

[![CI](https://github.com/comicchang/tvos-cobalt-userscript/actions/workflows/ci.yml/badge.svg)](https://github.com/comicchang/tvos-cobalt-userscript/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**面向 Apple TV（tvOS）Cobalt 网页壳应用的轻量开源油猴脚本（Userscript）补丁工具。**

支持将 [TizenTube Cobalt](https://github.com/reisxd/TizenTubeCobalt) 或任意自定义 `.js` 油猴脚本注入到 tvOS 版 YouTube 等 Cobalt 应用中，并原生适配 [TrollStore Lite TV v2.1.6+](https://github.com/comicchang/trollstore-lite-tv) 的**本机离线热更新脚本托管（`http://127.0.0.1:8787/api/userscripts.js`）**，同时支持多开改 Bundle ID 与本机图标反色处理。

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

### 第 1 步：用自己的 Apple ID 获取官方 tvOS IPA

```sh
# 1) 登录本人 Apple 账号
ipatool auth login

# 2) 下载官方 tvOS 版 YouTube IPA（免费应用需带 --purchase 获取授权）
ipatool download --bundle-identifier com.google.ios.youtube --platform appletv --purchase -o YouTube-tvOS-official.ipa
```

> 如需下载与内置 Profile（`youtube-tvos-4.54.01`）精确匹配的历史版本，可在 `ipatool download` 中附加 `--external-version-id <版本ID>`。

---

### 第 2 步：在本地解密 FairPlay（`cryptid 1 → 0`）

App Store 下载的官方 IPA 带有 FairPlay 加密（`cryptid=1`），必须先在本地解密才能打补丁并 ad-hoc 签名。使用开源 FairPlay 解密工具（如 `fairplay-decrypt`）配合你从自己设备上提取的 16 字节账号凭据解密：

```sh
python decrypt.py \
  --credential /path/to/your/credential.bin \
  YouTube-tvOS-official.ipa \
  YouTube-tvOS-decrypted.ipa
```

`cobalt-patcher` 在打补丁前会自动校验 `LC_ENCRYPTION_INFO_64` 的 `cryptid == 0`，若误传加密包会直接报错提示。

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

## 本地测试

所有单元测试均不依赖任何第三方 IPA：

```sh
uv run --extra dev pytest -v
```

---

## 许可证

MIT License — 详见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)。
