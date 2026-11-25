"""CLI entrypoint for tvos-cobalt-userscript."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .bundle import patch_ipa
from .macho import load_profile
from .payload import (
    DEFAULT_FALLBACK_USERSCRIPT_URL,
    DEFAULT_LOCAL_USERSCRIPT_URL,
    build_csp_prefix,
    build_loader_js,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cobalt-patcher",
        description=(
            "Patch a locally decrypted Cobalt-based tvOS IPA to load Tampermonkey-style "
            "userscripts (supports local hot-reload via TrollStore Lite TV :8787)."
        ),
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--in", dest="input_ipa", required=True, type=Path, help="Input decrypted .ipa path")
    p.add_argument("--out", dest="output_ipa", required=True, type=Path, help="Output patched .ipa path")
    p.add_argument(
        "--profile",
        default="youtube-tvos-4.54.01",
        help="Built-in profile name or path to custom JSON profile (default: youtube-tvos-4.54.01)",
    )
    p.add_argument(
        "--userscript-url",
        default=DEFAULT_LOCAL_USERSCRIPT_URL,
        help=f"Primary userscript URL (default: {DEFAULT_LOCAL_USERSCRIPT_URL})",
    )
    p.add_argument(
        "--fallback-url",
        default=DEFAULT_FALLBACK_USERSCRIPT_URL,
        help="Fallback userscript URL if primary fails (pass '' to disable)",
    )
    p.add_argument(
        "--extra-csp",
        action="append",
        default=[],
        help="Additional CSP origin/domain to whitelist (repeatable)",
    )
    p.add_argument("--bundle-id", default=None, help="Override CFBundleIdentifier for side-by-side install")
    p.add_argument("--display-name", default=None, help="Override CFBundleDisplayName on tvOS Home Screen")
    p.add_argument("--app-name", default=None, help="Rename Payload/<Name>.app directory inside output IPA")
    p.add_argument(
        "--invert-icon",
        action="store_true",
        help="Color-invert the app's tvOS icon layers locally on macOS for visual distinction",
    )
    p.add_argument("--no-4k-patch", action="store_true", help="Skip MediaSource 4K JS hook")
    p.add_argument("--no-hdr-patches", action="store_true", help="Skip Cobalt VP9/HDR binary gates")
    p.add_argument("--enable-printf-logs", action="store_true", help="Emit printf logs inside hook stubs")
    p.add_argument("--compress", action="store_true", help="Deflate-compress output IPA (slower unpack on TV)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    profile = load_profile(args.profile)
    fallback = args.fallback_url.strip() or None

    loader_js = build_loader_js(
        primary_url=args.userscript_url,
        fallback_url=fallback,
        patch_4k=not args.no_4k_patch,
    )
    csp_prefix = build_csp_prefix(
        profile.get("default_csp_origins", []),
        primary_url=args.userscript_url,
        fallback_url=fallback,
        extra_origins=args.extra_csp,
    )

    try:
        records = patch_ipa(
            args.input_ipa,
            args.output_ipa,
            profile,
            loader_js,
            csp_prefix,
            bundle_id=args.bundle_id,
            display_name=args.display_name,
            app_dir_name=args.app_name,
            invert_icon=args.invert_icon,
            apply_hdr_patches=not args.no_hdr_patches,
            enable_logs=args.enable_printf_logs,
            compress=args.compress,
        )
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Patched IPA written: {args.output_ipa}")
    print(f"Profile: {profile['name']}")
    print(f"Userscript primary:  {args.userscript_url}")
    if fallback:
        print(f"Userscript fallback: {fallback}")
    print(f"CSP prefix: {csp_prefix.decode('ascii').strip()}")
    for rec in records:
        stub_info = f"stub={hex(rec.stub_va)} ({rec.stub_size}B)" if rec.stub_va else "inline"
        print(f"  - {rec.name:24s} site={hex(rec.site_va)} {stub_info}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
