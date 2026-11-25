#!/usr/bin/env swift
// Extract tvOS AppIcon layers from an existing Assets.car, apply CIColorInvert,
// and emit a valid AppIcon.brandassets catalog ready for `xcrun actool`.
//
// Usage: swift invert_icon.swift <input-Assets.car> <output-dir.xcassets>

import Cocoa
import CoreImage
import Foundation

guard CommandLine.arguments.count == 3 else {
    fputs("Usage: invert_icon.swift <input-Assets.car> <output.xcassets>\n", stderr)
    exit(2)
}

let carURL = URL(fileURLWithPath: CommandLine.arguments[1])
let xcassets = URL(fileURLWithPath: CommandLine.arguments[2])

guard dlopen("/System/Library/PrivateFrameworks/CoreUI.framework/CoreUI", RTLD_NOW) != nil,
      let catalogCls = NSClassFromString("CUICatalog") as? NSObject.Type else {
    fputs("Failed to load CoreUI CUICatalog\n", stderr)
    exit(1)
}

let allocSel = NSSelectorFromString("alloc")
let initSel = NSSelectorFromString("initWithURL:error:")
guard let allocObj = catalogCls.perform(allocSel)?.takeUnretainedValue() as? NSObject else {
    fputs("CUICatalog alloc failed\n", stderr)
    exit(1)
}
typealias InitFn = @convention(c) (AnyObject, Selector, NSURL, UnsafeMutablePointer<NSError?>?) -> AnyObject?
let imp = allocObj.method(for: initSel)
let initFn = unsafeBitCast(imp, to: InitFn.self)
var err: NSError? = nil
guard let catalog = initFn(allocObj, initSel, carURL as NSURL, &err) as? NSObject else {
    fputs("CUICatalog initWithURL failed: \(err?.localizedDescription ?? "unknown")\n", stderr)
    exit(1)
}

let ciCtx = CIContext(options: nil)
let fm = FileManager.default

func writeJSON(_ obj: Any, to url: URL) throws {
    let data = try JSONSerialization.data(withJSONObject: obj, options: [.prettyPrinted, .sortedKeys])
    try fm.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
    try data.write(to: url)
}

func invertAndSave(cgImage: CGImage, opaque: Bool, to url: URL) throws {
    let ciIn = CIImage(cgImage: cgImage)
    guard let filter = CIFilter(name: "CIColorInvert") else {
        throw NSError(domain: "invert", code: 1)
    }
    filter.setValue(ciIn, forKey: kCIInputImageKey)
    guard let ciOut = filter.outputImage,
          let outCG = ciCtx.createCGImage(ciOut, from: ciIn.extent) else {
        throw NSError(domain: "invert", code: 2)
    }
    let w = outCG.width, h = outCG.height
    let alphaInfo: CGImageAlphaInfo = opaque ? .noneSkipLast : .premultipliedLast
    let cs = CGColorSpaceCreateDeviceRGB()
    guard let ctx = CGContext(
        data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
        space: cs, bitmapInfo: alphaInfo.rawValue
    ) else {
        throw NSError(domain: "invert", code: 3)
    }
    if opaque {
        ctx.setFillColor(red: 0, green: 0, blue: 0, alpha: 1)
        ctx.fill(CGRect(x: 0, y: 0, width: w, height: h))
    }
    ctx.draw(outCG, in: CGRect(x: 0, y: 0, width: w, height: h))
    guard let finalCG = ctx.makeImage() else {
        throw NSError(domain: "invert", code: 4)
    }
    let rep = NSBitmapImageRep(cgImage: finalCG)
    guard let png = rep.representation(using: .png, properties: [:]) else {
        throw NSError(domain: "invert", code: 5)
    }
    try fm.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
    try png.write(to: url)
}

typealias ImagesFn = @convention(c) (AnyObject, Selector, NSString) -> NSArray?
let imagesSel = NSSelectorFromString("imagesWithName:")
let imagesFn = unsafeBitCast(catalog.method(for: imagesSel), to: ImagesFn.self)

func extractLayer(_ name: String) -> [(scale: Int, image: CGImage)] {
    guard let arr = imagesFn(catalog, imagesSel, name as NSString) else { return [] }
    var out: [(Int, CGImage)] = []
    for item in arr {
        guard let obj = item as? NSObject else { continue }
        let scaleNum = (obj.value(forKey: "scale") as? NSNumber)?.doubleValue ?? 1.0
        let imgSel = NSSelectorFromString("image")
        guard obj.responds(to: imgSel) else { continue }
        typealias ImgFn = @convention(c) (AnyObject, Selector) -> Unmanaged<CGImage>?
        let fn = unsafeBitCast(obj.method(for: imgSel), to: ImgFn.self)
        if let cg = fn(obj, imgSel)?.takeUnretainedValue() {
            out.append((Int(round(scaleNum)), cg))
        }
    }
    return out
}

try? fm.removeItem(at: xcassets)
let brand = xcassets.appendingPathComponent("AppIcon.brandassets")
try writeJSON(["info": ["author": "xcode", "version": 1]], to: xcassets.appendingPathComponent("Contents.json"))
try writeJSON([
    "assets": [
        ["filename": "AppIconSmall.imagestack", "idiom": "tv", "role": "primary-app-icon", "size": "400x240"],
        ["filename": "AppIconLarge.imagestack", "idiom": "tv", "role": "primary-app-icon", "size": "1280x768"],
    ],
    "info": ["author": "xcode", "version": 1],
], to: brand.appendingPathComponent("Contents.json"))

let stacks: [(stack: String, layerPrefix: String, scales: [Int])] = [
    ("AppIconSmall", "AppIconSmall", [1, 2]),
    ("AppIconLarge", "AppIconLarge", [1]),
]

var extractedCount = 0
for spec in stacks {
    let stackDir = brand.appendingPathComponent("\(spec.stack).imagestack")
    try writeJSON([
        "info": ["author": "xcode", "version": 1],
        "layers": [
            ["filename": "Front.imagestacklayer"],
            ["filename": "Back.imagestacklayer"],
        ],
    ], to: stackDir.appendingPathComponent("Contents.json"))

    for (layerName, opaque) in [("Front", false), ("Back", true)] {
        let layerDir = stackDir.appendingPathComponent("\(layerName).imagestacklayer")
        let imgSet = layerDir.appendingPathComponent("Content.imageset")
        try writeJSON(["info": ["author": "xcode", "version": 1]], to: layerDir.appendingPathComponent("Contents.json"))

        let found = extractLayer("\(spec.layerPrefix)/\(layerName)/Content")
        var imagesJSON: [[String: Any]] = []
        for s in spec.scales {
            let pick = found.first(where: { $0.scale == s }) ?? found.first
            guard let cg = pick?.image else { continue }
            let fname = "img@\(s)x.png"
            try invertAndSave(cgImage: cg, opaque: opaque, to: imgSet.appendingPathComponent(fname))
            imagesJSON.append(["filename": fname, "idiom": "tv", "scale": "\(s)x"])
            extractedCount += 1
        }
        try writeJSON(["images": imagesJSON, "info": ["author": "xcode", "version": 1]],
                      to: imgSet.appendingPathComponent("Contents.json"))
    }
}

guard extractedCount >= 4 else {
    fputs("Expected >=4 icon layers in Assets.car, extracted only \(extractedCount)\n", stderr)
    exit(1)
}
print("Inverted \(extractedCount) tvOS icon layers -> \(xcassets.path)")
