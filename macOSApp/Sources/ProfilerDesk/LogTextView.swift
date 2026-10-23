import AppKit
import SwiftUI

/// NSTextView preserves both manual scroll position and text selection on every append.
struct LogTextView: NSViewRepresentable {
    let text: String
    let jumpToLatest: Int

    final class Coordinator {
        var rendered = ""
        var jump = 0
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSScrollView()
        scroll.hasVerticalScroller = true
        scroll.hasHorizontalScroller = true
        scroll.autohidesScrollers = false
        scroll.borderType = .noBorder
        scroll.drawsBackground = true
        scroll.backgroundColor = NSColor(srgbRed: 0.065, green: 0.086, blue: 0.13, alpha: 1)
        let view = NSTextView(frame: .zero)
        view.isEditable = false
        view.isSelectable = true
        view.isRichText = false
        view.isAutomaticLinkDetectionEnabled = false
        view.font = .monospacedSystemFont(ofSize: 12, weight: .regular)
        view.textColor = NSColor(srgbRed: 0.81, green: 0.86, blue: 0.93, alpha: 1)
        view.backgroundColor = scroll.backgroundColor
        view.insertionPointColor = .white
        view.textContainerInset = NSSize(width: 16, height: 14)
        view.minSize = .zero
        view.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        view.isVerticallyResizable = true
        view.isHorizontallyResizable = true
        view.autoresizingMask = []
        view.textContainer?.containerSize = view.maxSize
        view.textContainer?.widthTracksTextView = false
        view.setAccessibilityLabel("终端日志，可手动滚动，新增日志不自动滚动")
        scroll.documentView = view
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        guard let view = scroll.documentView as? NSTextView else { return }
        let coordinator = context.coordinator
        if coordinator.rendered != text {
            let origin = scroll.contentView.bounds.origin
            let selection = view.selectedRanges
            let attributes: [NSAttributedString.Key: Any] = [
                .font: NSFont.monospacedSystemFont(ofSize: 12, weight: .regular),
                .foregroundColor: NSColor(srgbRed: 0.81, green: 0.86, blue: 0.93, alpha: 1)
            ]
            if text.hasPrefix(coordinator.rendered) {
                let added = (text as NSString).substring(from: (coordinator.rendered as NSString).length)
                view.textStorage?.append(NSAttributedString(string: added, attributes: attributes))
            } else {
                view.textStorage?.setAttributedString(NSAttributedString(string: text, attributes: attributes))
            }
            coordinator.rendered = text
            if let container = view.textContainer { view.layoutManager?.ensureLayout(for: container) }
            view.sizeToFit()
            let length = (text as NSString).length
            let retainedSelection = selection.filter { $0.rangeValue.location + $0.rangeValue.length <= length }
            view.selectedRanges = retainedSelection.isEmpty ? [NSValue(range: NSRange(location: 0, length: 0))] : retainedSelection
            let maxY = max(0, view.frame.height - scroll.contentView.bounds.height)
            scroll.contentView.scroll(to: NSPoint(x: max(0, origin.x), y: min(origin.y, maxY)))
            scroll.reflectScrolledClipView(scroll.contentView)
        }
        if coordinator.jump != jumpToLatest {
            coordinator.jump = jumpToLatest
            view.scrollRangeToVisible(NSRange(location: (text as NSString).length, length: 0))
        }
    }
}
