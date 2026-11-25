# Security Policy

## Scope

`tvos-cobalt-userscript` is a local build-time binary patching tool. It does not transmit user
credentials, Apple IDs, or `.ipa` files anywhere.

## Important Notes

- Never commit `.ipa` files, FairPlay credentials, Apple developer certificates, or device logs
  into issues or pull requests.
- Only load userscripts from sources you trust: scripts run with full access to the Cobalt page
  context inside the patched application.

## Reporting a Vulnerability

Please open a private security advisory on GitHub or report the issue without attaching any
copyrighted binaries or personal credentials.
