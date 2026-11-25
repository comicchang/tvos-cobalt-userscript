"""Unit tests for tvos-cobalt-userscript (zero third-party IPA required)."""

from __future__ import annotations

import plistlib
import struct
from types import SimpleNamespace

import pytest

from cobalt_patcher.bundle import rewrite_info_plist, should_strip_entry
from cobalt_patcher.cli import build_parser
from cobalt_patcher.macho import BumpRegion, check_unencrypted, encode_arm64_bl, load_profile
from cobalt_patcher.payload import build_csp_prefix, build_loader_js, extract_csp_token


def test_builtin_profile_loads_and_has_required_fields() -> None:
    prof = load_profile("youtube-tvos-4.54.01")
    assert prof["binary_name"] == "YouTubeUnstable"
    assert prof["script_needle"] == "yttv"
    assert len(prof["hooks"]) == 2
    assert len(prof["hdr_gates"]) == 3
    for h in prof["hooks"]:
        assert int(h["entry_va"], 16) > 0x100000000


def test_extract_csp_token() -> None:
    assert extract_csp_token("http://127.0.0.1:8787/api/userscripts.js") == "http://127.0.0.1:8787"
    assert extract_csp_token("https://cdn.jsdelivr.net/npm/foo/bar.js") == "cdn.jsdelivr.net"
    assert extract_csp_token("https://example.com:8443/a.js") == "https://example.com:8443"
    assert extract_csp_token("not-a-url") is None


def test_build_csp_prefix_deduplicates_and_terminates_with_space() -> None:
    raw = build_csp_prefix(
        ["http://127.0.0.1:8787", "cdn.jsdelivr.net"],
        primary_url="http://127.0.0.1:8787/api/userscripts.js",
        fallback_url="https://cdn.jsdelivr.net/npm/x.js",
        extra_origins=["my.lan.local", "cdn.jsdelivr.net"],
    )
    text = raw.decode("ascii")
    assert text.endswith(" ")
    tokens = text.strip().split(" ")
    assert tokens == ["http://127.0.0.1:8787", "cdn.jsdelivr.net", "my.lan.local"]


def test_build_loader_js_local_with_fallback_and_4k() -> None:
    js = build_loader_js(
        primary_url="http://127.0.0.1:8787/api/userscripts.js",
        fallback_url="https://cdn.jsdelivr.net/npm/@foxreis/tizentube/dist/userScript.js",
        patch_4k=True,
    ).decode("ascii")
    assert "http://127.0.0.1:8787/api/userscripts.js" in js
    assert "cdn.jsdelivr.net" in js
    assert "DOMContentLoaded" in js
    assert "MediaSource.isTypeSupported" in js


def test_build_loader_js_without_fallback_or_4k() -> None:
    js = build_loader_js(
        primary_url="https://example.com/my.js",
        fallback_url=None,
        patch_4k=False,
    ).decode("ascii")
    assert "https://example.com/my.js" in js
    assert "MediaSource.isTypeSupported" not in js


def test_encode_arm64_bl_forward_and_backward() -> None:
    # Forward +0x10 bytes -> imm26 = +4 -> 0x94000004
    assert encode_arm64_bl(0x100001000, 0x100001010) == 0x94000004
    # Backward -4 bytes -> imm26 = -1 -> 0x97FFFFFF
    assert encode_arm64_bl(0x100001004, 0x100001000) == 0x97FFFFFF
    with pytest.raises(ValueError):
        encode_arm64_bl(0x100001000, 0x100001002)
    with pytest.raises(ValueError):
        encode_arm64_bl(0x100000000, 0x110000000)


def test_bump_region_alignment_and_overflow() -> None:
    r = BumpRegion(0x101, 0x120)
    assert r.reserve(5, align=4) == 0x104
    assert r.reserve(4, align=16) == 0x110
    with pytest.raises(ValueError):
        r.reserve(0x20, align=4)


def test_check_unencrypted_rejects_fairplay() -> None:
    fake_encrypted = SimpleNamespace(encryption_info=SimpleNamespace(crypt_id=1))
    with pytest.raises(RuntimeError, match="FairPlay-encrypted"):
        check_unencrypted(fake_encrypted)  # type: ignore[arg-type]

    fake_plain = SimpleNamespace(encryption_info=SimpleNamespace(crypt_id=0))
    check_unencrypted(fake_plain)  # type: ignore[arg-type]


def test_rewrite_info_plist_and_strip_entries() -> None:
    raw = plistlib.dumps(
        {"CFBundleIdentifier": "com.example.orig", "CFBundleDisplayName": "Orig", "Keep": 1},
        fmt=plistlib.FMT_BINARY,
    )
    updated = plistlib.loads(
        rewrite_info_plist(raw, bundle_id="com.example.alt", display_name="Alt TV")
    )
    assert updated["CFBundleIdentifier"] == "com.example.alt"
    assert updated["CFBundleDisplayName"] == "Alt TV"
    assert updated["Keep"] == 1

    prefix = "Payload/Foo.app/"
    assert should_strip_entry("Payload/Foo.app/SC_Info/Foo.sinf", prefix)
    assert should_strip_entry("Payload/Foo.app/_CodeSignature/CodeResources", prefix)
    assert not should_strip_entry("Payload/Foo.app/Info.plist", prefix)
    assert not should_strip_entry("iTunesMetadata.plist", prefix)


def test_cli_parser_defaults() -> None:
    args = build_parser().parse_args(["--in", "a.ipa", "--out", "b.ipa", "--invert-icon"])
    assert str(args.input_ipa) == "a.ipa"
    assert str(args.output_ipa) == "b.ipa"
    assert args.profile == "youtube-tvos-4.54.01"
    assert args.invert_icon is True
    assert "127.0.0.1:8787" in args.userscript_url
