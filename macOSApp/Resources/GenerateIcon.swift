import AppKit

let directory = URL(fileURLWithPath: CommandLine.arguments[1])
try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
for size in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let pixels = size * scale
        let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
                                      bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                                      isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
        let factor = CGFloat(pixels) / 1024
        let transform = NSAffineTransform()
        transform.scale(by: factor)
        transform.concat()
        let rectangle = NSRect(x: 44, y: 44, width: 936, height: 936)
        let background = NSBezierPath(roundedRect: rectangle, xRadius: 214, yRadius: 214)
        NSGradient(starting: NSColor(srgbRed: 0.29, green: 0.47, blue: 0.98, alpha: 1),
                   ending: NSColor(srgbRed: 0.13, green: 0.23, blue: 0.7, alpha: 1))!
            .draw(in: background, angle: -75)
        NSColor.white.withAlphaComponent(0.13).setStroke()
        let screen = NSBezierPath(roundedRect: NSRect(x: 220, y: 240, width: 584, height: 544), xRadius: 60, yRadius: 60)
        screen.lineWidth = 24
        screen.stroke()
        let wave = NSBezierPath()
        wave.move(to: NSPoint(x: 192, y: 488))
        for point in [NSPoint(x: 344, y: 488), NSPoint(x: 412, y: 632), NSPoint(x: 510, y: 344),
                      NSPoint(x: 614, y: 660), NSPoint(x: 694, y: 488), NSPoint(x: 832, y: 488)] {
            wave.line(to: point)
        }
        wave.lineWidth = 49
        wave.lineCapStyle = .round
        wave.lineJoinStyle = .round
        NSColor.white.setStroke()
        wave.stroke()
        NSGraphicsContext.restoreGraphicsState()
        let name = "icon_\(size)x\(size)\(scale == 2 ? "@2x" : "").png"
        try bitmap.representation(using: .png, properties: [:])!.write(to: directory.appendingPathComponent(name))
    }
}
