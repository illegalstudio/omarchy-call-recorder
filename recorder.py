#!/usr/bin/env python3

import argparse
from datetime import datetime
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading

import gi

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst

from mix_audio import check_dependencies, mix_tracks


APP_NAME = "Omarchy Call Recorder"


def emit(kind, **fields):
    payload = {"type": kind}
    payload.update(fields)
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def pactl_json(*args):
    raw = subprocess.check_output(["pactl", "-f", "json", *args], text=True)
    return json.loads(raw)


def audio_devices(info=None, snapshot=None):
    info = info or pactl_json("info")
    snapshot = snapshot or pactl_json("list")
    sources = snapshot.get("sources", [])
    sinks = snapshot.get("sinks", [])

    microphones = []
    source_by_name = {}
    for source in sources:
        name = str(source.get("name") or "")
        if not name:
            continue
        source_by_name[name] = source
        properties = source.get("properties") or {}
        media_class = str(properties.get("media.class") or "")
        if name.endswith(".monitor") or media_class == "Audio/Sink":
            continue
        label = str(source.get("description") or properties.get("device.description") or name)
        microphones.append({"value": name, "label": label})

    microphones.sort(key=lambda item: item["label"].casefold())
    default_microphone = str(info.get("default_source_name") or "")
    if default_microphone not in {item["value"] for item in microphones} and microphones:
        default_microphone = microphones[0]["value"]

    desktop_outputs = []
    for sink in sinks:
        name = str(sink.get("name") or "")
        if not name:
            continue
        properties = sink.get("properties") or {}
        label = str(sink.get("description") or properties.get("device.description") or name)
        monitor_source = str(sink.get("monitor_source") or f"{name}.monitor")
        desktop_outputs.append(
            {
                "value": name,
                "label": label,
                "source": monitor_source,
                "index": int(sink.get("index", -1)),
            }
        )

    desktop_outputs.sort(key=lambda item: item["label"].casefold())
    default_sink = str(info.get("default_sink_name") or "")
    output_by_name = {item["value"]: item for item in desktop_outputs}
    default_output = output_by_name.get(default_sink) or {}
    desktop_source = str(default_output.get("source") or "")
    desktop_label = str(default_output.get("label") or default_sink or "Default output")

    return {
        "microphones": microphones,
        "default_microphone": default_microphone,
        "desktop_outputs": desktop_outputs,
        "default_desktop_output": default_sink,
        "desktop_source": desktop_source,
        "desktop_label": desktop_label,
        "source_names": list(source_by_name),
    }


def device_label(devices, name):
    for device in devices.get("microphones", []):
        if device["value"] == name:
            return device["label"]
    return name or "Default microphone"


def artifact_paths(output_path, *, grouped=False):
    stem = output_path.with_suffix("")
    if grouped:
        stem = stem.with_name(stem.name.removesuffix("-audio"))
    return {
        "mix": output_path,
        "microphone": stem.with_name(f"{stem.name}-microphone.flac"),
        "desktop": stem.with_name(f"{stem.name}-desktop.flac"),
        "session": stem.with_name(f"{stem.name}-session.json"),
    }


