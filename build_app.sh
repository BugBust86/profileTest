#!/bin/bash
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$PROJECT_DIR/dist/ProfilerDesk.app"

swift build --package-path "$PROJECT_DIR/macOSApp" -c release
BIN_DIR="$(swift build --package-path "$PROJECT_DIR/macOSApp" -c release --show-bin-path)"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources/Collector"
cp "$BIN_DIR/ProfilerDesk" "$APP_DIR/Contents/MacOS/ProfilerDesk"
cp "$PROJECT_DIR/macOSApp/Resources/terminal_runner.py" "$APP_DIR/Contents/Resources/terminal_runner.py"
cp "$PROJECT_DIR/collect.sh" "$PROJECT_DIR/collector.py" "$PROJECT_DIR/verify_results.py" "$APP_DIR/Contents/Resources/Collector/"
chmod +x "$APP_DIR/Contents/MacOS/ProfilerDesk" "$APP_DIR/Contents/Resources/Collector/collect.sh"
ICONSET_DIR="$PROJECT_DIR/macOSApp/.build/ProfilerDesk.iconset"
swift "$PROJECT_DIR/macOSApp/Resources/GenerateIcon.swift" "$ICONSET_DIR"
iconutil -c icns "$ICONSET_DIR" -o "$APP_DIR/Contents/Resources/AppIcon.icns"

cat > "$APP_DIR/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleExecutable</key><string>ProfilerDesk</string>
  <key>CFBundleIdentifier</key><string>com.juphoon.ProfilerDesk</string>
  <key>CFBundleName</key><string>ProfilerDesk</string>
  <key>CFBundleDisplayName</key><string>性能测试工作台</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSApplicationCategoryType</key><string>public.app-category.developer-tools</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSPrincipalClass</key><string>NSApplication</string>
</dict></plist>
PLIST
/usr/libexec/PlistBuddy -c "Add :ProfilerProjectPath string $PROJECT_DIR" "$APP_DIR/Contents/Info.plist"
codesign --force --sign - "$APP_DIR"
echo "构建完成：$APP_DIR"
if [[ "${1:-}" == "--open" ]]; then
  open "$APP_DIR"
fi
