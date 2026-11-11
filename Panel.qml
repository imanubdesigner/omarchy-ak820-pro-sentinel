import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "Model.js" as Model

// Bar shield plus status panel for AK820 Pro Sentinel.
//
// The panel is a pure reader: it opens the JSON snapshot written by the
// service and never talks to the receiver. Every color comes from the active
// Omarchy theme through Commons.Color, so the widget follows theme switches
// without any hard coded hue.
Panel {
  id: root
  moduleName: "omarchy-ak820-pro-sentinel"
  ipcTarget: "omarchy-ak820-pro-sentinel"

  property var status: null
  property bool showLog: false
  // The setup card only appears after a short grace period, so a service that
  // is starting up does not flash "not installed".
  property bool setupReady: false
  property bool unitInstalled: false
  property bool setupScriptPresent: false
  property bool setupRunning: false
  // The widget marks a snapshot as stale when a service that has not stopped
  // cleanly stops refreshing the file. Five seconds covers two status ticks.
  property double nowMs: Date.now()
  readonly property int staleSeconds: 5
  readonly property string statusPath: "/run/ak820-pro-sentinel/status.json"
  readonly property string homeDir: Quickshell.env("HOME") || ""
  readonly property string omarchyBin: (Quickshell.env("OMARCHY_PATH") || "/usr/share/omarchy") + "/bin"
  readonly property string pluginDir: homeDir + "/.config/omarchy/plugins/omarchy-ak820-pro-sentinel"
  readonly property string setupScript: pluginDir + "/install/install.sh"
  readonly property string unitPath: "/etc/systemd/system/ak820-pro-sentinel.service"
  readonly property bool needsSetup: status === null && setupReady
  readonly property string setupTitle: unitInstalled ? "Service not running" : "Service not installed"
  readonly property string setupBody: unitInstalled
    ? "The background service is installed but has not written a status file yet."
    : "The bar widget cannot keep the receiver awake on its own. Install the background service to start the watchdog."
  readonly property string setupHint: unitInstalled
    ? "Starting the service asks for your password once, in a terminal."
    : "The installer asks for your password once, in a terminal."
  readonly property string setupCommand: unitInstalled
    ? "sudo systemctl restart ak820-pro-sentinel.service"
    : (setupScriptPresent ? "sudo \"" + setupScript + "\"" : "")
  readonly property string stateKey: Model.isStale(status, nowMs, staleSeconds)
    ? "stale"
    : Model.stateOf(status)
  readonly property color accentColor: Commons.Color.accent
  readonly property color foregroundColor: Commons.Color.foreground
  readonly property color mutedColor: Commons.Color.muted
  readonly property color urgentColor: Commons.Color.urgent
  readonly property color stateColor: Model.stateColor(
    stateKey, accentColor, foregroundColor, mutedColor, urgentColor
  )
  readonly property var rows: Model.rows(status, stateKey)
  readonly property var logRows: Model.logRows(status, 20)
  readonly property var session: Model.sessionStats(status)
  readonly property string fontFamily: root.bar ? root.bar.fontFamily : Style.font.family

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // Reopening the panel always starts from the status view.
  onOpenedChanged: if (!opened) showLog = false

  onStatusChanged: root.refreshSetupTimer()

  Component.onCompleted: root.refreshSetupTimer()

  function refreshSetupTimer() {
    if (root.status !== null) {
      setupTimer.stop()
      root.setupReady = false
      return
    }
    if (!setupTimer.running) setupTimer.restart()
  }

  function runSetup() {
    if (root.setupRunning || root.setupCommand === "") return
    root.setupRunning = true
    setupLauncher.command = [
      root.omarchyBin + "/omarchy", "launch", "floating", "terminal",
      "with", "presentation", root.setupCommand
    ]
    setupLauncher.running = true
  }

  function toneColor(tone) {
    if (tone === "accent") return root.accentColor
    if (tone === "urgent") return root.urgentColor
    if (tone === "muted") return root.mutedColor
    return root.foregroundColor
  }

  FileView {
    id: statusFile
    path: root.statusPath
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: root.status = Model.parse(text())
    onLoadFailed: root.status = null
  }

  // The setup card needs to know whether the service is merely stopped or was
  // never installed, and whether this checkout still carries the installer.
  FileView {
    id: unitFile
    path: root.unitPath
    watchChanges: true
    printErrors: false
    onLoaded: root.unitInstalled = true
    onLoadFailed: root.unitInstalled = false
  }

  FileView {
    id: setupFile
    path: root.setupScript
    watchChanges: true
    printErrors: false
    onLoaded: root.setupScriptPresent = true
    onLoadFailed: root.setupScriptPresent = false
  }

  Timer {
    id: setupTimer
    interval: 3000
    repeat: false
    onTriggered: root.setupReady = root.status === null
  }

  // The installer runs in a terminal, the way every Omarchy plugin asks for
  // root: the command is visible, sudo prompts there, and the shell never
  // holds the password.
  Process {
    id: setupLauncher
    environment: ({ PATH: root.omarchyBin + ":/usr/bin" })
    onExited: function(code) {
      if (code === 127) {
        fallbackLauncher.command = [
          "/usr/bin/xdg-terminal-exec", "bash", "-c", root.setupCommand
        ]
        fallbackLauncher.running = true
        return
      }
      root.setupRunning = false
      statusFile.reload()
    }
  }

  Process {
    id: fallbackLauncher
    onExited: function() {
      root.setupRunning = false
      statusFile.reload()
    }
  }

  // Drive the stale check without touching the file. The status file is
  // refreshed every second while the service runs, so a single second here is
  // enough to notice that updates have stopped.
  Timer {
    interval: 1000
    running: true
    repeat: true
    onTriggered: root.nowMs = Date.now()
  }

  // The file is created when the service starts, so a watch alone is not
  // enough: poll slowly as a fallback until it appears.
  Timer {
    interval: 2000
    running: true
    repeat: true
    onTriggered: statusFile.reload()
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󰒃"
    tooltipText: Model.statusSentence(root.stateKey)
    foreground: root.stateColor
    onPressed: function(_mouse) { root.toggle() }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened

    contentWidth: panel.fittedContentWidth(Style.space(380))
    contentHeight: panel.fittedContentHeight(content.implicitHeight, Style.space(520))

    Column {
      id: content
      anchors.fill: parent
      spacing: Style.space(12)

      // ------------------------------------------------------------- header
      Item {
        width: parent.width
        implicitHeight: Math.max(
          heroIcon.implicitHeight,
          heroLabels.implicitHeight,
          viewButton.implicitHeight
        )

        Text {
          id: heroIcon
          text: "󰒃"
          color: root.stateColor
          font.family: root.fontFamily
          font.pixelSize: Style.font.display
          anchors.left: parent.left
          anchors.verticalCenter: parent.verticalCenter
        }

        Button {
          id: viewButton
          width: Style.space(56)
          visible: !root.needsSetup
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          text: root.showLog ? "BACK" : "LOG"
          tooltipText: root.showLog ? "Back to status" : "Show event log"
          foreground: root.barForeground
          accent: root.accentColor
          bordered: true
          focusable: true
          fontSize: Style.font.caption
          horizontalPadding: Style.space(10)
          verticalPadding: Style.space(4)
          onClicked: root.showLog = !root.showLog
        }

        Column {
          id: heroLabels
          anchors.left: heroIcon.right
          anchors.leftMargin: Style.space(12)
          anchors.right: viewButton.left
          anchors.rightMargin: Style.space(10)
          anchors.verticalCenter: parent.verticalCenter
          spacing: Style.space(2)

          Text {
            textFormat: Text.PlainText
            text: "AK820 PRO SENTINEL"
            color: Qt.darker(root.barForeground, 1.4)
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            font.bold: true
            font.letterSpacing: 1.4
            elide: Text.ElideRight
            width: parent.width
          }

          Text {
            textFormat: Text.PlainText
            text: root.needsSetup
              ? root.setupTitle
              : (root.showLog ? "Event log" : Model.statusSentence(root.stateKey))
            color: root.needsSetup
              ? root.foregroundColor
              : (root.showLog ? root.barForeground : root.stateColor)
            font.family: root.fontFamily
            font.pixelSize: Style.font.heading
            font.bold: true
            font.letterSpacing: 0.2
            elide: Text.ElideRight
            width: parent.width
          }
        }
      }

      PanelSeparator {
        width: parent.width
        foreground: root.barForeground
      }

      // ------------------------------------------------------------- setup
      // Shown only when no status file exists: the widget is installed and
      // the service is not, or it is installed but stopped. The button opens
      // the installer in a terminal, which is the only place sudo can ask.
      Column {
        id: setupView
        visible: root.needsSetup
        width: parent.width
        spacing: Style.space(10)

        Text {
          textFormat: Text.PlainText
          text: root.setupBody
          color: root.barForeground
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          wrapMode: Text.WordWrap
          width: parent.width
        }

        Button {
          width: parent.width
          visible: root.setupCommand !== ""
          enabled: !root.setupRunning
          text: root.setupRunning
            ? "WAITING FOR THE TERMINAL"
            : (root.unitInstalled ? "START IN A TERMINAL" : "INSTALL IN A TERMINAL")
          tooltipText: root.setupCommand
          foreground: root.barForeground
          accent: root.accentColor
          bordered: true
          focusable: true
          fontSize: Style.font.caption
          horizontalPadding: Style.space(12)
          verticalPadding: Style.space(6)
          onClicked: root.runSetup()
        }

        Text {
          textFormat: Text.PlainText
          text: root.setupHint
          color: Qt.darker(root.barForeground, 1.5)
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          wrapMode: Text.WordWrap
          width: parent.width
        }

        Text {
          textFormat: Text.PlainText
          visible: root.setupCommand !== ""
          text: "$ " + root.setupCommand
          color: Qt.darker(root.barForeground, 1.6)
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          wrapMode: Text.WrapAnywhere
          width: parent.width
        }
      }

      // ------------------------------------------------------------ status
      Column {
        id: mainView
        visible: !root.showLog && !root.needsSetup
        width: parent.width
        spacing: Style.space(12)

        Repeater {
          model: root.rows

          Item {
            required property var modelData

            width: mainView.width
            implicitHeight: Math.max(rowLabel.implicitHeight, rowValue.implicitHeight)

            Text {
              id: rowLabel
              textFormat: Text.PlainText
              text: modelData.label
              color: Qt.darker(root.barForeground, 1.4)
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 1.1
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
            }

            Column {
              id: rowValue
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              width: parent.width * 0.62
              spacing: Style.space(1)

              Text {
                textFormat: Text.PlainText
                text: modelData.value
                color: modelData.tone ? root.stateColor : root.barForeground
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                horizontalAlignment: Text.AlignRight
                elide: Text.ElideRight
                width: parent.width
              }

              Text {
                textFormat: Text.PlainText
                text: modelData.detail || ""
                visible: text.length > 0
                color: Qt.darker(root.barForeground, 1.5)
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                horizontalAlignment: Text.AlignRight
                elide: Text.ElideRight
                width: parent.width
              }
            }
          }
        }

        PanelSeparator {
          width: parent.width
          foreground: root.barForeground
        }

        Text {
          textFormat: Text.PlainText
          text: "Radio link not verified. Sentinel monitors the USB receiver only."
          color: Qt.darker(root.barForeground, 1.5)
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          wrapMode: Text.WordWrap
          width: parent.width
        }
      }

      // --------------------------------------------------------------- log
      Column {
        id: logView
        visible: root.showLog && !root.needsSetup
        width: parent.width
        spacing: Style.space(8)

        // A fixed proof of the microdia fix: the interfaces held open right
        // now, how long the service has been up, and how many recoveries it
        // already performed. It never adds rows to the event list.
        Row {
          width: parent.width
          spacing: Style.space(6)

          Text {
            textFormat: Text.PlainText
            text: Model.sessionHoldLine(root.status)
            color: Qt.darker(root.barForeground, 1.4)
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            font.bold: true
            font.letterSpacing: 0.8
          }

          Text {
            textFormat: Text.PlainText
            text: Model.sessionRecoveryLine(root.status)
            color: root.session.recoveries > 0
              ? root.urgentColor
              : root.accentColor
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            font.bold: true
            font.letterSpacing: 0.8
          }
        }

        PanelSeparator {
          width: parent.width
          foreground: root.barForeground
        }

        ListView {
          id: logList
          width: parent.width
          height: Math.min(Style.space(360), Math.max(0, contentHeight))
          visible: root.logRows.length > 0
          spacing: Style.space(8)
          clip: true
          boundsBehavior: Flickable.StopAtBounds
          interactive: contentHeight > height
          model: root.logRows
          ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

          delegate: Item {
            id: logEntry
            required property var modelData
            width: logList.width
            implicitHeight: Math.max(logTime.implicitHeight, logText.implicitHeight, logChip.implicitHeight)

            Text {
              id: logTime
              anchors.left: parent.left
              anchors.top: parent.top
              text: Model.formatEventTime(logEntry.modelData.timestamp, root.nowMs)
              color: Qt.darker(root.barForeground, 1.5)
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              width: Style.space(56)
            }

            Column {
              id: logText
              anchors.left: logTime.right
              anchors.leftMargin: Style.space(8)
              anchors.right: logChip.left
              anchors.rightMargin: Style.space(8)
              anchors.top: parent.top
              spacing: Style.space(2)

              Text {
                textFormat: Text.PlainText
                text: logEntry.modelData.title
                color: root.toneColor(logEntry.modelData.tone)
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                elide: Text.ElideRight
                width: parent.width
              }

              Text {
                textFormat: Text.PlainText
                text: logEntry.modelData.detail || ""
                visible: text.length > 0
                color: Qt.darker(root.barForeground, 1.5)
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                elide: Text.ElideRight
                width: parent.width
              }
            }

            Text {
              id: logChip
              anchors.right: parent.right
              anchors.top: parent.top
              text: Model.logInterfaceLabel(logEntry.modelData)
              color: Qt.darker(root.barForeground, 1.4)
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: logEntry.modelData.count > 1
            }
          }
        }

        Text {
          textFormat: Text.PlainText
          visible: root.logRows.length === 0
          text: "No events yet."
          color: Qt.darker(root.barForeground, 1.5)
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
        }

        PanelSeparator {
          width: parent.width
          foreground: root.barForeground
        }

        Text {
          textFormat: Text.PlainText
          text: "Significant events only. Cleared when the service restarts."
          color: Qt.darker(root.barForeground, 1.5)
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          wrapMode: Text.WordWrap
          width: parent.width
        }
      }
    }
  }
}

