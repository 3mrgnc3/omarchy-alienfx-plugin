import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

// AlienFX bar widget and control popup.
//
// Styling note: every colour, font and metric below resolves from the active
// Omarchy theme at runtime - via the host-forwarded `bar.*`, the `Color`
// singleton and `Style` - exactly as the first-party plugins do. There are no
// hardcoded chrome colours, so the popup re-themes itself the moment the user
// switches themes, with no code here reacting to it.
//
// The one place a literal colour appears is the colour preview swatch and the
// per-zone dots. Those are *content*: they depict the actual light being sent
// to the hardware, so they must not be themed.
//
// No HID code lives here. Everything is delegated to the `alienfx-ctl` CLI,
// because plugins run unsandboxed inside the long-lived omarchy-shell process
// and a crash in here would take the bar down with it.
Panel {
  id: root
  moduleName: "mrgnc.alienfx"
  ipcTarget: "mrgnc.alienfx"
  // The Panel base registers an IpcHandler for ipcTarget automatically, and a
  // target only permits one. We declare our own below (it adds refresh()), so
  // hand ownership over rather than letting the two collide.
  manageIpc: false

  // ------------------------------------------------------------ state mirror
  // What the CLI last reported. Authoritative for the hardware, and for things
  // the user cannot edit here (profiles, theme name, device access) - but NOT
  // for rendering the controls. See the ui* block below.
  property var st: ({})
  property var profileNames: []
  property string currentProfile: ""
  property string themeName: ""
  property bool hasKeymap: true
  property bool devicesOk: true
  property bool loaded: false
  property string errorText: ""

  property bool pickerOpen: false
  property bool saveOpen: false
  // Keyboard cursor over the effect row. First key press only reveals the
  // cursor rather than moving it, so a stray arrow key cannot change the
  // lighting - the same behaviour as the power plugin's profile row.
  property bool cursorActive: false
  property int cursorIndex: 0

  // ------------------------------------------------------- locally owned UI
  // Omarchy's controls are stateless by contract: Toggle expects the consumer
  // to flip `checked` in response to clicked(), and PanelSlider resets its knob
  // to `value` the moment a drag ends. Binding either of them to state that
  // only arrives after a process round-trip makes the control revert under the
  // user's finger and then jump ~300ms later - which reads as "the first click
  // did nothing".
  //
  // So these properties are what the controls bind to, and they change the
  // instant the user acts. The CLI is told afterwards.
  property bool uiThemesync: false
  property bool uiZonesync: true
  property string uiEffect: "gradient"
  property string uiZone: "kbd"
  property string uiProfile: ""
  property int uiBrightness: 26

  // The gradient range: two ends, each its own swatch. "a" is the near end and
  // the only one Solid uses.
  property string uiColorA: "ff7800"
  property string uiColorB: ""
  property string pickTarget: "a"

  // Picker channels, mirroring whichever end is being edited.
  property int pickR: 255
  property int pickG: 120
  property int pickB: 0

  // Solid is a single flat colour, so the far end has nothing to mean.
  readonly property bool rangeUsable: root.uiEffect !== "solid"
  readonly property color previewA: Model.hexColor(root.uiColorA)
  readonly property color previewB: Model.hexColor(root.uiColorB !== "" ? root.uiColorB
                                                  : Model.complementHex(root.uiColorA))

  // While the user is driving, an in-flight state read must not overwrite what
  // they just set. Any local edit marks a settling window; reconciliation waits
  // for quiet.
  property double localEditAt: 0
  readonly property int settleMs: 900
  function touch() { root.localEditAt = Date.now(); settleTimer.restart() }
  function editing() { return (Date.now() - root.localEditAt) < root.settleMs }

  // True while any slider is under the pointer. Reconciliation must never move
  // a control the user is physically holding.
  readonly property bool hasRange: root.uiColorB !== "" && root.uiColorB !== root.uiColorA

  readonly property bool anyDragging: brightnessSlider.dragging
    || redSlider.dragging || greenSlider.dragging || blueSlider.dragging

  readonly property bool themesync: uiThemesync
  readonly property bool zonesync: uiZonesync
  readonly property string effect: uiEffect
  readonly property string selectedZone: uiZone
  readonly property int brightness: uiBrightness

  // Theme-derived chrome.  // Theme-derived chrome. Falls back to the Color singleton when the widget is
  // rendered outside a bar host.
  readonly property color fg: root.bar ? root.bar.foreground : Color.foreground
  readonly property string fontFamily: root.bar ? root.bar.fontFamily : Style.font.family

  // The directory this QML was loaded from. After `omarchy plugin add` that is
  // a full clone of the repo, so the CLI and installer are sitting right here
  // even before anything has been installed into ~/.local.
  readonly property string pluginDir: {
    var url = String(Qt.resolvedUrl("."))
    if (url.indexOf("file://") === 0) url = url.substring(7)
    while (url.length > 1 && url.charAt(url.length - 1) === "/") url = url.substring(0, url.length - 1)
    return url
  }

  // Absolute paths: the shell process does not necessarily carry ~/.local/bin
  // on PATH, so resolving by name would work for some users and not others.
  // `cliPath` is settled at runtime by cliResolver below, preferring an
  // installed CLI and falling back to the bundled one.
  property string cliPath: setting("cliPath", Quickshell.env("HOME") + "/.local/bin/alienfx-ctl")
  property bool cliResolved: false
  readonly property string bundledCli: pluginDir + "/cli/bin/alienfx-ctl"
  readonly property string bundledInstaller: pluginDir + "/install.sh"
  readonly property string wizardPath: setting("wizardPath", Quickshell.env("HOME") + "/.local/bin/omarchy-alienfx-wizard")
  readonly property string iconGlyph: setting("icon", Model.ICON.alien)

  // Setup is unfinished in either of two ways, and they look identical from
  // the outside: there is no CLI at all, or there is one but the udev rule was
  // never installed so every write is denied. Both leave the lights dead, so
  // both offer to finish the install.
  readonly property bool setupNeeded: resolverDone && (!cliResolved || (loaded && !devicesOk))

  // Content colour, not chrome: this is the light being sent to the hardware.
  readonly property color previewColor: Qt.rgba(pickR / 255, pickG / 255, pickB / 255, 1)

  // ---------------------------------------------------------------- plumbing
  property bool resolverDone: false

  // Pick the first CLI that actually exists, preferring an installed one. Done
  // in one shell call rather than probing from QML, which has no file tests.
  function resolveCli() {
    if (!cliResolver.running) cliResolver.running = true
  }

  function onCliResolved(text) {
    var found = String(text || "").trim()
    root.resolverDone = true
    if (found !== "") {
      root.cliPath = found
      root.cliResolved = true
      root.refresh()
      if (root.opened) root.startStream()
    } else {
      root.cliResolved = false
      root.loaded = false
    }
  }

  function runWizard() {
    Quickshell.execDetached([root.wizardPath])
    root.close()
  }

  function runSetup() {
    // The udev step needs root, so this has to happen in a terminal where sudo
    // (or pkexec) can prompt - not silently from the shell process.
    Quickshell.execDetached([
      "omarchy-launch-or-focus-tui", "--app-id=alienfx-setup",
      "bash", "-lc",
      "'" + root.bundledInstaller + "' ; printf '\\n[press Enter to close] ' ; read -r _"
    ])
    root.close()
  }

  function refresh() {
    if (!root.cliResolved) { resolveCli(); return }
    if (!stateProc.running) stateProc.running = true
  }

  function ingest(text) {
    var parsed = Model.parseState(text)
    // Keep the last good state on a parse failure rather than blanking the
    // panel; a transient failure should not look like a broken plugin.
    if (!parsed) return
    root.st = parsed

    // Always adopt what the user cannot edit from here.
    root.profileNames = parsed.profiles || []
    root.currentProfile = parsed.current_profile || ""
    root.themeName = parsed.theme || ""
    root.hasKeymap = parsed.has_keymap === true
    root.devicesOk = parsed.devices_ok !== false
    root.loaded = true
    root.errorText = ""

    // Adopt control values only once the user has stopped touching them.
    if (!root.editing()) adoptFromState()
  }

  function adoptFromState() {
    var s = root.st
    if (!s) return
    // Belt and braces: a live drag owns its control outright, whatever the
    // settling window thinks.
    if (root.anyDragging) return
    root.uiThemesync = s.themesync === true
    root.uiZonesync = s.zonesync !== false
    root.uiEffect = s.effect ? String(s.effect) : "gradient"
    root.uiZone = s.selected_zone ? String(s.selected_zone) : "kbd"
    // Only follow the reported profile when the user has not picked something
    // else, and fall back to the first available so Load is never a no-op.
    var reported = root.currentProfile !== "" ? root.currentProfile
                 : (root.profileNames.length > 0 ? String(root.profileNames[0]) : "")
    if (root.uiProfile === "" || root.uiProfile === reported
        || root.profileNames.indexOf(root.uiProfile) < 0) {
      root.uiProfile = reported
    }
    if (s.brightness !== undefined) root.uiBrightness = s.brightness
    root.uiColorA = Model.zoneHex(s, root.uiZone)
    root.uiColorB = s.secondary ? String(s.secondary) : ""
    loadPickerFromTarget()
  }

  function loadPickerFromZone(zone) {
    root.uiColorA = Model.zoneHex(root.st, zone)
    root.pickTarget = "a"
    loadPickerFromTarget()
  }

  // Point the RGB sliders at whichever end of the range is being edited.
  function loadPickerFromTarget() {
    var hex = root.pickTarget === "b"
      ? (root.uiColorB !== "" ? root.uiColorB : Model.complementHex(root.uiColorA))
      : root.uiColorA
    var rgb = Model.hexToRgb(hex)
    root.pickR = rgb.r
    root.pickG = rgb.g
    root.pickB = rgb.b
  }

  function editEnd(which) {
    root.pickTarget = which
    root.pickerOpen = true
    loadPickerFromTarget()
  }

  property bool streamReady: false

  function startStream() {
    if (!root.cliResolved || streamProc.running) return
    streamProc.running = true
  }

  function stopStream() {
    if (!streamProc.running) return
    streamProc.write("quit\n")
    streamProc.running = false
    root.streamReady = false
  }

  // Route a command to the hardware.
  //
  // While the popup is open a single `alienfx-ctl stream` process holds the
  // device descriptors, so a change costs ~107ms instead of ~294ms - spawning
  // an interpreter and reopening the chassis node per frame was most of the
  // latency. Anything that cannot go down the stream falls back to a one-shot.
  function run(args) {
    if (root.streamReady && streamProc.running && Model.streamSafe(args)) {
      streamProc.write(args.join(" ") + "\n")
      refreshTimer.restart()
      return
    }
    Quickshell.execDetached([root.cliPath].concat(args))
    refreshTimer.restart()
  }

  // `live` marks an intermediate drag frame: it skips the slow power-button
  // NVRAM programming and drops rather than queues if the hardware is busy.
  // Which zones a colour change should drive.
  //
  // While the user is dragging and zones are synced, only the keyboard is
  // written: its ioctl is ~2ms per packet against the chassis's ~63ms, so
  // including the chassis every frame roughly halves the frame rate for the
  // sake of three small lights. The chassis catches up on release.
  function colorTargets(live) {
    if (!root.uiZonesync) return root.uiZone
    return live ? "kbd" : "all"
  }

  // `live` marks an intermediate frame: skip the power button's ~2s NVRAM walk,
  // and drop rather than queue if the hardware is busy.
  function applyPickedColor(live) {
    var hex = Model.rgbToHex(root.pickR, root.pickG, root.pickB)
    var args
    if (root.pickTarget === "b") {
      root.uiColorB = hex
      // The far end is a property of the range, not of a zone, so it is not
      // sent with --zones.
      args = ["set", "--color2", hex, "--fast"]
    } else {
      root.uiColorA = hex
      args = ["set", "--zones", colorTargets(live), "--color", hex, "--fast"]
    }
    // An intermediate frame may be dropped if the hardware is busy; the value
    // the user settles on may not, or saved state ends up behind the UI.
    if (live) args.push("--drop-if-busy")
    root.run(args)
  }

  function setBrightness(value, live) {
    root.uiBrightness = Model.clamp255(value)
    var args = ["set", "--zones", live ? colorTargets(true) : "all",
                "--brightness", String(root.uiBrightness), "--fast"]
    if (live) args.push("--drop-if-busy")
    root.run(args)
  }

  // Once the user has stopped, re-apply durably so the power button's colour
  // survives a power transition. This is the only path that pays the ~2s NVRAM
  // cost, and it never runs while anything is being dragged.
  function commitDurable() {
    if (!root.cliResolved) return
    Quickshell.execDetached([root.cliPath, "commit"])
    refreshTimer.restart()
  }

  function moveCursor(delta) {
    if (!root.cursorActive) { root.cursorActive = true; return }
    var count = Model.EFFECT_ORDER.length
    root.cursorIndex = Math.max(0, Math.min(count - 1, root.cursorIndex + delta))
  }

  function activateCursor() {
    if (!root.cursorActive) return
    var name = Model.EFFECT_ORDER[root.cursorIndex]
    if (name) root.applyEffect(String(name))
  }

  // One path for changing the effect, so the mouse and the keyboard cannot
  // drift apart.
  function applyEffect(name) {
    root.uiEffect = name
    if (name !== "gradient") root.uiThemesync = false
    root.cursorIndex = Math.max(0, Model.EFFECT_ORDER.indexOf(name))
    root.touch()
    root.run(["set", "--effect", name, "--fast"])
  }

  function zoneDotColor(zone) {
    var rgb = Model.hexToRgb(Model.zoneHex(root.st, zone))
    return Qt.rgba(rgb.r / 255, rgb.g / 255, rgb.b / 255, 1)
  }

  Process {
    id: streamProc
    command: [root.cliPath, "stream"]
    stdinEnabled: true
    running: false
    onStarted: root.streamReady = true
    onExited: root.streamReady = false
  }

  Process {
    id: cliResolver
    command: ["sh", "-c",
      "for p in \"$1\" \"$2\" \"$3\"; do [ -n \"$p\" ] && [ -x \"$p\" ] && { printf %s \"$p\"; exit 0; }; done; exit 1",
      "sh",
      Quickshell.env("HOME") + "/.local/bin/alienfx-ctl",
      root.bundledCli,
      "/usr/local/bin/alienfx-ctl"
    ]
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root.onCliResolved(text) }
    onExited: function (code) { if (code !== 0) root.onCliResolved("") }
  }

  Process {
    id: stateProc
    command: [root.cliPath, "state", "--json"]
    stdout: StdioCollector { waitForEnd: true; onStreamFinished: root.ingest(text) }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: if (text && !root.loaded) root.errorText = String(text).trim()
    }
  }

  // Coalesces the refresh that follows any change, so a burst of slider
  // updates costs one state read rather than one per frame.
  Timer {
    id: refreshTimer
    interval: 400
    repeat: false
    // Re-reading mid-drag is wasted work, but giving up entirely would leave
    // `st` stale forever - so keep waiting rather than dropping the refresh.
    onTriggered: {
      if (root.editing()) restart()
      else root.refresh()
    }
  }

  // Profile loads re-apply all four zones, so give the CLI time before reading
  // the result back and adopting it into the controls.
  Timer {
    id: profileReload
    interval: 700
    repeat: false
    onTriggered: { root.localEditAt = 0; root.refresh() }
  }

  // Fires once the user has been quiet. It re-reads state rather than adopting
  // the snapshot it already has: during a drag the refresh is deliberately
  // suppressed, so `st` is stale by definition here, and adopting it would snap
  // every control back to its pre-drag value. ingest() does the adopting once
  // the fresh read lands, since editing() has expired by then.
  Timer {
    id: settleTimer
    interval: root.settleMs + 300
    repeat: false
    onTriggered: {
      root.refresh()
      // Also make the change durable. Interactive applies go out --fast, which
      // writes the power button's colour but not its six NVRAM state blocks -
      // so it shows the new colour immediately and then reverts at the next
      // AC/battery/sleep transition. Waiting for the popup to close was not
      // enough: a user who picks a colour and leaves the panel open loses it on
      // the next power change.
      //
      // Runs as its own process rather than down the stream, so the ~2s write
      // cannot hold up a drag the user resumes. The hardware lock serialises
      // them, and a drag frame landing during it drops - invisible, since the
      // next frame supersedes it.
      root.commitDurable()
    }
  }

  // Realtime-but-not-wasteful. A chassis write costs ~190ms (the controller's
  // ioctl blocks ~62ms per packet), so a faster cadence than this just makes
  // frames queue up and drop against the hardware lock. Release always applies
  // once more, so the value the user settles on is exact regardless.
  Timer {
    id: pickerDebounce
    interval: 160
    repeat: false
    onTriggered: root.applyPickedColor(true)
  }

  Timer {
    id: brightnessDebounce
    interval: 160
    repeat: false
    property int pending: 0
    onTriggered: root.setBrightness(pending, true)
  }

  IpcHandler {
    target: "mrgnc.alienfx"
    function open(): void { root.open() }
    function close(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): void { root.refresh() }
  }

  onOpenedChanged: {
    if (!opened) {
      // Closing is the settle point. Stop the stream first so the durable
      // write is not queued behind it, then make the last state permanent -
      // this is the only place that pays the power button's ~2s NVRAM cost.
      settleTimer.stop()
      root.stopStream()
      if (root.loaded) root.commitDurable()
    }
    if (opened) {
      // Re-resolve on open: setup may have completed since the last look.
      resolveCli()
      cursorActive = false
      cursorIndex = Math.max(0, Model.EFFECT_ORDER.indexOf(root.uiEffect))
      saveOpen = false
      startStream()
    }
  }

  Component.onCompleted: resolveCli()

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // ------------------------------------------------------------- bar button
  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    // A glyph verified present in the Nerd Font Omarchy ships, with a real
    // icon as fallback so this slot can never render blank.
    text: root.iconGlyph !== "" ? root.iconGlyph : Model.ICON.alienAlt
    tooltipText: root.loaded
      ? ("AlienFX - " + (root.themesync ? "ThemeSync" : Model.EFFECT_LABELS[root.effect] || root.effect))
      : "AlienFX"
    onPressed: function (b) { root.toggle() }
  }

  // ------------------------------------------------------------------ popup
  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(430))
    contentHeight: panel.fittedContentHeight(column.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function (direction) { root.switchPanel(direction) }
      onMoveRequested: function (dx, dy) {
        // Vertical steps brightness, horizontal walks the effect row - the two
        // things worth reaching without the mouse.
        if (dy !== 0) {
          root.touch()
          root.setBrightness(Model.clamp255(root.uiBrightness - dy * 8), false)
          return
        }
        if (dx !== 0) root.moveCursor(dx)
      }
      onActivateRequested: root.activateCursor()

      Column {
        id: column
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        spacing: Style.space(13)

        // -------------------------------------------------- hero
        Item {
          width: parent.width
          implicitHeight: Math.max(heroIcon.implicitHeight, heroText.implicitHeight)

          Text {
            id: heroIcon
            text: root.iconGlyph !== "" ? root.iconGlyph : Model.ICON.alienAlt
            color: root.fg
            font.family: root.fontFamily
            font.pixelSize: Style.font.display
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            Behavior on color { ColorAnimation { duration: 200 } }
          }

          Column {
            id: heroText
            anchors.left: heroIcon.right
            anchors.leftMargin: Style.space(13)
            anchors.right: wizardIcon.left
            anchors.rightMargin: Style.space(8)
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(2)

            Text {
              text: "AlienFX"
              color: root.fg
              font.family: root.fontFamily
              font.pixelSize: Style.font.title
              font.bold: true
              elide: Text.ElideRight
              width: parent.width
            }

            Text {
              id: heroStatus
              text: {
                if (root.setupNeeded) return root.cliResolved ? "NO DEVICE ACCESS" : "SETUP REQUIRED"
                if (root.errorText !== "") return "ERROR"
                if (!root.loaded) return "READING..."
                if (root.themesync) return "THEMESYNC - " + (root.themeName || "theme").toUpperCase()
                return (Model.EFFECT_LABELS[root.effect] || root.effect).toUpperCase()
                  + (root.zonesync ? " - ALL ZONES" : " - " + (Model.ZONE_LABELS[root.selectedZone] || "").toUpperCase())
              }
              color: Qt.darker(root.fg, 1.4)
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 1.1
              elide: Text.ElideRight
              width: parent.width
            }
          }

          // Always reachable, not just on first run: re-running the wizard is
          // how you repair or replace a keymap, and hiding that behind "only
          // when none exists" made it a one-shot.
          PanelActionButton {
            id: wizardIcon
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            iconText: Model.ICON.keyboard
            tooltipText: "KeyMap Wizard"
            foreground: root.fg
            fontFamily: root.fontFamily
            onClicked: root.runWizard()
          }
        }

        // -------------------------------------------------- setup required
        // Reached when the plugin was added straight from git: the QML is here
        // but the CLI, udev rule and restore unit are not. Rather than just
        // reporting that, offer to finish the job.
        Column {
          visible: root.setupNeeded
          width: parent.width
          spacing: Style.space(9)

          Text {
            width: parent.width
            text: "Setup needs finishing"
            color: root.fg
            font.family: root.fontFamily
            font.pixelSize: Style.font.subtitle
            font.bold: true
          }

          Text {
            width: parent.width
            text: root.cliResolved
              ? "The lighting controllers are not writable yet - the udev rule that grants "
                + "access has not been installed. This opens a terminal and installs it; "
                + "it needs your password once."
              : "The lighting controller needs a udev rule and a small CLI, which live "
                + "outside the plugin folder. This opens a terminal and installs them - "
                + "it will check dependencies first and ask before changing anything."
            color: Qt.darker(root.fg, 1.3)
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
            wrapMode: Text.WordWrap
          }

          Button {
            width: parent.width
            iconText: Model.ICON.check
            iconSize: Style.font.title
            text: "Complete setup"
            fontSize: Style.font.bodySmall
            foreground: root.fg
            accent: Color.accent
            fontFamily: root.fontFamily
            bordered: true
            leftAlign: true
            verticalPadding: Style.spacing.controlPaddingY + Style.space(2)
            onClicked: root.runSetup()
          }
        }

        // -------------------------------------------------- other errors
        Text {
          visible: !root.setupNeeded && root.errorText !== ""
          width: parent.width
          text: root.errorText
          color: Color.urgent
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          wrapMode: Text.WordWrap
        }

        // -------------------------------------------------- KeyMap Wizard
        // Shown only while no user keymap exists; it self-hides once one is
        // saved, so it is a first-run affordance rather than permanent chrome.
        Button {
          visible: root.loaded && !root.hasKeymap
          width: parent.width
          iconText: Model.ICON.keyboard
          iconSize: Style.font.title
          text: "KeyMap Wizard"
          tooltipText: "Import or build the per-key map this laptop needs"
          fontSize: Style.font.bodySmall
          foreground: root.fg
          accent: Color.accent
          fontFamily: root.fontFamily
          bordered: true
          leftAlign: true
          verticalPadding: Style.spacing.controlPaddingY + Style.space(2)
          onClicked: root.runWizard()
        }

        PanelSeparator { foreground: root.fg; visible: root.loaded }

        // -------------------------------------------------- ThemeSync
        Toggle {
          visible: root.loaded
          width: parent.width
          label: Model.ICON.palette + "  ThemeSync"
          description: "Blend a diagonal gradient from the active Omarchy theme across every zone."
          checked: root.uiThemesync
          foreground: root.fg
          accent: Color.accent
          fontFamily: root.fontFamily
          onClicked: {
            root.uiThemesync = !root.uiThemesync
            if (root.uiThemesync) root.uiEffect = "gradient"
            root.touch()
            root.run(["themesync", root.uiThemesync ? "on" : "off"])
          }
        }

        // -------------------------------------------------- brightness
        Column {
          visible: root.loaded
          width: parent.width
          spacing: Style.space(7)

          Item {
            width: parent.width
            implicitHeight: brightnessLabel.implicitHeight

            PanelSectionHeader {
              id: brightnessLabel
              text: "BRIGHTNESS"
              foreground: root.fg
              fontFamily: root.fontFamily
              anchors.left: parent.left
            }

            Text {
              text: Model.brightnessPercent(brightnessSlider.liveValue) + "%"
              color: Qt.darker(root.fg, 1.3)
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              anchors.right: parent.right
              anchors.baseline: brightnessLabel.baseline
            }
          }

          PanelSlider {
            id: brightnessSlider
            width: parent.width
            bar: root.bar
            minimum: 0
            maximum: 255
            step: 1
            integer: true
            // Bound to locally-owned state: PanelSlider resets liveValue to
            // `value` on release, so `value` has to already hold the new
            // number or the knob visibly jumps back.
            value: root.uiBrightness
            onMoved: function (v) {
              root.uiBrightness = Math.round(v)
              root.touch()
              brightnessDebounce.pending = Math.round(v)
              brightnessDebounce.restart()
            }
            onReleased: function (v) {
              brightnessDebounce.stop()
              root.touch()
              root.setBrightness(v, false)
            }
          }
        }

        // ============ everything below is hidden while ThemeSync is on ======
        // ThemeSync drives all four zones from the theme, so exposing colour,
        // zone and effect controls would only offer changes it would overwrite.

        PanelSeparator { foreground: root.fg; visible: root.loaded && !root.themesync }

        Toggle {
          visible: root.loaded && !root.themesync
          width: parent.width
          label: (root.zonesync ? Model.ICON.link : Model.ICON.unlink) + "  ZoneSync"
          description: "Change every zone together. Turn off to control each zone on its own."
          checked: root.uiZonesync
          foreground: root.fg
          accent: Color.accent
          fontFamily: root.fontFamily
          onClicked: {
            root.uiZonesync = !root.uiZonesync
            root.touch()
            root.run(["zonesync", root.uiZonesync ? "on" : "off"])
          }
        }

        // -------------------------------------------------- zone selector
        Column {
          visible: root.loaded && !root.themesync && !root.zonesync
          width: parent.width
          spacing: Style.space(7)

          PanelSectionHeader {
            text: "ZONE"
            foreground: root.fg
            fontFamily: root.fontFamily
          }

          ChipRow {
            width: parent.width
            options: Model.zoneOptions(false)
            current: root.uiZone
            showDots: true
            onPicked: function (value) {
              root.uiZone = value
              root.loadPickerFromZone(value)
              root.touch()
              root.run(["set", "--select", value, "--fast"])
            }
          }
        }

        // -------------------------------------------------- colour
        Column {
          visible: root.loaded && !root.themesync
          width: parent.width
          spacing: Style.space(7)

          PanelSectionHeader {
            text: {
              var scope = root.zonesync ? "ALL ZONES"
                        : (Model.ZONE_LABELS[root.uiZone] || "").toUpperCase()
              return (root.rangeUsable ? "COLOUR RANGE - " : "COLOUR - ") + scope
            }
            foreground: root.fg
            fontFamily: root.fontFamily
          }

          // Both ends of the range on one row. Clicking a swatch points the
          // sliders at that end; Solid hides the far end entirely, since a flat
          // colour has no second end to set.
          Row {
            width: parent.width
            spacing: Style.space(8)

            SwatchButton {
              id: swatchA
              label: root.rangeUsable ? "FROM" : "COLOUR"
              hex: root.uiColorA
              shade: root.previewA
              active: root.pickTarget === "a"
              onPicked: root.editEnd("a")
            }

            SwatchButton {
              id: swatchB
              visible: root.rangeUsable
              label: "TO"
              hex: root.uiColorB !== "" ? root.uiColorB : Model.complementHex(root.uiColorA)
              shade: root.previewB
              active: root.pickTarget === "b"
              derived: root.uiColorB === ""
              onPicked: root.editEnd("b")
            }

            Button {
              anchors.verticalCenter: parent.verticalCenter
              text: root.pickerOpen ? "Done" : "Pick"
              fontSize: Style.font.bodySmall
              foreground: root.fg
              accent: Color.accent
              fontFamily: root.fontFamily
              bordered: true
              onClicked: root.pickerOpen = !root.pickerOpen
            }
          }

          Text {
            visible: root.rangeUsable && root.uiColorB === ""
            width: parent.width
            text: "The far end is derived from the first colour until you set it."
            color: Qt.darker(root.fg, 1.45)
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            wrapMode: Text.WordWrap
          }

          Column {
            visible: root.pickerOpen
            width: parent.width
            spacing: Style.space(5)

            ChannelSlider {
              id: redSlider
              width: parent.width
              label: "R"
              channel: root.pickR
              onChannelMoved: function (v) { root.pickR = v; root.touch(); pickerDebounce.restart() }
              onChannelReleased: function (v) {
                root.pickR = v; root.touch()
                pickerDebounce.stop(); root.applyPickedColor(false)
              }
            }
            ChannelSlider {
              id: greenSlider
              width: parent.width
              label: "G"
              channel: root.pickG
              onChannelMoved: function (v) { root.pickG = v; root.touch(); pickerDebounce.restart() }
              onChannelReleased: function (v) {
                root.pickG = v; root.touch()
                pickerDebounce.stop(); root.applyPickedColor(false)
              }
            }
            ChannelSlider {
              id: blueSlider
              width: parent.width
              label: "B"
              channel: root.pickB
              onChannelMoved: function (v) { root.pickB = v; root.touch(); pickerDebounce.restart() }
              onChannelReleased: function (v) {
                root.pickB = v; root.touch()
                pickerDebounce.stop(); root.applyPickedColor(false)
              }
            }
          }
        }

        // -------------------------------------------------- effects
        Column {
          visible: root.loaded && !root.themesync
          width: parent.width
          spacing: Style.space(7)

          PanelSectionHeader {
            text: Model.ICON.effects + "  EFFECT"
            foreground: root.fg
            fontFamily: root.fontFamily
          }

          ChipRow {
            width: parent.width
            options: Model.effectOptions()
            current: root.uiEffect
            cursorIndex: root.cursorActive ? root.cursorIndex : -1
            onPicked: function (value) { root.applyEffect(value) }
          }
        }

        // -------------------------------------------------- profiles
        PanelSeparator { foreground: root.fg; visible: root.loaded }

        Column {
          visible: root.loaded
          width: parent.width
          spacing: Style.space(7)

          PanelSectionHeader {
            text: Model.ICON.profile + "  PROFILE"
              + (root.currentProfile !== "" ? "  -  " + root.currentProfile.toUpperCase() : "")
            foreground: root.fg
            fontFamily: root.fontFamily
          }

          Row {
            width: parent.width
            spacing: Style.space(6)

            Dropdown {
              id: profileDropdown
              width: parent.width - loadButton.width - saveButton.width - parent.spacing * 2
              showLabel: false
              options: Model.profileOptions(root.profileNames)
              // Caller-owned, like every other control here. Dropdown assigns
              // its own `value` imperatively on selection, which would destroy
              // a binding to reported state outright - so the selection lives
              // in uiProfile and is handed back through onChanged. This is the
              // pattern Omarchy's own component gallery documents.
              value: root.uiProfile
              onChanged: function (v) { root.uiProfile = v }
              foreground: Color.popups.text
              background: Color.popups.background
              popupBorder: Color.popups.border
              accent: Color.accent
              fontFamily: root.fontFamily
            }

            Button {
              id: loadButton
              text: "Load"
              fontSize: Style.font.bodySmall
              foreground: root.fg
              accent: Color.accent
              fontFamily: root.fontFamily
              bordered: true
              onClicked: {
                if (root.uiProfile === "") return
                // A profile load is meant to overwrite the controls, so drop the
                // settling window and adopt whatever comes back.
                root.localEditAt = 0
                settleTimer.stop()
                Quickshell.execDetached([root.cliPath, "profile", "load", root.uiProfile])
                profileReload.restart()
              }
            }

            Button {
              id: saveButton
              text: "Save"
              iconText: Model.ICON.save
              iconSize: Style.font.body
              fontSize: Style.font.bodySmall
              foreground: root.fg
              accent: Color.accent
              fontFamily: root.fontFamily
              bordered: true
              onClicked: {
                root.saveOpen = !root.saveOpen
                if (root.saveOpen) {
                  saveField.text = root.uiProfile !== "" ? root.uiProfile : "Default"
                  saveField.forceActiveFocus()
                  saveField.selectAll()
                }
              }
            }
          }

          // Same field overwrites the loaded profile or, renamed, creates a
          // new one - which is exactly how the requirement describes Save.
          Row {
            visible: root.saveOpen
            width: parent.width
            spacing: Style.space(6)

            TextField {
              id: saveField
              width: parent.width - confirmSave.width - parent.spacing
              foreground: root.fg
              accent: Color.accent
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
              placeholderText: "profile name"
              onAccepted: confirmSave.clicked()
            }

            Button {
              id: confirmSave
              text: "OK"
              fontSize: Style.font.bodySmall
              foreground: root.fg
              accent: Color.accent
              fontFamily: root.fontFamily
              bordered: true
              onClicked: {
                var name = saveField.text.trim()
                if (name === "") return
                root.run(["profile", "save", name])
                root.saveOpen = false
              }
            }
          }
        }
      }
    }
  }

  // ------------------------------------------------------------- components

  // A wrapping row of selectable chips. Built from Omarchy's own Button so all
  // state chrome (selected / hover / focus) comes from the shared Style
  // tokens; a Flow rather than a Row so long labels wrap instead of
  // overflowing the popup.
  component ChipRow: Flow {
    id: chipRow
    property var options: []
    property string current: ""
    property bool showDots: false
    property int cursorIndex: -1
    signal picked(string value)

    spacing: Style.space(6)

    Repeater {
      model: chipRow.options

      delegate: Button {
        required property var modelData
        required property int index
        hasCursor: chipRow.cursorIndex === index
        text: String(modelData.label)
        // The dot previews the colour that zone is actually showing.
        iconText: chipRow.showDots ? "•" : ""
        iconSize: Style.font.title
        selected: String(modelData.value) === chipRow.current
        tooltipText: modelData.tooltip ? String(modelData.tooltip) : ""
        bordered: true
        foreground: root.fg
        accent: Color.accent
        fontFamily: root.fontFamily
        fontSize: Style.font.bodySmall
        horizontalPadding: Math.round(Style.spacing.controlPaddingX * 0.75)
        onClicked: chipRow.picked(String(modelData.value))
      }
    }
  }

  // One end of the colour range: a swatch showing the actual light colour with
  // its hex beneath. The fill is content, not chrome - it depicts what is being
  // sent to the hardware - so it is the one literal colour in the panel.
  component SwatchButton: Item {
    id: swatch
    property string label: ""
    property string hex: "000000"
    property color shade: "black"
    property bool active: false
    property bool derived: false
    signal picked()

    implicitWidth: Style.space(92)
    implicitHeight: column.implicitHeight
    anchors.verticalCenter: parent ? parent.verticalCenter : undefined

    Column {
      id: column
      width: parent.width
      spacing: Style.space(3)

      Text {
        text: swatch.label
        color: Qt.darker(root.fg, swatch.active ? 1.0 : 1.5)
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        font.bold: swatch.active
        font.letterSpacing: 1.0
      }

      Rectangle {
        width: parent.width
        height: Style.spacing.controlHeight
        radius: Style.cornerRadius > 0 ? Style.cornerRadius : 3
        color: swatch.shade
        opacity: swatch.derived ? 0.55 : 1.0
        border.width: swatch.active ? Math.max(2, Style.selectedBorderWidth + 1)
                                    : Math.max(1, Style.normalBorderWidth)
        border.color: swatch.active ? Color.accent : Style.normalBorderColor
        Behavior on color { ColorAnimation { duration: 140 } }
      }

      Text {
        text: "#" + swatch.hex
        color: Qt.darker(root.fg, 1.3)
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
      }
    }

    MouseArea {
      anchors.fill: parent
      cursorShape: Qt.PointingHandCursor
      onClicked: swatch.picked()
    }
  }

  // One labelled 0-255 channel slider.
  component ChannelSlider: Item {
    id: channelRow
    property string label: ""
    property int channel: 0
    // Surfaced so the panel can refuse to reconcile a control mid-drag.
    readonly property bool dragging: slider.dragging
    signal channelMoved(int value)
    signal channelReleased(int value)

    implicitHeight: Math.max(channelLabel.implicitHeight, slider.implicitHeight)

    Text {
      id: channelLabel
      text: channelRow.label
      color: Qt.darker(root.fg, 1.3)
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      width: Style.space(14)
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
    }

    PanelSlider {
      id: slider
      anchors.left: channelLabel.right
      anchors.leftMargin: Style.space(6)
      anchors.right: valueLabel.left
      anchors.rightMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
      bar: root.bar
      minimum: 0
      maximum: 255
      step: 1
      integer: true
      value: channelRow.channel
      onMoved: function (v) { channelRow.channelMoved(Math.round(v)) }
      onReleased: function (v) { channelRow.channelReleased(Math.round(v)) }
    }

    Text {
      id: valueLabel
      text: String(Math.round(slider.liveValue))
      color: Qt.darker(root.fg, 1.3)
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      width: Style.space(26)
      horizontalAlignment: Text.AlignRight
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
    }
  }
}
