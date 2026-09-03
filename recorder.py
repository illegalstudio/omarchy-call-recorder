#!/usr/bin/env python3

import argparse
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


APP_NAME = "Omarchy Call Recorder"


def emit(kind, **fields):
    payload = {"type": kind}
    payload.update(fields)
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def pactl_json(*args):
    raw = subprocess.check_output(["pactl", "-f", "json", *args], text=True)
    return json.loads(raw)


def audio_devices():
    info = pactl_json("info")
    sources = pactl_json("list", "sources")
    sinks = pactl_json("list", "sinks")

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

    default_sink = str(info.get("default_sink_name") or "")
    desktop_source = default_sink + ".monitor" if default_sink else ""
    sink_by_name = {str(sink.get("name") or ""): sink for sink in sinks}
    sink = sink_by_name.get(default_sink) or {}
    sink_properties = sink.get("properties") or {}
    desktop_label = str(
        sink.get("description")
        or sink_properties.get("device.description")
        or default_sink
        or "Default output"
    )

    return {
        "microphones": microphones,
        "default_microphone": default_microphone,
        "desktop_source": desktop_source,
        "desktop_label": desktop_label,
        "source_names": list(source_by_name),
    }


def device_label(devices, name):
    for device in devices.get("microphones", []):
        if device["value"] == name:
            return device["label"]
    return name or "Default microphone"


def choose_output_path(explicit_path):
    if explicit_path:
        path = Path(explicit_path).expanduser()
    else:
        from datetime import datetime

        directory = Path.home() / "Music" / "Recordings"
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = directory / f"call-{stamp}.mp3"

    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    for index in range(2, 1000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError("Could not choose an unused output filename")


def gst_quote(value):
    return json.dumps(str(value))


def meter_value(db):
    if not math.isfinite(db):
        return 0.0
    return max(0.0, min(1.0, (db + 60.0) / 60.0))


class Recorder:
    def __init__(self, microphone, output_file):
        self.devices = audio_devices()
        available = {item["value"] for item in self.devices["microphones"]}
        self.microphone = microphone if microphone in available else self.devices["default_microphone"]
        self.desktop_source = self.devices["desktop_source"]
        if not self.microphone:
            raise RuntimeError("No microphone source is available")
        if not self.desktop_source:
            raise RuntimeError("No desktop audio source is available")

        self.output_file = choose_output_path(output_file)
        self.loop = GLib.MainLoop()
        self.pipeline = None
        self.mic_volume = None
        self.desktop_volume = None
        self.mic_source = None
        self.paused = False
        self.stopping = False
        self.failed = False
        self.mic_muted = False
        self.desktop_muted = False
        self.levels = {"miclevel": 0.0, "desktoplevel": 0.0}
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
        description = (
            f"pulsesrc name=micsrc device={gst_quote(self.microphone)} "
            f"client-name={gst_quote(APP_NAME + ' Microphone')} provide-clock=true ! "
            "audioconvert ! audioresample ! "
            "audio/x-raw,format=F32LE,rate=48000,channels=2 ! "
            "level name=miclevel interval=100000000 post-messages=true ! "
            "volume name=micvolume ! queue ! mix. "
            f"pulsesrc name=desktopsrc device={gst_quote(self.desktop_source)} "
            f"client-name={gst_quote(APP_NAME + ' Desktop')} provide-clock=false ! "
            "audioconvert ! audioresample ! "
            "audio/x-raw,format=F32LE,rate=48000,channels=2 ! "
            "level name=desktoplevel interval=100000000 post-messages=true ! "
            "volume name=desktopvolume ! queue ! mix. "
            "audiomixer name=mix ! audioconvert ! audioresample ! "
            "audiodynamic mode=compressor characteristics=soft-knee threshold=0.9 ratio=0.25 ! "
            "lamemp3enc target=bitrate bitrate=128 cbr=true encoding-engine-quality=standard ! "
            "id3v2mux ! "
            f"filesink location={gst_quote(self.output_file)}"
        )
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
            emit("error", message=str(error), detail=str(debug or ""))
            self.loop.quit()
            return

        if message.type == Gst.MessageType.EOS:
            emit("saved", path=str(self.output_file))
            self.loop.quit()
            return

        if message.type != Gst.MessageType.ELEMENT:
            return
        structure = message.get_structure()
        if not structure or structure.get_name() != "level":
            return

        values = structure.get_value("rms")
        db = max(values) if values else -60.0
        name = message.src.get_name()
        self.levels[name] = meter_value(float(db))
        emit(
            "level",
            microphone=self.levels["miclevel"],
            desktop=self.levels["desktoplevel"],
        )

    def write_active_file(self):
        self.active_file.write_text(
            json.dumps({"pid": os.getpid(), "path": str(self.output_file)}),
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
        emit(
            "device",
            microphone=self.microphone,
            microphone_label=device_label(devices, self.microphone),
        )

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
        Gst.init(None)
        self.acquire_lock()
        self.build_pipeline()
        result = self.pipeline.set_state(Gst.State.PLAYING)
        if result == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("GStreamer could not start the recording pipeline")

        self.write_active_file()
        emit(
            "ready",
            path=str(self.output_file),
            microphone=self.microphone,
            microphone_label=device_label(self.devices, self.microphone),
            desktop=self.desktop_source,
            desktop_label=self.devices["desktop_label"],
        )

        command_thread = threading.Thread(target=self.read_commands, daemon=True)
        command_thread.start()
        self.loop.run()
        self.pipeline.set_state(Gst.State.NULL)
        return 1 if self.failed else 0

    def cleanup(self):
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
        recorder = Recorder(args.microphone, args.output_file)

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
    record_parser.add_argument("--output-file", default="")
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
