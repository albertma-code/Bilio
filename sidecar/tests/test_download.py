"""Real yt-dlp + bundled ffmpeg on locally generated media, no Bilibili access."""
import copy
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from imageio_ffmpeg import get_ffmpeg_exe
from yt_dlp import YoutubeDL as RealYoutubeDL
from bilio_sidecar.ytdlp import download_video, DownloadProgressTracker


class LocalMediaTest(unittest.TestCase):
    def test_split_video_audio_merge_returns_playable_final_path(self):
        with tempfile.TemporaryDirectory() as directory:
            video = os.path.join(directory, "video.mp4")
            audio = os.path.join(directory, "audio.m4a")
            ffmpeg = get_ffmpeg_exe()
            subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                "color=c=teal:s=160x90:r=10", "-t", "0.3", "-an", "-c:v", "libx264", video], check=True)
            subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                "sine=frequency=440:sample_rate=44100", "-t", "0.3", "-c:a", "aac", audio], check=True)
            fixture = {"id": "local", "title": "Local fixture", "extractor": "mock", "webpage_url": "https://example.invalid/local",
                "formats": [
                    {"format_id": "video", "url": Path(video).as_uri(), "ext": "mp4", "height": 90,
                     "width": 160, "vcodec": "avc1", "acodec": "none"},
                    {"format_id": "audio", "url": Path(audio).as_uri(), "ext": "m4a", "vcodec": "none", "acodec": "aac"}]}

            class LocalYoutubeDL(RealYoutubeDL):
                def __init__(self, opts):
                    # Local fixture transport is enabled only in this test.
                    super().__init__({**opts, "enable_file_urls": True})

                def extract_info(self, url, download=False, **kwargs):
                    return self.process_ie_result(copy.deepcopy(fixture), download=download)

            progress = []
            with patch("bilio_sidecar.ytdlp.YoutubeDL", LocalYoutubeDL):
                path = download_video("https://example.invalid/local", {"mode": "exact", "format_id": "video"},
                    os.path.join(directory, "output"), progress.append, threading.Event())
            self.assertTrue(path and os.path.isfile(path))
            probe = subprocess.run([ffmpeg, "-i", path, "-f", "null", "-"], capture_output=True, text=True)
            self.assertEqual(probe.returncode, 0, probe.stderr)
            self.assertIn("Video:", probe.stderr)
            self.assertIn("Audio:", probe.stderr)
            self.assertTrue(any(p.get("status") == "processing" for p in progress))
            percents = [p["overall_percent"] for p in progress if p.get("overall_percent") is not None]
            self.assertEqual(percents, sorted(percents))
            self.assertTrue(all("info_dict" not in p for p in progress))
            fixture["formats"].append({**fixture["formats"][0], "format_id": "video-alt"})
            with patch("bilio_sidecar.ytdlp.YoutubeDL", LocalYoutubeDL):
                another = download_video("https://example.invalid/local", {"mode": "exact", "format_id": "video-alt"},
                    os.path.join(directory, "output"), lambda data: None, threading.Event())
            self.assertNotEqual(path, another, "different quality reused an existing final file")
            self.assertTrue(os.path.isfile(path) and os.path.isfile(another))


class ProgressTest(unittest.TestCase):
    def test_video_audio_and_processing_have_monotonic_aggregate_progress(self):
        messages = []
        tracker = DownloadProgressTracker({"requested_formats": [
            {"format_id": "video", "filesize": 80}, {"format_id": "audio", "filesize": 20}]}, messages.append)
        tracker.hook({"info_dict": {"format_id": "video", "url": "secret"}, "downloaded_bytes": 80, "total_bytes": 80})
        tracker.hook({"info_dict": {"format_id": "audio"}, "downloaded_bytes": 2, "total_bytes": 20})
        tracker.hook({"info_dict": {"format_id": "audio"}, "downloaded_bytes": 20, "total_bytes": 20})
        tracker.publish("processing")
        self.assertEqual([m["overall_percent"] for m in messages], [79, 81, 99, 99])
        self.assertEqual(messages[-1]["downloaded_bytes"], 100)
        self.assertNotIn("secret", str(messages))
