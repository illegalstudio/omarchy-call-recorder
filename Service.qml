import QtQuick
import Quickshell
import Quickshell.Io

Item {
  id: root

  property var shell: null
  property var manifest: null

  readonly property string pluginId: "illegalstudio.omarchy-call-recorder"
  readonly property string backendPath: Qt.resolvedUrl("recorder.py").toString().replace(/^file:\/\//, "")

  property string preferredMicrophone: ""
  property var microphones: []
  property string defaultMicrophone: ""
  property string desktopSource: ""
  property string desktopLabel: "Default output"

  property bool starting: false
  property bool recording: false
  property bool paused: false
  property bool stopping: false
  property bool microphoneMuted: false
  property bool desktopMuted: false
  property real microphoneLevel: 0
  property real desktopLevel: 0
  property string currentMicrophone: ""
  property string currentMicrophoneLabel: "Default microphone"
  property string currentDesktop: ""
  property string currentDesktopLabel: "Default output"
  property string outputPath: ""
  property string lastSavedPath: ""
  property string errorText: ""

  property double clockNowMs: Date.now()
  property double recordedMs: 0
  property double segmentStartedMs: 0

  readonly property bool visibleInBar: starting || recording || stopping
  readonly property int elapsedSeconds: Math.max(0, Math.floor((recordedMs
    + (recording && !paused && !stopping && segmentStartedMs > 0
      ? clockNowMs - segmentStartedMs : 0)) / 1000))

  function boolValue(value) {
    var text = String(value || "").toLowerCase()
    return text === "1" || text === "true" || text === "yes" || text === "on"
  }

  function microphoneExists(name) {
    for (var i = 0; i < microphones.length; i++)
      if (String(microphones[i].value) === String(name)) return true
    return false
  }

  function microphoneLabel(name) {
    for (var i = 0; i < microphones.length; i++)
      if (String(microphones[i].value) === String(name)) return String(microphones[i].label)
    return String(name || "Default microphone")
  }

  function formatElapsed(seconds) {
    var value = Math.max(0, Number(seconds) || 0)
    var hours = Math.floor(value / 3600)
    var minutes = Math.floor((value % 3600) / 60)
    var secs = Math.floor(value % 60)
    function two(n) { return String(n).padStart(2, "0") }
    return hours > 0 ? hours + ":" + two(minutes) + ":" + two(secs) : two(minutes) + ":" + two(secs)
  }

  function refreshDevices() {
    if (!devicesProc.running) devicesProc.running = true
  }

  function loadDevices(raw) {
    try {
      var data = JSON.parse(raw || "{}")
      if (data.type === "error") {
        errorText = String(data.message || "Could not read audio devices")
        return
      }
      microphones = data.microphones || []
      defaultMicrophone = String(data.default_microphone || "")
      desktopSource = String(data.desktop_source || "")
      desktopLabel = String(data.desktop_label || "Default output")
    } catch (error) {
      errorText = "Could not read the audio device list"
    }
  }

  function openPanel() {
    if (shell) shell.summon(pluginId)
  }

  function startRecording() {
    if (starting || recording || stopping || recorderProc.running) {
      openPanel()
      return "already recording"
    }

    var selected = microphoneExists(preferredMicrophone) ? preferredMicrophone : defaultMicrophone
    var args = [backendPath, "record"]
    if (selected !== "") args.push("--microphone", selected)

    errorText = ""
    outputPath = ""
    microphoneLevel = 0
    desktopLevel = 0
    microphoneMuted = false
    desktopMuted = false
    recordedMs = 0
    segmentStartedMs = 0
    starting = true
    paused = false
    stopping = false
    recorderProc.command = args
    recorderProc.running = true
    return "starting"
  }

  function sendCommand(command, value) {
    if (!recorderProc.running) return false
    var payload = { command: command }
    if (value !== undefined) payload.value = value
    recorderProc.write(JSON.stringify(payload) + "\n")
    return true
  }

  function togglePause() {
    if (!recording || stopping) return
    sendCommand(paused ? "resume" : "pause")
  }

  function setMicrophoneMuted(muted) {
    if (!recording || stopping) return
    sendCommand("mute-microphone", muted)
  }

  function setDesktopMuted(muted) {
    if (!recording || stopping) return
    sendCommand("mute-desktop", muted)
  }

  function setMicrophone(name) {
    preferredMicrophone = String(name || "")
    if (recording && !stopping) sendCommand("microphone", preferredMicrophone)
  }

  function stopRecording() {
    if (!recorderProc.running || stopping) return
    stopping = true
    commitRunningTime()
    sendCommand("stop")
  }

  function commitRunningTime() {
    if (recording && !paused && segmentStartedMs > 0) {
      recordedMs += Date.now() - segmentStartedMs
      segmentStartedMs = 0
    }
  }

  function setBackendState(state) {
    if (state === "paused") {
      commitRunningTime()
      paused = true
      microphoneLevel = 0
      desktopLevel = 0
      return
    }
    if (state === "recording") {
      if (paused || segmentStartedMs <= 0) segmentStartedMs = Date.now()
      paused = false
      return
    }
    if (state === "stopping") {
      commitRunningTime()
      stopping = true
    }
  }

  function applyBackendLine(line) {
    var data
    try { data = JSON.parse(line) } catch (error) { return }

    if (data.type === "ready") {
      starting = false
      recording = true
      paused = false
      stopping = false
      segmentStartedMs = Date.now()
      outputPath = String(data.path || "")
      currentMicrophone = String(data.microphone || "")
      currentMicrophoneLabel = String(data.microphone_label || microphoneLabel(currentMicrophone))
      currentDesktop = String(data.desktop || "")
      currentDesktopLabel = String(data.desktop_label || desktopLabel)
      return
    }

    if (data.type === "level") {
      if (!paused && !stopping) {
        microphoneLevel = Math.max(0, Math.min(1, Number(data.microphone) || 0))
        desktopLevel = Math.max(0, Math.min(1, Number(data.desktop) || 0))
      }
      return
    }

    if (data.type === "state") {
      setBackendState(String(data.state || ""))
      return
    }

    if (data.type === "mute") {
      microphoneMuted = data.microphone === true
      desktopMuted = data.desktop === true
      return
    }

    if (data.type === "device") {
      currentMicrophone = String(data.microphone || currentMicrophone)
      currentMicrophoneLabel = String(data.microphone_label || microphoneLabel(currentMicrophone))
      return
    }

    if (data.type === "saved") {
      commitRunningTime()
      lastSavedPath = String(data.path || outputPath)
      recording = false
      paused = false
      stopping = false
      microphoneLevel = 0
      desktopLevel = 0
      if (lastSavedPath !== "") {
        Quickshell.execDetached([
          "omarchy-notification-send",
          "Call recording saved",
          lastSavedPath,
          "-t", "10000",
          "--exec", "xdg-open", "--", lastSavedPath
        ])
      }
      return
    }

    if (data.type === "error") {
      errorText = String(data.message || "Recording error")
    }
  }

  IpcHandler {
    target: "illegalstudio.omarchy-call-recorder"

    function start(): string { return root.startRecording() }
    function open(): void { root.openPanel() }
    function stop(): void { root.stopRecording() }
    function pause(): void { if (!root.paused) root.togglePause() }
    function resume(): void { if (root.paused) root.togglePause() }
    function muteMicrophone(value: string): void { root.setMicrophoneMuted(root.boolValue(value)) }
    function muteDesktop(value: string): void { root.setDesktopMuted(root.boolValue(value)) }
    function microphone(name: string): void { root.setMicrophone(name) }
    function isActive(): string { return root.visibleInBar ? "true" : "false" }
    function state(): string {
      return JSON.stringify({
        active: root.visibleInBar,
        recording: root.recording,
        paused: root.paused,
        stopping: root.stopping,
        microphoneMuted: root.microphoneMuted,
        desktopMuted: root.desktopMuted,
        microphone: root.currentMicrophone,
        microphoneLabel: root.currentMicrophoneLabel,
        desktop: root.currentDesktop,
        desktopLabel: root.currentDesktopLabel,
        elapsed: root.elapsedSeconds,
        path: root.outputPath,
        error: root.errorText
      })
    }
  }

  Process {
    id: devicesProc
    command: [root.backendPath, "devices"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.loadDevices(text)
    }
  }

  Process {
    id: recorderProc
    stdinEnabled: true
    stdout: SplitParser {
      onRead: function(line) { root.applyBackendLine(line) }
    }
    stderr: SplitParser {
      onRead: function(line) {
        if (String(line || "").trim() !== "") root.errorText = String(line).trim()
      }
    }
    onExited: function(exitCode) {
      root.commitRunningTime()
      var hadError = root.errorText !== "" || exitCode !== 0
      if (exitCode !== 0 && root.errorText === "") root.errorText = "Recorder stopped with exit code " + exitCode
      root.starting = false
      root.recording = false
      root.paused = false
      root.stopping = false
      root.microphoneLevel = 0
      root.desktopLevel = 0
      root.segmentStartedMs = 0
      if (hadError) {
        Quickshell.execDetached([
          "omarchy-notification-send",
          "Call recording error",
          root.errorText,
          "-u", "critical",
          "-t", "8000"
        ])
      }
    }
  }

  Timer {
    interval: 500
    repeat: true
    running: root.visibleInBar
    onTriggered: root.clockNowMs = Date.now()
  }

  Timer {
    interval: 15000
    repeat: true
    running: true
    triggeredOnStart: true
    onTriggered: root.refreshDevices()
  }
}
