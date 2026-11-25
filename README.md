# tvos-cobalt-userscript

[English](README.md) · [中文](README.zh.md)

[![CI](https://github.com/comicchang/tvos-cobalt-userscript/actions/workflows/ci.yml/badge.svg)](https://github.com/comicchang/tvos-cobalt-userscript/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**A clean-room userscript loader patcher for Cobalt-based tvOS apps (such as YouTube for Apple TV).**

Bring Tampermonkey-style userscripts (e.g. [TizenTube Cobalt](https://github.com/reisxd/TizenTubeCobalt) or your own custom `.js` scripts) to Apple TV — with **local offline hot-reload** via [TrollStore Lite TV](https://github.com/comicchang/trollstore-lite-tv), automatic CSP whitelisting, side-by-side bundle ID rewriting, and optional local icon inversion.

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

### Step 1: Download the Official tvOS IPA (Your Own Apple Account)

```sh
# 1) Authenticate with your Apple Account
ipatool auth login

# 2) Download the official tvOS YouTube IPA (check version matches profile, e.g. 4.54.01)
ipatool download --bundle-identifier com.google.ios.youtube --platform appletv --purchase -o YouTube-tvOS-official.ipa
```

> To download a specific historical version matching the built-in `youtube-tvos-4.54.01` profile, pass `--external-version-id <id>` (use `ipatool list-versions -b com.google.ios.youtube --platform appletv` if supported, or pull the `.ipa` from your own device backup).

---

### Step 2: Decrypt FairPlay Locally

App Store IPAs are FairPlay-encrypted (`cryptid != 0`) and cannot be patched or ad-hoc signed while encrypted. Decrypt your own IPA locally using an open-source FairPlay decryptor (for example `fairplay-decrypt`) with the 16-byte account-bound credential retrieved from your own device:

```sh
python decrypt.py \
  --credential /path/to/your/credential.bin \
  YouTube-tvOS-official.ipa \
  YouTube-tvOS-decrypted.ipa
```

`cobalt-patcher` automatically verifies `cryptid == 0` before patching and aborts with a clear message if the input IPA is still encrypted.

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

## Development & Testing

All unit tests run without any third-party IPA:

```sh
uv run --extra dev pytest -v
```

---

## License

MIT License — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
