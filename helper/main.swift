// Starts Pad Deck Helper (see PadDeckHelper.swift).
import AppKit

let app = NSApplication.shared
app.delegate = Menu.shared
app.setActivationPolicy(.accessory)
app.run()