def choose_output_path(explicit_path):
    if not explicit_path:
        directory = Path.home() / "Music" / "Recordings"
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        for index in range(1, 1000):
            number = "" if index == 1 else f"-{index}"
            session_directory = directory / f"{stamp}{number}-audio-rec"
            try:
                # Reserve the whole folder, even if an existing one is empty.
                session_directory.mkdir(mode=0o700)
            except FileExistsError:
                continue
            return session_directory / f"{stamp}{number}-audio.mp3"
        raise RuntimeError("Could not choose an unused recording folder")

    # An explicit CLI filename retains its custom path and sidecar names.
    path = Path(explicit_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not any(candidate.exists() for candidate in artifact_paths(path).values()):
        return path

    stem = path.stem
    suffix = path.suffix
    for index in range(2, 1000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not any(item.exists() for item in artifact_paths(candidate).values()):
            return candidate
    raise RuntimeError("Could not choose an unused output filename")


def gst_quote(value):
    return json.dumps(str(value))


def meter_value(db):
    if not math.isfinite(db):
        return 0.0
    return max(0.0, min(1.0, (db + 60.0) / 60.0))


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def desktop_route(devices, requested_output):
    outputs = {item["value"]: item for item in devices.get("desktop_outputs", [])}
    selected = outputs.get(requested_output) if requested_output else None
    fallback = bool(requested_output and selected is None)
    if selected is None:
        selected = outputs.get(devices.get("default_desktop_output"))
    if selected is None and outputs:
        selected = next(iter(outputs.values()))
    return selected or {}, fallback


def capture_branch(source, name, output):
    # FLAC does not retain buffer timestamps. Make sample positions match the
    # shared pipeline timeline, including delayed starts and clock corrections.
    # No live mixer may discard a late source: both files are mixed after EOS.
    return (
        f"{source} ! audioconvert ! audioresample ! "
        "audio/x-raw,format=F32LE,rate=48000,channels=1 ! "
        f"audiorate name={name}rate skip-to-first=false tolerance=1000000 ! "
        f"volume name={name}volume ! "
        f"level name={name}level interval=100000000 post-messages=true ! "
        "audioconvert ! audio/x-raw,format=S16LE,rate=48000,channels=1 ! "
        "flacenc quality=5 ! "
        f"filesink location={gst_quote(output)} sync=false async=false "
    )


class Recorder:
    def __init__(self, microphone, desktop_output, output_file):
        self.devices = audio_devices()
        available = {item["value"] for item in self.devices["microphones"]}
        self.microphone = microphone if microphone in available else self.devices["default_microphone"]
        self.requested_desktop_output = desktop_output
        self.initial_desktop_output = desktop_output
        route, self.desktop_fallback = desktop_route(self.devices, self.requested_desktop_output)
        self.desktop_output = str(route.get("value") or "")
        self.desktop_source = str(route.get("source") or "")
        self.desktop_label = str(route.get("label") or "Default output")
        if not self.microphone:
            raise RuntimeError("No microphone source is available")
        if not self.desktop_source:
            raise RuntimeError("No desktop audio source is available")

        self.output_file = choose_output_path(output_file)
        self.artifacts = artifact_paths(self.output_file, grouped=not output_file)
        self.loop = GLib.MainLoop()
        self.pipeline = None
        self.mic_volume = None
        self.desktop_volume = None
        self.mic_source = None
        self.paused = False
        self.stopping = False
        self.failed = False
        self.received_eos = False
        self.timeline_stats = {}
        self.mix_result = None
        self.mic_muted = False
        self.desktop_muted = False
        self.levels = {"miclevel": 0.0, "desktoplevel": 0.0}
        self.peak_levels = {"microphone": 0.0, "desktop": 0.0}
        self.desktop_active_samples = 0
        self.route_events = []
        self.control_events = []
        self.last_health = None
        self.started_at = now_iso()
        self.finished_at = None
        self.failure_detail = ""
        self.runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
        self.active_file = self.runtime_dir / "illegalstudio.omarchy-call-recorder.active"
        self.lock_file = self.runtime_dir / "illegalstudio.omarchy-call-recorder.lock"
        self.lock_handle = None

    def acquire_lock(self):
        self.lock_handle = self.lock_file.open("w")
        try:
            fcntl.flock(self.lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("A call recording is already active") from error

    def build_pipeline(self):
        microphone = (
            f"pulsesrc name=micsrc device={gst_quote(self.microphone)} "
            f"client-name={gst_quote(APP_NAME + ' Microphone')} provide-clock=true"
        )
        desktop = (
            f"pulsesrc name=desktopsrc device={gst_quote(self.desktop_source)} "
            f"client-name={gst_quote(APP_NAME + ' Desktop')} provide-clock=false"
        )
        description = capture_branch(microphone, "mic", self.artifacts["microphone"])
        description += capture_branch(desktop, "desktop", self.artifacts["desktop"])
        self.pipeline = Gst.parse_launch(description)
        self.mic_volume = self.pipeline.get_by_name("micvolume")
        self.desktop_volume = self.pipeline.get_by_name("desktopvolume")
        self.mic_source = self.pipeline.get_by_name("micsrc")

        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.on_message)

    def on_message(self, _bus, message):
        if message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            self.failed = True
            self.failure_detail = str(debug or error)
            emit("error", message=str(error), detail=str(debug or ""))
            self.loop.quit()
            return

        if message.type == Gst.MessageType.EOS:
            self.finished_at = now_iso()
            self.received_eos = True
            self.loop.quit()
            return

        if message.type == Gst.MessageType.LATENCY:
            self.pipeline.recalculate_latency()
            return

        if message.type != Gst.MessageType.ELEMENT:
            return
        structure = message.get_structure()
        if not structure or structure.get_name() != "level":
            return

        values = structure.get_value("rms")
        db = max(values) if values else -60.0
        name = message.src.get_name()
        value = meter_value(float(db))
        self.levels[name] = value
        if name == "miclevel":
            self.peak_levels["microphone"] = max(self.peak_levels["microphone"], value)
        elif name == "desktoplevel":
            self.peak_levels["desktop"] = max(self.peak_levels["desktop"], value)
            if value >= 0.08:
                self.desktop_active_samples += 1
        emit(
            "level",
            microphone=self.levels["miclevel"],
            desktop=self.levels["desktoplevel"],
        )

    def write_active_file(self):
        self.active_file.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "path": str(self.output_file),
                    "microphone_path": str(self.artifacts["microphone"]),
                    "desktop_path": str(self.artifacts["desktop"]),
                    "session_path": str(self.artifacts["session"]),
                }
            ),
            encoding="utf-8",
        )

    def record_control(self, action, **fields):
        event = {"at": now_iso(), "action": action}
        event.update(fields)
        self.control_events.append(event)

    def record_route(self):
        details = {
            "output": self.desktop_output,
            "source": self.desktop_source,
            "label": self.desktop_label,
            "fallback": self.desktop_fallback,
        }
        if not self.route_events or any(
            self.route_events[-1].get(key) != value for key, value in details.items()
        ):
            event = {"at": now_iso()}
            event.update(details)
            self.route_events.append(event)

    def write_session_metadata(self, status):
        payload = {
            "schema_version": 2,
            "status": status,
            "started_at": self.started_at,
            "finished_at": self.finished_at or now_iso(),
            "files": {key: str(path) for key, path in self.artifacts.items()},
            "microphone": self.microphone,
            "initial_desktop_output": self.initial_desktop_output,
            "requested_desktop_output": self.requested_desktop_output,
            "final_desktop_output": self.desktop_output,
            "final_desktop_source": self.desktop_source,
            "route_events": self.route_events,
            "control_events": self.control_events,
            "peak_levels": self.peak_levels,
            "desktop_active_samples": self.desktop_active_samples,
            "timeline": {
                "method": "audiorate", "sample_rate": 48000,
                "tolerance_ns": 1000000, "tracks": self.timeline_stats,
            },
            "mix": self.mix_result,
            "error": self.failure_detail,
        }
        self.artifacts["session"].write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def set_paused(self, paused):
        if self.stopping or self.paused == paused:
            return
        target = Gst.State.PAUSED if paused else Gst.State.PLAYING
        result = self.pipeline.set_state(target)
        if result == Gst.StateChangeReturn.FAILURE:
            emit("error", message="Could not change the recording pause state")
            return
        self.paused = paused
        self.record_control("pause" if paused else "resume")
        if paused:
            self.levels = {"miclevel": 0.0, "desktoplevel": 0.0}
        emit("state", state="paused" if paused else "recording")

    def set_muted(self, channel, muted):
        element = self.mic_volume if channel == "microphone" else self.desktop_volume
        if element is None:
            return
        element.set_property("mute", bool(muted))
        if channel == "microphone":
            self.mic_muted = bool(muted)
        else:
            self.desktop_muted = bool(muted)
        self.record_control("mute", channel=channel, value=bool(muted))
        emit(
            "mute",
            microphone=self.mic_muted,
            desktop=self.desktop_muted,
        )

    def set_microphone(self, name):
        devices = audio_devices()
        available = {item["value"] for item in devices["microphones"]}
        if name not in available:
            emit("error", message="The selected microphone is no longer available")
            return
        self.mic_source.set_property("device", name)
        self.microphone = name
        self.devices = devices
        self.record_control("microphone", value=name)
        emit(
            "device",
            microphone=self.microphone,
            microphone_label=device_label(devices, self.microphone),
        )

    def desktop_source_output(self, source_outputs):
        process_id = str(os.getpid())
        for source_output in source_outputs:
            properties = source_output.get("properties") or {}
            if str(properties.get("application.process.id") or "") != process_id:
                continue
            if str(properties.get("application.name") or "") == APP_NAME + " Desktop":
                return source_output
        return None

    def browser_output_labels(self, devices, sink_inputs):
        sink_by_index = {
            int(item.get("index", -1)): item
            for item in devices.get("desktop_outputs", [])
        }
        labels = []
        for sink_input in sink_inputs:
            properties = sink_input.get("properties") or {}
            application = str(properties.get("application.name") or "").casefold()
            if "chrome" not in application and "chromium" not in application:
                continue
            if sink_input.get("corked") is True:
                continue
            output = sink_by_index.get(int(sink_input.get("sink", -1)))
            if output and output["value"] != self.desktop_output:
                labels.append(output["label"])
        return sorted(set(labels))

    def emit_health(self, healthy, status):
        signature = (bool(healthy), str(status))
        if signature == self.last_health:
            return
        self.last_health = signature
        emit("health", desktop_healthy=bool(healthy), desktop_status=str(status))

    def refresh_desktop_route(self):
        if self.stopping:
            return False
        try:
            info = pactl_json("info")
            snapshot = pactl_json("list")
            devices = audio_devices(info=info, snapshot=snapshot)
            route, fallback = desktop_route(devices, self.requested_desktop_output)
            desired_output = str(route.get("value") or "")
            desired_source = str(route.get("source") or "")
            desired_label = str(route.get("label") or "Default output")
            if not desired_source:
                self.emit_health(False, "No desktop output is available")
                return True

            source_by_name = {
                str(source.get("name") or ""): source
                for source in snapshot.get("sources", [])
            }
            desired_source_info = source_by_name.get(desired_source)
            source_output = self.desktop_source_output(snapshot.get("source_outputs", []))
            if desired_source_info is None or source_output is None:
                self.emit_health(False, "Desktop capture is reconnecting")
                return True

            desired_index = int(desired_source_info.get("index", -1))
            current_index = int(source_output.get("source", -1))
            route_changed = (
                desired_output != self.desktop_output
                or desired_source != self.desktop_source
                or desired_index != current_index
            )
            if desired_index != current_index:
                subprocess.run(
                    [
                        "pactl",
                        "move-source-output",
                        str(source_output.get("index")),
                        desired_source,
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )

            self.devices = devices
            self.desktop_output = desired_output
            self.desktop_source = desired_source
            self.desktop_label = desired_label
            self.desktop_fallback = fallback
            if route_changed:
                self.record_route()
                emit(
                    "device",
                    desktop=self.desktop_source,
                    desktop_output=self.desktop_output,
                    desktop_label=self.desktop_label,
                )

            browser_outputs = self.browser_output_labels(
                devices,
                snapshot.get("sink_inputs", []),
            )
            if browser_outputs:
                self.emit_health(
                    False,
                    "Chrome is playing on " + ", ".join(browser_outputs),
                )
            elif fallback:
                self.emit_health(
                    False,
                    "Selected output unavailable, capturing " + self.desktop_label,
                )
            else:
                self.emit_health(True, "Capturing " + self.desktop_label)
        except (OSError, subprocess.CalledProcessError, ValueError, json.JSONDecodeError):
            self.emit_health(False, "Could not verify the desktop audio route")
        return True

    def set_desktop_output(self, name):
        self.requested_desktop_output = name
        self.record_control("desktop-output", value=name)
        self.refresh_desktop_route()

    def stop(self):
        if self.stopping:
            return
        self.stopping = True
        self.mic_volume.set_property("mute", True)
        self.desktop_volume.set_property("mute", True)
        if self.paused:
            self.pipeline.set_state(Gst.State.PLAYING)
        emit("state", state="stopping")
        GLib.timeout_add(50, self.send_eos)

    def send_eos(self):
        self.pipeline.send_event(Gst.Event.new_eos())
        return False

    def handle_command(self, command):
        if self.stopping:
            return False
        action = str(command.get("command") or "")
        if action == "pause":
            self.set_paused(True)
        elif action == "resume":
            self.set_paused(False)
        elif action == "mute-microphone":
            self.set_muted("microphone", bool(command.get("value")))
        elif action == "mute-desktop":
            self.set_muted("desktop", bool(command.get("value")))
        elif action == "microphone":
            self.set_microphone(str(command.get("value") or ""))
        elif action == "desktop-output":
            self.set_desktop_output(str(command.get("value") or ""))
        elif action == "stop":
            self.stop()
        return False

    def read_commands(self):
        try:
            for line in sys.stdin:
                try:
                    command = json.loads(line)
                except json.JSONDecodeError:
                    continue
                GLib.idle_add(self.handle_command, command)
        finally:
            GLib.idle_add(self.stop)

    def run(self):
        check_dependencies()
        Gst.init(None)
        self.acquire_lock()
        self.build_pipeline()
        result = self.pipeline.set_state(Gst.State.PLAYING)
        if result == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("GStreamer could not start the recording pipeline")

        self.write_active_file()
        self.record_route()
        emit(
            "ready",
            path=str(self.output_file),
            microphone_path=str(self.artifacts["microphone"]),
            desktop_path=str(self.artifacts["desktop"]),
            session_path=str(self.artifacts["session"]),
            microphone=self.microphone,
            microphone_label=device_label(self.devices, self.microphone),
            desktop=self.desktop_source,
            desktop_output=self.desktop_output,
            desktop_label=self.desktop_label,
        )

        GLib.timeout_add_seconds(2, self.refresh_desktop_route)

        command_thread = threading.Thread(target=self.read_commands, daemon=True)
        command_thread.start()
        self.loop.run()
        # Read counters before NULL resets audiorate; close/flush both encoders
        # before ffmpeg opens the FLAC files. Capture is over during MP3 export.
        for track, name in (("microphone", "micrate"), ("desktop", "desktoprate")):
            element = self.pipeline.get_by_name(name)
            self.timeline_stats[track] = {
                key: int(element.get_property(key)) for key in ("in", "out", "add", "drop")
            }
        self.pipeline.set_state(Gst.State.NULL)
        if self.finished_at is None:
            self.finished_at = now_iso()
        if self.failed or not self.received_eos:
            self.write_session_metadata("error" if self.failed else "stopped")
        else:
            self.finalize()
        return 1 if self.failed else 0

    def finalize(self):
        self.stopping = True
        emit("state", state="finalizing")
        self.write_session_metadata("finalizing")
        try:
            self.mix_result = mix_tracks(
                self.artifacts["microphone"], self.artifacts["desktop"], self.output_file,
                progress=lambda percent: emit("progress", percent=percent),
            )
        except Exception as error:
            self.failed = True
            self.failure_detail = str(error)
            self.write_session_metadata("error")
            emit("error", message="Could not create the MP3. Your FLAC tracks are preserved in "
                 + str(self.output_file.parent), detail=self.failure_detail)
            return
        self.write_session_metadata("saved")
        emit(
            "saved", path=str(self.output_file),
            microphone_path=str(self.artifacts["microphone"]),
            desktop_path=str(self.artifacts["desktop"]),
            session_path=str(self.artifacts["session"]),
        )

    def cleanup(self):
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
        try:
            if self.active_file.exists():
                state = json.loads(self.active_file.read_text(encoding="utf-8"))
                if int(state.get("pid", -1)) == os.getpid():
                    self.active_file.unlink()
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        if self.lock_handle is not None:
            self.lock_handle.close()


def record(args):
    recorder = None
    try:
        recorder = Recorder(args.microphone, args.desktop_output, args.output_file)

        def request_stop(_signum, _frame):
            if recorder and recorder.loop:
                GLib.idle_add(recorder.stop)

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        return recorder.run()
    except Exception as error:
        emit("error", message=str(error))
        return 1
    finally:
        if recorder is not None:
            recorder.cleanup()


def main():
    parser = argparse.ArgumentParser(description="Audio backend for the Omarchy Call Recorder plugin")
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("devices")
    record_parser = subparsers.add_parser("record")
    record_parser.add_argument("--microphone", default="")
    record_parser.add_argument("--desktop-output", default="")
    record_parser.add_argument(
        "--output-file", default="",
        help="Use an explicit MP3 path instead of a dated recording folder",
    )
    args = parser.parse_args()

    if args.action == "devices":
        try:
            print(json.dumps(audio_devices(), ensure_ascii=False))
            return 0
        except Exception as error:
            emit("error", message=str(error))
            return 1
    return record(args)


if __name__ == "__main__":
    raise SystemExit(main())
