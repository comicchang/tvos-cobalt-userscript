# tvos-cobalt-userscript

[English](README.md) · [中文](README.zh.md)

[![CI](https://github.com/comicchang/tvos-cobalt-userscript/actions/workflows/ci.yml/badge.svg)](https://github.com/comicchang/tvos-cobalt-userscript/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**A clean-room userscript loader patcher for Cobalt-based tvOS apps (such as YouTube for Apple TV).**

Bring Tampermonkey-style userscripts (e.g. [TizenTube](https://github.com/reisxd/TizenTube) / [TizenTube Cobalt](https://github.com/reisxd/TizenTubeCobalt) with [SponsorBlock](https://github.com/ajayyy/SponsorBlock) & [DeArrow](https://github.com/ajayyy/DeArrow), or your own custom `.js` scripts) to Apple TV — with **local offline hot-reload** via [TrollStore Lite TV](https://github.com/comicchang/trollstore-lite-tv) or cold-reboot survival via [TrollStore TV](https://github.com/comicchang/trollstore-tv), automatic CSP whitelisting, side-by-side bundle ID rewriting, and optional local icon inversion.

> **Legal & Compliance Notice**: This repository contains **zero** copyrighted code, binaries, `.ipa` packages, artwork, or FairPlay decryption keys from Apple, Google, or YouTube. You must supply your own legally obtained `.ipa` from your own Apple Account. See [NOTICE](NOTICE).

---

## Why a Binary Patcher Instead of a WKWebView Tweak?

YouTube for tvOS does **not** use Apple's `WKWebView` or `UIWebView`. It embeds Google's **Cobalt + Starboard + V8** HTML5 runtime rendering `https://www.youtube.com/tv`. Standard Objective-C `WKWebView` swizzling has no effect.

`tvos-cobalt-userscript` injects tiny position-independent ARM64 trampolines into `__TEXT` header padding of your locally decrypted Cobalt executable:

```
Official tvOS IPA (your Apple Account via ipatool)
   │
   ▼  Local FairPlay decryption (cryptid 1 → 0)
Decrypted IPA
   │
   ▼  cobalt-patcher (declarative profile + ARM64 stubs + CSP + optional icon invert)
Patched unsigned IPA (side-by-side Bundle ID + inverted icon)
   │
   ▼  TrollStore Lite TV (:8787) or Sideloadly / Xcode
Apple TV ──▶ fetches http://127.0.0.1:8787/api/userscripts.js (hot-reload, offline-first)
```

- **Script hook (`HTMLScriptElement::Execute`)**: injects a lightweight bootstrapper before the app's main script and dispatches a `DOMContentLoaded` shim (which Cobalt skips).
- **CSP hook (`DirectiveList::AddDirective`)**: prepends allowed script/API origins (`http://127.0.0.1:8787`, SponsorBlock, jsDelivr, plus any `--extra-csp` origins).
- **Optional 4K / VP9 HDR gates**: preserves 4K / HDR playback eligibility after re-signing.
- **Side-by-side install & local icon inversion**: optionally rewrites `CFBundleIdentifier` / `CFBundleDisplayName` and applies `CIColorInvert` locally to your own IPA's tvOS `Assets.car` so the patched app coexists cleanly alongside the stock App Store app.

---

## End-to-End Workflow (From Official IPA to Apple TV)

### Prerequisites

- macOS with Xcode Command Line Tools (`xcrun --sdk appletvos clang` + `actool` for `--invert-icon`)
- [`uv`](https://docs.astral.sh/uv/) (Python 3.11+)
- [`ipatool`](https://github.com/majd/ipatool) (`brew install ipatool`)
- Your own Apple Account & jailbroken Apple TV (tvOS 17.x + Relaxin/roothide + [TrollStore Lite TV v2.1.6+](https://github.com/comicchang/trollstore-lite-tv))

---

### Step 1: Download the Official tvOS IPA ([`majd/ipatool`](https://github.com/majd/ipatool))

[`ipatool`](https://github.com/majd/ipatool) is an open-source CLI (MIT) that lets you log in with your own Apple Account and download `.ipa` packages you legally own. Downloaded packages remain FairPlay-encrypted (`cryptid=1`).

```sh
# 1) Install ipatool
brew install ipatool

# 2) Authenticate with your own Apple Account
ipatool auth login

# 3) Download the official tvOS YouTube IPA (--purchase binds the free app license to your account)
ipatool download --bundle-identifier com.google.ios.youtube --platform appletv --purchase -o YouTube-tvOS-official.ipa
```

> To download a specific historical version matching the built-in `youtube-tvos-4.54.01` profile, pass `--external-version-id <id>` (check available version IDs via `ipatool list-versions -b com.google.ios.youtube --platform appletv`).

---

### Step 2: Decrypt FairPlay Locally ([`EthanArbuckle/fairplay-decrypt`](https://github.com/EthanArbuckle/fairplay-decrypt))

[`fairplay-decrypt`](https://github.com/EthanArbuckle/fairplay-decrypt) is an open-source Python tool (GPL-3.0, macOS/Linux) that decrypts FairPlay-encrypted arm64 IPAs offline using the 16-byte account-bound `credential.bin` retrieved from your own jailbroken device, clearing `cryptid` to `0`.

```sh
# 1) Clone and set up fairplay-decrypt
git clone https://github.com/EthanArbuckle/fairplay-decrypt.git
cd fairplay-decrypt
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt

# 2) Retrieve your own 16-byte account credential from your jailbroken device
#    (follow fairplay-decrypt's credential-retrieval/README.md to generate credential.bin)

# 3) Decrypt your legally obtained IPA locally (output file must not already exist)
python decrypt.py \
  /path/to/YouTube-tvOS-official.ipa \
  /path/to/YouTube-tvOS-decrypted.ipa \
  --credential /path/to/credential.bin
```

`cobalt-patcher` automatically verifies `cryptid == 0` before patching and exits with a clear error if the input IPA is still encrypted.

---

### Step 3: Patch the IPA

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

Key options:

| Flag | Default | Purpose |
|---|---|---|
| `--profile` | `youtube-tvos-4.54.01` | Built-in profile name or path to a custom JSON profile |
| `--userscript-url` | `http://127.0.0.1:8787/api/userscripts.js` | Primary userscript URL (local TrollStore Lite TV host) |
| `--fallback-url` | TizenTube jsDelivr URL | Fallback URL if primary fails (`''` to disable) |
| `--extra-csp` | *(none)* | Extra CSP origin/domain to whitelist (repeatable) |
| `--bundle-id` | *(unchanged)* | Override `CFBundleIdentifier` for side-by-side install |
| `--display-name` | *(unchanged)* | Override `CFBundleDisplayName` on Home Screen |
| `--app-name` | *(unchanged)* | Rename `Payload/<Name>.app` bundle folder |
| `--invert-icon` | `false` | Color-invert the tvOS `Assets.car` icon locally on macOS |
| `--no-hdr-patches` | `false` | Skip VP9/HDR binary gate patches |
| `--no-4k-patch` | `false` | Skip JS `MediaSource.isTypeSupported` 4K shim |

---

### Step 4: Install & Hot-Reload Userscripts on Apple TV

#### On Jailbroken Apple TV (Recommended — Permanent, No 7-Day Resigning)

1. Install [TrollStore Lite TV v2.1.6+](https://github.com/comicchang/trollstore-lite-tv) on your Apple TV (v2.1.5+ automatically signs the required GPU IOKit entitlements `com.apple.security.iokit-user-client-class` so Cobalt's OpenGL ES 3 context initializes cleanly).
2. Open `http://<AppleTV-IP>:8787` in your browser:
   - Upload your userscript(s) in the **Userscripts** section (for example, download `@foxreis/tizentube`'s `userScript.js` as `01-tizentube.js`, plus any custom `.js` of your own).
   - Drag and drop `YouTubeTVL-patched.ipa` into the IPA installer zone.
3. Launch **YouTube TVL** on Apple TV. On `http://<AppleTV-IP>:8787/`, the userscript pull counter will immediately increment — confirming offline local loading. To add, disable, or edit scripts later, just update them on `:8787` and relaunch the app (zero IPA repackaging!).

#### On Non-Jailbroken Apple TV (Sideloadly / Xcode)

If you are not running TrollStore Lite TV locally, point `--userscript-url` directly to an HTTPS userscript URL (or LAN server) and sideload the output IPA with Sideloadly or Xcode:

```sh
uv run cobalt-patcher \
  --in YouTube-tvOS-decrypted.ipa \
  --out ./YouTubeTVL-cdn.ipa \
  --userscript-url https://cdn.jsdelivr.net/npm/@foxreis/tizentube/dist/userScript.js \
  --fallback-url ""
```

---

## Referenced External Tools

None of the tools below are bundled in this repository; install and run them separately as needed:

| Tool | License | Role | Quick Usage |
|---|---|---|---|
| [`majd/ipatool`](https://github.com/majd/ipatool) | MIT | Log into your own Apple Account and download your legally licensed (encrypted) tvOS `.ipa` | `ipatool auth login && ipatool download -b com.google.ios.youtube --platform appletv --purchase -o official.ipa` |
| [`EthanArbuckle/fairplay-decrypt`](https://github.com/EthanArbuckle/fairplay-decrypt) | GPL-3.0 | Decrypt FairPlay-encrypted arm64 IPAs offline using your own device's 16-byte `credential.bin` | `python decrypt.py official.ipa decrypted.ipa --credential credential.bin` |
| [`reisxd/TizenTube`](https://github.com/reisxd/TizenTube) | GPL-3.0 | Open-source YouTube TV userscript (`@foxreis/tizentube` on npm/jsDelivr: ad-blocking, SponsorBlock, DeArrow, speed controls) | Upload `userScript.js` to TrollStore Lite TV `:8787` or pass via `--userscript-url` |
| [`reisxd/TizenTubeCobalt`](https://github.com/reisxd/TizenTubeCobalt) | BSD-3-Clause | Companion Cobalt YouTube TV project by `reisxd` | See upstream repo |
| [`ajayyy/SponsorBlock`](https://github.com/ajayyy/SponsorBlock) | GPL-3.0 / CC BY-NC-SA 4.0 (API DB) | Community sponsor-segment skipping service ([sponsor.ajay.app](https://sponsor.ajay.app)) used at runtime by TizenTube (CSP-whitelisted) | Enabled automatically via TizenTube settings |
| [`ajayyy/DeArrow`](https://github.com/ajayyy/DeArrow) | GPL-3.0 | Crowdsourced non-clickbait titles & thumbnails ([dearrow.ajay.app](https://dearrow.ajay.app)) used at runtime by TizenTube (CSP-whitelisted) | Enabled optionally via TizenTube settings |
| [`comicchang/trollstore-tv`](https://github.com/comicchang/trollstore-tv) & [`trollstore-tv-tools`](https://github.com/comicchang/trollstore-tv-tools) | MIT / BSD-4-Clause (`uicache.m`) | Non-Lite CoreTrust permanent installer for tvOS 17.0 (cold-reboot unjailbroken survival) & BYOIPA build helper `scripts/build-youtube-tvos.sh` | Install patched `.ipa` with on-device `TrollStore TV` |
| [`comicchang/trollstore-lite-tv`](https://github.com/comicchang/trollstore-lite-tv) | MIT | Jailbroken tvOS permanent installer with GPU entitlements & local `:8787` userscript host | Drag patched `.ipa` & `.js` scripts into `http://<AppleTV-IP>:8787` |
| [`owngoal-dev/Relaxin`](https://github.com/owngoal-dev/Relaxin) | MIT | RootHide-based tvOS 17 jailbreak environment providing kernel trustcache support | Follow Relaxin's official setup guide on supported Apple TV hardware |
| [`Lakr233/vphone-cli`](https://github.com/Lakr233/vphone-cli) | MIT | Virtualized Apple firmware runner on Apple Silicon Macs (`Virtualization.framework`) for safe debugging (per current latest version, tvOS presets only support ~tvOS 26.6–27.x / ~tvOS 27, not tvOS 17.x) | Optional research environment; see upstream repo |

---

## Development & Testing

All unit tests run without any third-party IPA:

```sh
uv run --extra dev pytest -v
```

---

## License, Copyright & Legal Disclaimer

- **License**: Released under the **MIT License** ([LICENSE](LICENSE)), `Copyright (c) 2026 Comic Chang`. All source code in this repository is a clean-room original implementation; no GPL or unlicensed third-party source code is included.
- **No Copyrighted Third-Party Assets**: This repository ships **zero** `.ipa` files, binaries, firmware, icons, certificates, or FairPlay keys, and implements **no** DRM/FairPlay decryption (`cryptid != 0` inputs are rejected).
- **Trademarks & Non-Affiliation**: Apple, Apple TV, tvOS, App Store, and FairPlay are trademarks of Apple Inc.; Google, YouTube, and Cobalt are trademarks of Google LLC. This project is independent and not affiliated with or endorsed by any of them.
- **Disclaimer & Limitation of Liability (AS-IS)**: Provided strictly for educational, interoperability, and personal customization use on software and hardware you legally own. Software is provided **"AS IS"** without warranty of any kind. Users are solely responsible for compliance with local laws and third-party Terms of Service; the authors accept no liability for any damages, account actions, or device issues. Full details in [NOTICE](NOTICE).
