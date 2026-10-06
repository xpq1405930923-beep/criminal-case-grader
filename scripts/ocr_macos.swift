#!/usr/bin/env swift
// macOS fallback. No network, no credentials. Outputs OCR observations for review.
import Foundation
import Vision
import PDFKit
import ImageIO
import AppKit

struct OCRLine: Codable {
    let text: String
    let confidence: Float
    let box: [Double] // normalized x, y, width, height; origin at bottom left
}
struct OCRPage: Codable {
    let page: Int
    let text: String
    let lines: [OCRLine]
}
struct OCROutput: Encodable {
    let engine = "macOS Vision"
    let source: String
    let review_required = true
    let pages: [OCRPage]
}

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}

func recognize(_ cgImage: CGImage, orientation: CGImagePropertyOrientation = .up, page: Int) throws -> OCRPage {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    // Do not auto-correct a candidate's misspellings.
    request.usesLanguageCorrection = false
    let supported = try request.supportedRecognitionLanguages()
    request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"].filter { supported.contains($0) }
    let handler = VNImageRequestHandler(cgImage: cgImage, orientation: orientation, options: [:])
    try handler.perform([request])
    let lines: [OCRLine] = (request.results ?? []).compactMap { observation in
        guard let best = observation.topCandidates(1).first else { return nil }
        let b = observation.boundingBox
        return OCRLine(text: best.string, confidence: best.confidence,
                       box: [Double(b.minX), Double(b.minY), Double(b.width), Double(b.height)])
    }
    // Preserve Vision reading order; boxes let the reviewer correct multi-column order.
    return OCRPage(page: page, text: lines.map(\.text).joined(separator: "\n"), lines: lines)
}

let args = Array(CommandLine.arguments.dropFirst())
if args == ["--help"] || args == ["-h"] {
    print("用法：swift ocr_macos.swift 输入图片或PDF [输出.json]\n识别结果必须回看原图；手写、分栏和否定词不保证准确。")
    exit(0)
}
guard args.count == 1 || args.count == 2 else {
    fail("用法：swift ocr_macos.swift 输入图片或PDF [输出.json]")
}
let inputURL = URL(fileURLWithPath: args[0]).standardizedFileURL
guard FileManager.default.fileExists(atPath: inputURL.path) else { fail("输入文件不存在。") }
if args.count == 2 && URL(fileURLWithPath: args[1]).standardizedFileURL == inputURL {
    fail("输出不能覆盖输入文件。")
}

do {
    var pages: [OCRPage] = []
    if inputURL.pathExtension.lowercased() == "pdf" {
        guard let pdf = PDFDocument(url: inputURL), !pdf.isLocked, pdf.pageCount > 0 else {
            fail("无法读取PDF，文件可能加密、损坏或没有页面。")
        }
        for index in 0..<pdf.pageCount {
            guard let page = pdf.page(at: index) else { fail("无法读取PDF第\(index + 1)页。") }
            let bounds = page.bounds(for: .mediaBox)
            let scale = min(3.0, 3000.0 / max(bounds.width, bounds.height))
            let thumbnail = page.thumbnail(of: NSSize(width: max(1, bounds.width * scale),
                                                       height: max(1, bounds.height * scale)), for: .mediaBox)
            var imageRect = NSRect(origin: .zero, size: thumbnail.size)
            guard let cg = thumbnail.cgImage(forProposedRect: &imageRect, context: nil, hints: nil) else {
                fail("无法渲染PDF第\(index + 1)页。")
            }
            pages.append(try recognize(cg, page: index + 1))
        }
    } else {
        guard let imageSource = CGImageSourceCreateWithURL(inputURL as CFURL, nil),
              let cg = CGImageSourceCreateImageAtIndex(imageSource, 0, nil) else {
            fail("无法读取图片，请使用PNG、JPEG、HEIC或PDF。")
        }
        let properties = CGImageSourceCopyPropertiesAtIndex(imageSource, 0, nil) as? [CFString: Any]
        let exif = (properties?[kCGImagePropertyOrientation] as? NSNumber)?.uint32Value ?? 1
        pages.append(try recognize(cg, orientation: CGImagePropertyOrientation(rawValue: exif) ?? .up, page: 1))
    }
    guard pages.contains(where: { !$0.lines.isEmpty }) else {
        fail("未识别到文字；不要据此判断空白答案，请回看原图或改用其他OCR。")
    }
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
    let data = try encoder.encode(OCROutput(source: inputURL.lastPathComponent, pages: pages))
    if args.count == 2 {
        let outputURL = URL(fileURLWithPath: args[1])
        try FileManager.default.createDirectory(at: outputURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: outputURL, options: .atomic)
        print("OCR转录已保存，共\(pages.count)页；请核对原图。")
    } else {
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data("\n".utf8))
    }
} catch {
    fail("本地OCR失败（\(type(of: error))）；请用Codex查看原图，或调用已配置的OCR工具。")
}
