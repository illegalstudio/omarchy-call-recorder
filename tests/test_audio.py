from array import array
import contextlib
import io
import json
import math
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import recorder
from recorder import GLib, Gst, Recorder, capture_branch
from mix_audio import check_dependencies, mix_tracks, probe_audio


RATE = 48000


def tone(frequency, seconds, amplitude=0.1):
    return array("h", (round(amplitude * 32767 * math.sin(2 * math.pi * frequency * i / RATE))
                       for i in range(round(seconds * RATE))))


def decode(path):
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-f", "s16le", "-"],
        check=True, capture_output=True,
    )
    samples = array("h")
    samples.frombytes(result.stdout)
    return samples


def amplitude(samples, frequency, start, duration=0.1):
    window = samples[round(start * RATE):round((start + duration) * RATE)]
    if not window:
        return 0
    sine = sum(value * math.sin(2 * math.pi * frequency * i / RATE) for i, value in enumerate(window))
    cosine = sum(value * math.cos(2 * math.pi * frequency * i / RATE) for i, value in enumerate(window))
    return 2 * math.hypot(sine, cosine) / len(window) / 32767


def flac(path, samples):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-n", "-f", "s16le", "-ar", str(RATE),
         "-ac", "1", "-i", "-", "-c:a", "flac", str(path)],
        input=samples.tobytes(), check=True, capture_output=True,
    )


class AudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gst.init(None)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="call-recorder-test-")
        self.directory = Path(self.temporary.name)
        self.mic = self.directory / "microphone.flac"
        self.desktop = self.directory / "desktop.flac"
        self.output = self.directory / "mix.mp3"

    def tearDown(self):
        self.temporary.cleanup()

    def capture(self, desktop_timestamps, delayed=False):
        description = ""
        for name, path in (("mic", self.mic), ("desktop", self.desktop)):
            source = (f"appsrc name={name}src is-live=true format=time block=true "
                      "caps=audio/x-raw,format=S16LE,rate=48000,channels=1,layout=interleaved")
            description += capture_branch(source, name, path)
        pipeline = Gst.parse_launch(description)
        try:
            pipeline.set_state(Gst.State.PLAYING)
            for name, timestamps, frequency in (
                    ("mic", [i / 10 for i in range(10)], 440),
                    ("desktop", desktop_timestamps, 880)):
                if delayed and name == "desktop":
                    # Desktop arrives after the entire nominal recording time.
                    # This must not cause it to be dropped by a live deadline.
                    time.sleep(1.4)
                source = pipeline.get_by_name(name + "src")
                data = tone(frequency, 0.1).tobytes()
                for stamp in timestamps:
                    buffer = Gst.Buffer.new_allocate(None, len(data), None)
                    buffer.fill(0, data)
                    buffer.pts = round(stamp * Gst.SECOND)
                    buffer.duration = Gst.SECOND // 10
                    self.assertEqual(source.emit("push-buffer", buffer), Gst.FlowReturn.OK)
                source.emit("end-of-stream")
            message = pipeline.get_bus().timed_pop_filtered(
                10 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
            self.assertIsNotNone(message, "Capture did not finish")
            if message.type == Gst.MessageType.ERROR:
                self.fail(str(message.parse_error()))
            element = pipeline.get_by_name("desktoprate")
            return {key: element.get_property(key) for key in ("in", "out", "add", "drop")}
        finally:
            pipeline.set_state(Gst.State.NULL)

    def test_late_desktop_start_and_clock_jump_preserve_all_audio(self):
        stamps = [.12 + i / 10 + (.21 if i >= 5 else 0) for i in range(10)]
        stats = self.capture(stamps, delayed=True)
        self.assertEqual({key: stats[key] for key in ("in", "add", "drop")},
                         {"in": 48000, "add": 15840, "drop": 0})
        samples = decode(self.desktop)
        # GStreamer 1.24 undercounts the `out` property when filling a gap.
        # Verify actual decoded output, not that version-dependent statistic.
        self.assertEqual(len(samples), 63840)
        self.assertEqual(len(samples), stats["in"] + stats["add"] - stats["drop"])
        self.assertLess(max(abs(x) for x in samples[:5760]), 3)
        self.assertLess(max(abs(x) for x in samples[29760:39840]), 3)
        for start in (.12, .22, .32, .42, .52, .83, .93, 1.03, 1.13, 1.23):
            self.assertAlmostEqual(amplitude(samples, 880, start), .1, delta=.001)
        mix_tracks(self.mic, self.desktop, self.output)
        mixed = decode(self.output)
        self.assertLessEqual(abs(len(mixed) - len(samples)), 1)
        self.assertAlmostEqual(amplitude(mixed, 440, .2), .095, delta=.01)
        self.assertAlmostEqual(amplitude(mixed, 880, .2), .095, delta=.01)
        self.assertAlmostEqual(amplitude(mixed, 880, 1.2), .095, delta=.01)

    def test_small_timestamp_jitter_does_not_insert_or_drop_audio(self):
        stamps = [i / 10 + (.0005 if i % 2 else 0) for i in range(10)]
        stats = self.capture(stamps)
        self.assertEqual(stats, {"in": 48000, "out": 48000, "add": 0, "drop": 0})

    def test_timestamp_overlap_is_not_played_twice(self):
        stats = self.capture([0, .1, .2, .2, .3, .4, .5, .6, .7, .8])
        self.assertEqual(stats, {"in": 48000, "out": 43200, "add": 0, "drop": 4800})

    def test_mix_keeps_both_sources_and_tail_at_constant_gain(self):
        flac(self.mic, tone(440, 8))
        flac(self.desktop, tone(880, 12))
        before = (self.mic.read_bytes(), self.desktop.read_bytes())
        progress = []
        result = mix_tracks(self.mic, self.desktop, self.output, progress.append)
        self.assertEqual(result["duration_seconds"], 12)
        self.assertEqual(progress[-1], 100)
        mixed = decode(self.output)
        self.assertLessEqual(abs(len(mixed) - 12 * RATE), 1)
        for second in range(12):
            self.assertAlmostEqual(amplitude(mixed, 880, second + .2), .095, delta=.01)
            self.assertAlmostEqual(amplitude(mixed, 440, second + .2),
                                   .095 if second < 8 else 0, delta=.01)
        self.assertEqual(before, (self.mic.read_bytes(), self.desktop.read_bytes()))

    def test_limiter_prevents_clipping_of_overlapping_loud_sources(self):
        flac(self.mic, tone(440, 1, .8))
        flac(self.desktop, tone(880, 1, .8))
        mix_tracks(self.mic, self.desktop, self.output)
        self.assertLess(max(abs(value) for value in decode(self.output)), 32767)

    def test_mix_preserves_mute_gaps_and_tails_in_both_input_orders(self):
        microphone = tone(440, .4) + array("h", [0]) * round(.3 * RATE) + tone(440, 1.3)
        desktop = tone(880, 1.2) + array("h", [0]) * round(.3 * RATE) + tone(880, .5)
        flac(self.mic, microphone)
        flac(self.desktop, desktop)
        for index, sources in enumerate(((self.mic, self.desktop), (self.desktop, self.mic))):
            with self.subTest(input_order=index):
                output = self.directory / f"order-{index}.mp3"
                mix_tracks(*sources, output)
                samples = decode(output)
                self.assertEqual(len(samples), 2 * RATE)
                for second, mic_active, desktop_active in (
                        (.1, True, True), (.5, False, True), (.8, True, True),
                        (1.0, True, True), (1.3, True, False), (1.7, True, True)):
                    self.assertAlmostEqual(amplitude(samples, 440, second),
                                           .095 if mic_active else 0, delta=.01)
                    self.assertAlmostEqual(amplitude(samples, 880, second),
                                           .095 if desktop_active else 0, delta=.01)

    def test_longer_microphone_tail_is_not_truncated(self):
        flac(self.mic, tone(440, 1.003))
        flac(self.desktop, tone(880, .713))
        mix_tracks(self.mic, self.desktop, self.output)
        samples = decode(self.output)
        self.assertEqual(len(samples), round(1.003 * RATE))
        self.assertAlmostEqual(amplitude(samples, 440, .85), .095, delta=.01)
        self.assertAlmostEqual(amplitude(samples, 880, .85), 0, delta=.01)

    def test_fully_silent_source_does_not_suppress_the_other_source(self):
        flac(self.mic, array("h", [0]) * (2 * RATE))
        flac(self.desktop, tone(880, 2))
        mix_tracks(self.mic, self.desktop, self.output)
        samples = decode(self.output)
        self.assertEqual(len(samples), 2 * RATE)
        for second in (.1, .8, 1.7):
            self.assertAlmostEqual(amplitude(samples, 880, second), .095, delta=.01)

    def test_existing_output_is_not_overwritten(self):
        self.output.write_bytes(b"existing recording")
        with self.assertRaises(FileExistsError):
            mix_tracks(self.mic, self.desktop, self.output)
        self.assertEqual(self.output.read_bytes(), b"existing recording")

    def test_output_created_during_encoding_is_not_overwritten(self):
        flac(self.mic, tone(440, 1))
        flac(self.desktop, tone(880, 1))

        def competing_file(_percent):
            if not self.output.exists():
                self.output.write_bytes(b"another recording")

        with self.assertRaises(FileExistsError):
            mix_tracks(self.mic, self.desktop, self.output, competing_file)
        self.assertEqual(self.output.read_bytes(), b"another recording")
        self.assertFalse(list(self.directory.glob(".*.mp3")))

    def test_missing_dependency_is_detected_before_capture(self):
        with patch("mix_audio.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Install the ffmpeg package"):
                check_dependencies()

    def test_invalid_source_does_not_publish_mp3(self):
        flac(self.mic, tone(440, 1))
        self.desktop.write_bytes(b"broken FLAC")
        with self.assertRaises((RuntimeError, subprocess.CalledProcessError)):
            mix_tracks(self.mic, self.desktop, self.output)
        self.assertFalse(self.output.exists())
        self.assertTrue(self.mic.exists())

    def test_encoder_failure_removes_partial_output_and_preserves_tracks(self):
        flac(self.mic, tone(440, 1))
        flac(self.desktop, tone(880, 1))
        with patch("mix_audio.MIX_FILTER", "invalid_filter_for_test"):
            with self.assertRaisesRegex(RuntimeError, "encoding failed"):
                mix_tracks(self.mic, self.desktop, self.output)
        self.assertEqual(sorted(p.name for p in self.directory.iterdir()),
                         ["desktop.flac", "microphone.flac"])

    def test_export_failure_is_reported_without_saved_event(self):
        instance = Recorder.__new__(Recorder)
        instance.stopping = False
        instance.failed = False
        instance.output_file = self.output
        instance.artifacts = {"microphone": self.mic, "desktop": self.desktop}
        with patch.object(instance, "write_session_metadata") as metadata, \
                patch("recorder.mix_tracks", side_effect=RuntimeError("Disk full")), \
                patch("recorder.emit") as events:
            instance.finalize()
        self.assertTrue(instance.failed)
        self.assertEqual([c.args[0] for c in metadata.call_args_list], ["finalizing", "error"])
        self.assertNotIn("saved", [c.args[0] for c in events.call_args_list])
        self.assertIn("error", [c.args[0] for c in events.call_args_list])

    def test_live_lifecycle_pause_mute_stop_and_finalization(self):
        devices = {
            "microphones": [{"value": "synthetic", "label": "Synthetic microphone"}],
            "default_microphone": "synthetic", "default_desktop_output": "synthetic",
            "desktop_outputs": [{"value": "synthetic", "source": "synthetic.monitor"}],
        }
        with patch("recorder.audio_devices", return_value=devices), \
                patch("recorder.Path.home", return_value=self.directory):
            instance = Recorder("", "", "")
        self.output = instance.output_file
        instance.active_file = self.directory / "active.json"
        instance.lock_file = self.directory / "recorder.lock"

        def build():
            description = ""
            for name, frequency, path in (("mic", 440, instance.artifacts["microphone"]),
                                          ("desktop", 880, instance.artifacts["desktop"])):
                source = (f"audiotestsrc name={name}src is-live=true wave=sine "
                          f"freq={frequency} volume=0.1 samplesperbuffer=480")
                description += capture_branch(source, name, path)
            instance.pipeline = Gst.parse_launch(description)
            instance.mic_volume = instance.pipeline.get_by_name("micvolume")
            instance.desktop_volume = instance.pipeline.get_by_name("desktopvolume")
            bus = instance.pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message", instance.on_message)

        def once(action):
            def invoke():
                action()
                return False
            return invoke

        timers = []
        actions = [(.4, lambda: instance.set_muted("microphone", True)),
                   (.7, lambda: instance.set_muted("microphone", False)),
                   (.9, lambda: instance.set_paused(True)),
                   (1.3, lambda: instance.set_paused(False)),
                   (1.6, lambda: instance.set_muted("desktop", True)),
                   (1.9, lambda: instance.set_muted("desktop", False)),
                   (2.2, lambda: instance.set_paused(True)),
                   (2.4, instance.stop)]

        def controls():
            for seconds, action in actions:
                timers.append(GLib.timeout_add(round(seconds * 1000), once(action)))

        output = io.StringIO()
        with patch.object(instance, "build_pipeline", side_effect=build), \
                patch.object(instance, "refresh_desktop_route", return_value=False), \
                patch.object(instance, "read_commands", side_effect=controls), \
                contextlib.redirect_stdout(output):
            try:
                status = instance.run()
            finally:
                instance.cleanup()
        self.assertEqual(status, 0)
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(events[-1]["type"], "saved")
        self.assertIn({"type": "state", "state": "finalizing"}, events)
        session = json.loads(instance.artifacts["session"].read_text())
        self.assertEqual(self.output.parent.parent, self.directory / "Music" / "Recordings")
        self.assertRegex(self.output.parent.name, r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}-audio-rec$")
        stamp = self.output.parent.name.removesuffix("-audio-rec")
        self.assertEqual({path.name for path in self.output.parent.iterdir()},
                         {f"{stamp}-{name}" for name in
                          ("audio.mp3", "microphone.flac", "desktop.flac", "session.json")})
        self.assertEqual(session["files"], {key: str(path) for key, path in instance.artifacts.items()})
        for event in (events[0], events[-1]):
            self.assertEqual(event["path"], str(self.output))
            self.assertEqual(event["microphone_path"], str(instance.artifacts["microphone"]))
            self.assertEqual(event["desktop_path"], str(instance.artifacts["desktop"]))
            self.assertEqual(event["session_path"], str(instance.artifacts["session"]))
        self.assertEqual(session["status"], "saved")
        self.assertEqual(session["schema_version"], 2)
        self.assertEqual(session["mix"]["method"], "offline-flac-mix")
        duration = float(probe_audio(self.output)["duration"])
        self.assertGreater(duration, 1.7)
        self.assertLess(duration, 2.05, "Paused time must not become silence in the MP3")
        mixed = decode(self.output)
        self.assertLess(amplitude(mixed, 440, .48), .005)
        self.assertGreater(amplitude(mixed, 880, .48), .08)
        self.assertGreater(amplitude(mixed, 440, 1.28), .08)
        self.assertLess(amplitude(mixed, 880, 1.28), .005)
        self.assertGreater(amplitude(mixed, 440, 1.6), .08)
        self.assertGreater(amplitude(mixed, 880, 1.6), .08)
        self.assertFalse(instance.active_file.exists())


if __name__ == "__main__":
    unittest.main()
