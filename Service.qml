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
  property string preferredDesktopOutput: ""
  property var microphones: []
  property var desktopOutputs: []
  property string defaultMicrophone: ""
  property string defaultDesktopOutput: ""
  property string desktopSource: ""
  property string desktopLabel: "Default output"
  readonly property var desktopOutputOptions: [{ value: "", label: "Follow system output" }]
    .concat(desktopOutputs || [])

  property bool starting: false
  property bool recording: false
  property bool paused: false
  property bool stopping: false
  property bool finalizing: false
  property int exportProgress: 0
  property bool microphoneMuted: false
  property bool desktopMuted: false
  property real microphoneLevel: 0
  property real desktopLevel: 0
  property string currentMicrophone: ""
  property string currentMicrophoneLabel: "Default microphone"
  property string currentDesktop: ""
  property string currentDesktopOutput: ""
  property string currentDesktopLabel: "Default output"
  property bool desktopHealthy: true
  property string desktopStatus: ""
  property string outputPath: ""
  property string microphoneTrackPath: ""
  property string desktopTrackPath: ""
  property string sessionPath: ""
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
      desktopOutputs = data.desktop_outputs || []
      defaultMicrophone = String(data.default_microphone || "")
      defaultDesktopOutput = String(data.default_desktop_output || "")
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
    if (preferredDesktopOutput !== "") args.push("--desktop-output", preferredDesktopOutput)

    errorText = ""
    outputPath = ""
    microphoneTrackPath = ""
    desktopTrackPath = ""
    sessionPath = ""
    microphoneLevel = 0
    desktopLevel = 0
    desktopHealthy = true
    desktopStatus = "Checking desktop audio route"
    microphoneMuted = false
    desktopMuted = false
    recordedMs = 0
    segmentStartedMs = 0
    starting = true
    paused = false
    stopping = false
    finalizing = false
    exportProgress = 0
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

  function setDesktopOutput(name) {
    preferredDesktopOutput = String(name || "")
    if (recording && !stopping) sendCommand("desktop-output", preferredDesktopOutput)
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
    if (state === "stopping" || state === "finalizing") {
      commitRunningTime()
      stopping = true
      finalizing = state === "finalizing"
      microphoneLevel = 0
      desktopLevel = 0
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
      microphoneTrackPath = String(data.microphone_path || "")
      desktopTrackPath = String(data.desktop_path || "")
      sessionPath = String(data.session_path || "")
      currentMicrophone = String(data.microphone || "")
      currentMicrophoneLabel = String(data.microphone_label || microphoneLabel(currentMicrophone))
      currentDesktop = String(data.desktop || "")
      currentDesktopOutput = String(data.desktop_output || "")
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

    if (data.type === "progress") {
      exportProgress = Math.max(0, Math.min(100, Number(data.percent) || 0))
      return
    }

    if (data.type === "mute") {
      microphoneMuted = data.microphone === true
      desktopMuted = data.desktop === true
      return
    }

    if (data.type === "device") {
      if (data.microphone !== undefined) {
        currentMicrophone = String(data.microphone || currentMicrophone)
        currentMicrophoneLabel = String(data.microphone_label || microphoneLabel(currentMicrophone))
      }
      if (data.desktop !== undefined) {
        currentDesktop = String(data.desktop || currentDesktop)
        currentDesktopOutput = String(data.desktop_output || currentDesktopOutput)
        currentDesktopLabel = String(data.desktop_label || currentDesktopLabel)
      }
      return
    }

    if (data.type === "health") {
      desktopHealthy = data.desktop_healthy === true
      desktopStatus = String(data.desktop_status || "")
      return
    }

    if (data.type === "saved") {
      commitRunningTime()
      lastSavedPath = String(data.path || outputPath)
      microphoneTrackPath = String(data.microphone_path || microphoneTrackPath)
      desktopTrackPath = String(data.desktop_path || desktopTrackPath)
      sessionPath = String(data.session_path || sessionPath)
      recording = false
      paused = false
      stopping = false
      finalizing = false
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
    function desktopOutput(name: string): void { root.setDesktopOutput(name) }
    function isActive(): string { return root.visibleInBar ? "true" : "false" }
    function state(): string {
      return JSON.stringify({
        active: root.visibleInBar,
        recording: root.recording,
        paused: root.paused,
        stopping: root.stopping,
        finalizing: root.finalizing,
        exportProgress: root.exportProgress,
        microphoneMuted: root.microphoneMuted,
        desktopMuted: root.desktopMuted,
        microphone: root.currentMicrophone,
        microphoneLabel: root.currentMicrophoneLabel,
        desktop: root.currentDesktop,
        desktopOutput: root.currentDesktopOutput,
        desktopLabel: root.currentDesktopLabel,
        desktopHealthy: root.desktopHealthy,
        desktopStatus: root.desktopStatus,
        elapsed: root.elapsedSeconds,
        path: root.outputPath,
        microphoneTrackPath: root.microphoneTrackPath,
        desktopTrackPath: root.desktopTrackPath,
        sessionPath: root.sessionPath,
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
      root.finalizing = false
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
