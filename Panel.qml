import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "illegalstudio.omarchy-call-recorder"
  manageIpc: false

  readonly property var service: bar && bar.shell ? bar.shell.serviceFor(moduleName) : null
  readonly property string configuredMicrophone: String(setting("microphone", "") || "")
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.45)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  Binding {
    target: root.service
    property: "preferredMicrophone"
    value: root.configuredMicrophone
    when: root.service !== null
  }

  function selectMicrophone(name) {
    var next = Object.assign({}, settings, { microphone: String(name || "") })
    settings = next
    if (bar && bar.shell) bar.shell.updateEntryInline(moduleName, next)
    if (service) service.setMicrophone(name)
  }

  function togglePause() {
    if (service) service.togglePause()
  }

  function toggleMicrophoneMute() {
    if (service) service.setMicrophoneMuted(!service.microphoneMuted)
  }

  function toggleDesktopMute() {
    if (service) service.setDesktopMuted(!service.desktopMuted)
  }

  onOpenedChanged: {
    if (opened && service) service.refreshDevices()
  }

  onVisibleChanged: {
    if (!visible && opened) close()
  }

  visible: service ? service.visibleInBar : false
  implicitWidth: visible ? button.implicitWidth : 0
  implicitHeight: visible ? button.implicitHeight : 0

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.service && root.service.paused ? "" : ""
    active: true
    tooltipText: root.service
      ? (root.service.paused ? "Call recording paused" : "Call recording")
        + " · " + root.service.formatElapsed(root.service.elapsedSeconds)
      : "Call recording"
    onPressed: root.toggle()
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened && root.visible
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(390))
    contentHeight: panel.fittedContentHeight(content.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(text) {
        if (text === "p" || text === "P") root.togglePause()
        else if (text === "m" || text === "M") root.toggleMicrophoneMute()
        else if (text === "d" || text === "D") root.toggleDesktopMute()
        else if ((text === "s" || text === "S") && root.service) root.service.stopRecording()
      }

      Column {
        id: content
        width: parent.width
        spacing: Style.space(13)

        Item {
          id: heroHost
          width: parent.width
          implicitHeight: hero.implicitHeight
          readonly property color iconColor: root.service && root.service.paused ? root.dim : root.urgent

          PanelHero {
            id: hero
            width: parent.width
            title: "Call recording"
            meta: {
              if (!root.service) return "Starting"
              if (root.service.stopping) return "Saving recording"
              if (root.service.paused) return "Paused"
              if (root.service.starting) return "Starting"
              return "Microphone + desktop audio"
            }
            detail: root.service ? root.service.formatElapsed(root.service.elapsedSeconds) : "00:00"
            foreground: root.foreground
            fontFamily: root.fontFamily
            iconComponent: Component {
              Text {
                textFormat: Text.PlainText
                text: root.service && root.service.paused ? "" : ""
                color: heroHost.iconColor
                font.family: root.fontFamily
                font.pixelSize: Style.font.display
                font.bold: true
              }
            }
          }
        }

        PanelSeparator { foreground: root.foreground }

        Dropdown {
          id: microphonePicker
          width: parent.width
          label: "Microphone"
          value: root.service ? root.service.currentMicrophone : ""
          options: root.service ? root.service.microphones : []
          foreground: root.foreground
          fontFamily: root.fontFamily
          onChanged: function(value) { root.selectMicrophone(value) }
        }

        Column {
          width: parent.width
          spacing: Style.space(3)

          Text {
            text: "DESKTOP AUDIO"
            textFormat: Text.PlainText
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            font.bold: true
          }

          Text {
            width: parent.width
            text: root.service ? root.service.currentDesktopLabel : "Default output"
            textFormat: Text.PlainText
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            elide: Text.ElideRight
          }
        }

        PanelSeparator { foreground: root.foreground }

        PanelSectionHeader {
          text: "INPUT LEVELS"
          foreground: root.foreground
          fontFamily: root.fontFamily
        }

        LevelMeter {
          label: "Microphone"
          level: root.service ? root.service.microphoneLevel : 0
          muted: root.service ? root.service.microphoneMuted : false
          paused: root.service ? root.service.paused : false
          foreground: root.foreground
          urgent: root.urgent
          fontFamily: root.fontFamily
        }

        LevelMeter {
          label: "Desktop audio"
          level: root.service ? root.service.desktopLevel : 0
          muted: root.service ? root.service.desktopMuted : false
          paused: root.service ? root.service.paused : false
          foreground: root.foreground
          urgent: root.urgent
          fontFamily: root.fontFamily
        }

        PanelSeparator { foreground: root.foreground }

        Row {
          width: parent.width
          spacing: Style.space(8)

          Button {
            width: (parent.width - parent.spacing) / 2
            text: root.service && root.service.paused ? "Resume" : "Pause"
            iconText: root.service && root.service.paused ? "" : ""
            bordered: true
            foreground: root.foreground
            fontFamily: root.fontFamily
            onClicked: root.togglePause()
          }

          Button {
            width: (parent.width - parent.spacing) / 2
            text: root.service && root.service.microphoneMuted ? "Unmute mic" : "Mute mic"
            iconText: root.service && root.service.microphoneMuted ? "" : ""
            selected: root.service ? root.service.microphoneMuted : false
            bordered: true
            foreground: root.foreground
            fontFamily: root.fontFamily
            onClicked: root.toggleMicrophoneMute()
          }
        }

        Row {
          width: parent.width
          spacing: Style.space(8)

          Button {
            width: (parent.width - parent.spacing) / 2
            text: root.service && root.service.desktopMuted ? "Unmute desktop" : "Mute desktop"
            iconText: root.service && root.service.desktopMuted ? "󰖁" : "󰕾"
            selected: root.service ? root.service.desktopMuted : false
            bordered: true
            foreground: root.foreground
            fontFamily: root.fontFamily
            onClicked: root.toggleDesktopMute()
          }

          Button {
            width: (parent.width - parent.spacing) / 2
            text: "Stop and save"
            iconText: ""
            bordered: true
            foreground: root.urgent
            fontFamily: root.fontFamily
            onClicked: if (root.service) root.service.stopRecording()
          }
        }

        Text {
          width: parent.width
          visible: root.service && root.service.outputPath !== ""
          text: root.service ? root.service.outputPath : ""
          textFormat: Text.PlainText
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          elide: Text.ElideMiddle
        }

        Text {
          width: parent.width
          visible: root.service && root.service.errorText !== ""
          text: root.service ? root.service.errorText : ""
          textFormat: Text.PlainText
          color: root.urgent
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          wrapMode: Text.WordWrap
        }

        Text {
          width: parent.width
          text: "p pause · m microphone · d desktop · s stop"
          textFormat: Text.PlainText
          color: root.dim
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
          horizontalAlignment: Text.AlignHCenter
        }
      }
    }
  }
}
