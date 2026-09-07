from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from recorder import artifact_paths, choose_output_path


class RecordingPathTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="call-recorder-path-test-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.recordings = self.directory / "Music" / "Recordings"
        self.stamp = "2026-09-07_10-23-13"
        self.folder_name = "2026-09-07_10-23-13-audio-rec"
        home = patch("recorder.Path.home", return_value=self.directory)
        home.start()
        self.addCleanup(home.stop)
        clock = patch("recorder.datetime")
        clock.start().now.return_value = datetime(2026, 9, 7, 10, 23, 13)
        self.addCleanup(clock.stop)

    def test_default_files_share_a_dated_folder_without_call_prefixes(self):
        output = choose_output_path("")
        self.assertEqual(output, self.recordings / self.folder_name / f"{self.stamp}-audio.mp3")
        self.assertTrue(output.parent.is_dir())
        self.assertFalse(output.exists())
        paths = artifact_paths(output, grouped=True)
        self.assertEqual({path.name for path in paths.values()},
                         {f"{self.stamp}-{name}" for name in
                          ("audio.mp3", "microphone.flac", "desktop.flac", "session.json")})
        self.assertEqual({path.parent for path in paths.values()}, {output.parent})

    def test_same_second_recordings_reserve_different_folders(self):
        first = choose_output_path("")
        second = choose_output_path("")
        third = choose_output_path("")
        self.assertEqual(first.parent.name, self.folder_name)
        self.assertEqual(second.parent.name, "2026-09-07_10-23-13-2-audio-rec")
        self.assertEqual(third.parent.name, "2026-09-07_10-23-13-3-audio-rec")
        self.assertEqual(second.name, f"{self.stamp}-2-audio.mp3")
        paths = artifact_paths(second, grouped=True)
        self.assertEqual(paths["microphone"].name, f"{self.stamp}-2-microphone.flac")
        self.assertEqual(paths["session"].name, f"{self.stamp}-2-session.json")

    def test_existing_recording_is_not_changed(self):
        first = choose_output_path("")
        contents = b"existing recording"
        first.write_bytes(contents)
        second = choose_output_path("")
        self.assertNotEqual(first.parent, second.parent)
        self.assertEqual(first.read_bytes(), contents)

    def test_existing_file_at_folder_path_is_not_changed(self):
        self.recordings.mkdir(parents=True)
        existing = self.recordings / self.folder_name
        existing.write_bytes(b"existing file")
        output = choose_output_path("")
        self.assertEqual(output.parent.name, "2026-09-07_10-23-13-2-audio-rec")
        self.assertEqual(existing.read_bytes(), b"existing file")

    def test_explicit_output_keeps_its_path_and_collision_handling(self):
        requested = self.directory / "custom" / "interview.mp3"
        first = choose_output_path(str(requested))
        self.assertEqual(first, requested)
        artifact_paths(first)["desktop"].write_bytes(b"existing desktop track")
        second = choose_output_path(str(requested))
        self.assertEqual(second, requested.with_name("interview-2.mp3"))
        self.assertFalse(self.recordings.exists())


if __name__ == "__main__":
    unittest.main()
