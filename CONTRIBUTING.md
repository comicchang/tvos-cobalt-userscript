# Contributing

Contributions are welcome — especially new declarative JSON profiles for additional Cobalt-based
tvOS app versions and improvements to test coverage.

## Rules

1. **No copyrighted third-party assets**: Never commit `.ipa` files, decrypted binaries, app icons,
   or FairPlay keys.
2. **Clean-room code only**: All submitted code must be original or properly licensed under an
   MIT-compatible license with attribution in `NOTICE`.
3. **Verify before opening a PR**:

```sh
uv run --extra dev pytest -v
```
