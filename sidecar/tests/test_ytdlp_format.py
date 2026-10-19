import unittest

from bilio_sidecar.ytdlp import _best_video_format, _format_label, _format_with_audio, quality_selector, _apply_cookies, _shape
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, ExtractorError


class FormatWithAudioTest(unittest.TestCase):
    def test_exact_video_format_id_gets_best_audio(self):
        self.assertEqual(_format_with_audio("30080"), "30080+ba/30080[acodec!=none]")

    def test_complete_format_expression_is_preserved(self):
        self.assertEqual(
            _format_with_audio("bv*[height<=1080]+ba/best"),
            "bv*[height<=1080]+ba/best",
        )

    def test_fallback_expression_is_preserved(self):
        self.assertEqual(_format_with_audio("bestvideo/best"), "bestvideo/best")

    def test_empty_format_stays_empty(self):
        self.assertEqual(_format_with_audio("   "), "")


class BestVideoFormatTest(unittest.TestCase):
    def test_picks_highest_quality_video_and_ignores_audio(self):
        best = _best_video_format([
            {"format_id": "30280", "vcodec": "none", "height": None, "quality": None},
            {"format_id": "30032", "vcodec": "avc1", "height": 480, "width": 854, "quality": 32},
            {"format_id": "30080", "vcodec": "avc1", "height": 1080, "width": 1920, "quality": 80},
        ])
        self.assertIsNotNone(best)
        self.assertEqual(best["format_id"], "30080")

    def test_format_label_prefers_bilibili_format_name(self):
        self.assertEqual(
            _format_label({"format": "1080P 高清", "format_note": None, "height": 1080}),
            "1080P 高清",
        )


class StrictSelectionTest(unittest.TestCase):
    def test_anonymous_is_supported_and_unknown_browser_is_not_silently_ignored(self):
        options = {}
        _apply_cookies(options, None)
        self.assertNotIn("cookiesfrombrowser", options)
        with self.assertRaises(ValueError):
            _apply_cookies(options, "unknown")

    def test_episode_duration_is_not_blanket_discarded(self):
        info = _shape({"extractor": "BiliBiliBangumi", "duration": 3515.731,
                       "episode_number": 1, "series": "测试番剧"})
        self.assertEqual(info["duration"], 3515.731)
        self.assertIn("测试番剧", info["display_title"])
    def select(self, policy, formats):
        with YoutubeDL({"quiet": True, "no_warnings": True, "logger": self,
                        "format": quality_selector(policy)}) as ydl:
            return ydl.process_ie_result({"id": "test", "title": "test", "extractor": "mock", "formats": formats}, download=False)

    def debug(self, message): pass
    def warning(self, message): pass
    def error(self, message): pass

    def video(self, name="high", height=1080, audio="aac"):
        return {"format_id": name, "url": "https://example.invalid/test.mp4", "ext": "mp4",
                "height": height, "width": height * 16 // 9, "vcodec": "avc1", "acodec": audio}

    def test_height_limit_never_falls_back_to_higher_quality(self):
        with self.assertRaises((DownloadError, ExtractorError)):
            self.select({"mode": "max_height", "height": 720}, [self.video()])

    def test_missing_exact_format_does_not_download_something_else(self):
        with self.assertRaises((DownloadError, ExtractorError)):
            self.select({"mode": "exact", "format_id": "missing"}, [self.video()])

    def test_exact_video_only_requires_audio(self):
        with self.assertRaises((DownloadError, ExtractorError)):
            self.select({"mode": "exact", "format_id": "video"}, [self.video("video", audio="none")])

    def test_exact_muxed_stream_remains_supported(self):
        info = self.select({"mode": "exact", "format_id": "mux"}, [self.video("mux")])
        self.assertEqual(info["format_id"], "mux")

    def test_height_limit_with_muxed_fallback_is_still_bounded(self):
        info = self.select({"mode": "max_height", "height": 720}, [self.video("low", 480), self.video()])
        self.assertEqual(info["height"], 480)

    def test_injected_selector_is_rejected(self):
        with self.assertRaises(ValueError):
            quality_selector({"mode": "exact", "format_id": "bad/best"})

    def test_platform_quality_caps_do_not_confuse_widescreen_4k_with_1080(self):
        formats=[{**self.video('1080',816),'quality':80},
                 {**self.video('4k',1632),'width':3840,'quality':120},
                 {**self.video('8k',4320),'quality':127}]
        self.assertEqual(self.select({'mode':'max_quality','quality':80},formats)['format_id'],'1080')
        self.assertEqual(self.select({'mode':'max_quality','quality':120},formats)['format_id'],'4k')


if __name__ == "__main__":
    unittest.main()
