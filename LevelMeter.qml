import QtQuick
import qs.Commons

Column {
  id: root

  property string label: ""
  property real level: 0
  property bool muted: false
  property bool paused: false
  property color foreground: Color.foreground
  property color accent: Color.accent
  property color urgent: Color.urgent
  property string fontFamily: Style.font.family

  width: parent ? parent.width : implicitWidth
  spacing: Style.space(5)

  Row {
    width: parent.width

    Text {
      textFormat: Text.PlainText
      text: root.label
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.body
      font.bold: true
    }

    Item {
      width: Math.max(0, parent.width - parent.children[0].implicitWidth - status.implicitWidth)
      height: 1
    }

    Text {
      id: status
      textFormat: Text.PlainText
      text: root.paused ? "PAUSED" : (root.muted ? "MUTED" : Math.round(root.level * 100) + "%")
      color: root.muted || root.paused ? Qt.darker(root.foreground, 1.45) : root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      font.bold: true
      font.letterSpacing: 0.8
    }
  }

  Rectangle {
    width: parent.width
    height: Style.space(8)
    radius: height / 2
    color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.12)

    Rectangle {
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      height: parent.height
      radius: parent.radius
      width: root.paused ? 0 : Math.max(height, parent.width * Math.max(0, Math.min(1, root.level)))
      opacity: root.muted ? 0.35 : 1
      color: root.level > 0.92 ? root.urgent : root.foreground

      Behavior on width { NumberAnimation { duration: 90; easing.type: Easing.OutQuad } }
      Behavior on opacity { NumberAnimation { duration: 120 } }
      Behavior on color { ColorAnimation { duration: 100 } }
    }
  }
}
