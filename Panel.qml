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
  property bool cursorActive: false

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
  property int uiBrightness: 26

  // Picker channels, also locally owned.
  property int pickR: 255
  property int pickG: 120
  property int pickB: 0

  // While the user is driving, an in-flight state read must not overwrite what
  // they just set. Any local edit marks a settling window; reconciliation waits
  // for quiet.
  property double localEditAt: 0
  readonly property int settleMs: 900
  function touch() { root.localEditAt = Date.now(); settleTimer.restart() }
  function editing() { return (Date.now() - root.localEditAt) < root.settleMs }

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
    root.uiThemesync = s.themesync === true
    root.uiZonesync = s.zonesync !== false
    root.uiEffect = s.effect ? String(s.effect) : "gradient"
    root.uiZone = s.selected_zone ? String(s.selected_zone) : "kbd"
    if (s.brightness !== undefined) root.uiBrightness = s.brightness
    loadPickerFromZone(root.uiZone)
  }

  function loadPickerFromZone(zone) {
    var rgb = Model.hexToRgb(Model.zoneHex(root.st, zone))
    root.pickR = rgb.r
    root.pickG = rgb.g
    root.pickB = rgb.b
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
    root.run(["set", "--zones", colorTargets(live), "--color", hex, "--fast"])
  }

  function setBrightness(value, live) {
    root.uiBrightness = Model.clamp255(value)
    root.run(["set", "--zones", live ? colorTargets(true) : "all",
              "--brightness", String(root.uiBrightness), "--fast"])
  }

  // Once the user has stopped, re-apply durably so the power button's colour
  // survives a power transition. This is the only path that pays the ~2s NVRAM
  // cost, and it never runs while anything is being dragged.
  function commitDurable() {
    if (!root.cliResolved) return
    Quickshell.execDetached([root.cliPath, "commit"])
    refreshTimer.restart()
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
    // Never reconcile mid-interaction; adoptFromState is gated on editing()
    // anyway, but re-reading during a drag is just wasted work.
    onTriggered: if (!root.editing()) root.refresh()
  }

  // Profile loads re-apply all four zones, so give the CLI time before reading
  // the result back and adopting it into the controls.
  Timer {
    id: profileReload
    interval: 700
    repeat: false
    onTriggered: { root.localEditAt = 0; root.refresh() }
  }

  // Fires once the user has been quiet. It only reconciles the controls with
  // what the CLI reports - the durable write happens when the popup closes, so
  // an idle user is never interrupted by a 2s NVRAM walk mid-session.
  Timer {
    id: settleTimer
    interval: root.settleMs + 300
    repeat: false
    onTriggered: root.adoptFromState()
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
            anchors.right: parent.right
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
          onClicked: {
            Quickshell.execDetached([root.wizardPath])
            root.close()
          }
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
            text: root.zonesync ? "COLOUR - ALL ZONES"
                                : "COLOUR - " + (Model.ZONE_LABELS[root.selectedZone] || "").toUpperCase()
            foreground: root.fg
            fontFamily: root.fontFamily
          }

          // Click the swatch to reveal the channel sliders, per the "picker
          // displays on element click" behaviour.
          Item {
            width: parent.width
            implicitHeight: Style.spacing.controlHeight

            Rectangle {
              id: swatch
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
              width: Style.space(46)
              height: Style.spacing.controlHeight
              radius: Style.cornerRadius > 0 ? Style.cornerRadius : 3
              // Content, not chrome: the actual colour on the hardware.
              color: root.previewColor
              border.width: Math.max(1, Style.normalBorderWidth)
              border.color: Style.normalBorderColor
              Behavior on color { ColorAnimation { duration: 140 } }
            }

            Text {
              anchors.left: swatch.right
              anchors.leftMargin: Style.space(11)
              anchors.verticalCenter: parent.verticalCenter
              text: "#" + Model.rgbToHex(root.pickR, root.pickG, root.pickB)
              color: root.fg
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
            }

            Button {
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              text: root.pickerOpen ? "Done" : "Pick"
              fontSize: Style.font.bodySmall
              foreground: root.fg
              accent: Color.accent
              fontFamily: root.fontFamily
              bordered: true
              onClicked: root.pickerOpen = !root.pickerOpen
            }

            MouseArea {
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
              width: swatch.width
              height: swatch.height
              cursorShape: Qt.PointingHandCursor
              onClicked: root.pickerOpen = !root.pickerOpen
            }
          }

          Column {
            visible: root.pickerOpen
            width: parent.width
            spacing: Style.space(5)

            ChannelSlider {
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
            onPicked: function (value) {
              root.uiEffect = value
              // A manual effect means the theme is no longer driving.
              if (value !== "gradient") root.uiThemesync = false
              root.touch()
              root.run(["set", "--effect", value, "--fast"])
            }
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
              value: root.currentProfile !== "" ? root.currentProfile
                   : (root.profileNames.length > 0 ? String(root.profileNames[0]) : "")
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
                if (profileDropdown.value === "") return
                // A profile load is meant to overwrite the controls, so drop the
                // settling window and adopt whatever comes back.
                root.localEditAt = 0
                settleTimer.stop()
                Quickshell.execDetached([root.cliPath, "profile", "load", profileDropdown.value])
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
                  saveField.text = root.currentProfile !== "" ? root.currentProfile : "Default"
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
    signal picked(string value)

    spacing: Style.space(6)

    Repeater {
      model: chipRow.options

      delegate: Button {
        required property var modelData
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

  // One labelled 0-255 channel slider.
  component ChannelSlider: Item {
    id: channelRow
    property string label: ""
    property int channel: 0
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
