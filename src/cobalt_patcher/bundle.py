"""IPA bundle rewriting: Info.plist customization, FairPlay metadata cleanup, and local icon inversion."""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from .macho import PatchRecord, patch_macho_bytes

_STRIP_DIR_NAMES = {"SC_Info", "_CodeSignature"}


def rewrite_info_plist(
    raw_plist: bytes,
    *,
    bundle_id: str | None = None,
    display_name: str | None = None,
) -> bytes:
    """Update CFBundleIdentifier / CFBundleDisplayName in an Info.plist payload."""
    data = plistlib.loads(raw_plist)
    if bundle_id:
        data["CFBundleIdentifier"] = bundle_id
    if display_name:
        data["CFBundleDisplayName"] = display_name
    return plistlib.dumps(data, fmt=plistlib.FMT_XML)


def should_strip_entry(entry_name: str, app_prefix: str) -> bool:
    """Return True for FairPlay SC_Info / stale _CodeSignature entries inside the app bundle."""
    if not entry_name.startswith(app_prefix):
        return False
    rel = entry_name[len(app_prefix) :]
    head = rel.split("/", 1)[0]
    return head in _STRIP_DIR_NAMES


def invert_assets_car(assets_car_bytes: bytes) -> bytes:
    """Color-invert a tvOS Assets.car AppIcon locally on macOS using CoreUI + CoreImage + actool."""
    if sys.platform != "darwin":
        raise RuntimeError("--invert-icon requires macOS with Xcode / actool")
    script = Path(__file__).resolve().parents[2] / "scripts" / "invert_icon.swift"
    if not script.is_file():
        raise FileNotFoundError(f"Missing icon inversion helper: {script}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        in_car = tmp_dir / "in.car"
        xcassets = tmp_dir / "Inv.xcassets"
        out_dir = tmp_dir / "out"
        in_car.write_bytes(assets_car_bytes)
        out_dir.mkdir()

        res = subprocess.run(
            ["swift", str(script), str(in_car), str(xcassets)],
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            raise RuntimeError(f"Icon inversion failed:\n{res.stderr}\n{res.stdout}")

        actool_res = subprocess.run(
            [
                "xcrun",
                "actool",
                "--compile",
                str(out_dir),
                "--platform",
                "appletvos",
                "--target-device",
                "tv",
                "--minimum-deployment-target",
                "13.0",
                "--app-icon",
                "AppIcon",
                "--output-partial-info-plist",
                str(tmp_dir / "partial.plist"),
                str(xcassets),
            ],
            capture_output=True,
            text=True,
        )
        out_car = out_dir / "Assets.car"
        if actool_res.returncode != 0 or not out_car.is_file():
            raise RuntimeError(f"actool compilation failed:\n{actool_res.stderr}\n{actool_res.stdout}")
        return out_car.read_bytes()


def patch_ipa(
    input_ipa: Path,
    output_ipa: Path,
    profile: dict[str, Any],
    loader_js: bytes,
    csp_prefix: bytes,
    *,
    bundle_id: str | None = None,
    display_name: str | None = None,
    app_dir_name: str | None = None,
    invert_icon: bool = False,
    apply_hdr_patches: bool = True,
    enable_logs: bool = False,
    compress: bool = False,
) -> list[PatchRecord]:
    """Patch a decrypted tvOS IPA and write the modified unsigned IPA to output_ipa."""
    bin_name = profile["binary_name"]
    with zipfile.ZipFile(input_ipa, "r") as zin:
        infos = zin.infolist()
        bin_info = next(
            (i for i in infos if i.filename.startswith("Payload/") and i.filename.endswith(f".app/{bin_name}")),
            None,
        )
        if bin_info is None:
            raise FileNotFoundError(
                f"Target executable 'Payload/*.app/{bin_name}' not found in {input_ipa}"
            )

        app_prefix = bin_info.filename[: -len(bin_name)]  # e.g. "Payload/YouTubeUnstable.app/"
        new_app_prefix = f"Payload/{app_dir_name}/" if app_dir_name else app_prefix
        if app_dir_name and not app_dir_name.endswith(".app"):
            new_app_prefix = f"Payload/{app_dir_name}.app/"

        binary_buf = bytearray(zin.read(bin_info.filename))
        records = patch_macho_bytes(
            binary_buf,
            profile,
            loader_js,
            csp_prefix,
            apply_hdr_patches=apply_hdr_patches,
            enable_logs=enable_logs,
        )

        plist_entry = f"{app_prefix}Info.plist"
        assets_entry = f"{app_prefix}Assets.car"
        inverted_car: bytes | None = None
        if invert_icon:
            if not any(i.filename == assets_entry for i in infos):
                raise FileNotFoundError(f"{assets_entry} not found in {input_ipa}")
            inverted_car = invert_assets_car(zin.read(assets_entry))

        output_ipa.parent.mkdir(parents=True, exist_ok=True)
        compression = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
        with zipfile.ZipFile(output_ipa, "w", compression=compression) as zout:
            for info in infos:
                if should_strip_entry(info.filename, app_prefix):
                    continue
                target_name = info.filename
                if target_name.startswith(app_prefix):
                    target_name = new_app_prefix + target_name[len(app_prefix) :]

                if info.filename == bin_info.filename:
                    payload = bytes(binary_buf)
                elif info.filename == plist_entry and (bundle_id or display_name):
                    payload = rewrite_info_plist(
                        zin.read(info.filename),
                        bundle_id=bundle_id,
                        display_name=display_name,
                    )
                elif info.filename == assets_entry and inverted_car is not None:
                    payload = inverted_car
                else:
                    payload = zin.read(info.filename)

                out_info = zipfile.ZipInfo(target_name, date_time=info.date_time)
                out_info.external_attr = info.external_attr
                out_info.compress_type = compression if not target_name.endswith("/") else zipfile.ZIP_STORED
                zout.writestr(out_info, payload)

    return records
