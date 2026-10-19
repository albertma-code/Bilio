import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from email.message import Message
from bilio_sidecar.source import normalize_source, resolve_short_link


class SourceTest(unittest.TestCase):
    def test_share_text_extracts_one_url_and_multiple_requires_selection(self):
        self.assertEqual(normalize_source('【分享视频】 https://www.bilibili.com/video/BVtest?spm_id=x'),
                         'https://www.bilibili.com/video/BVtest')
        with self.assertRaisesRegex(ValueError,'每次选择一个'):
            normalize_source('分享 https://b23.tv/one https://b23.tv/two')

    def test_short_redirect_never_requests_external_or_local_target(self):
        for location in ('http://127.0.0.1/private','file:///tmp/test','https://bilibili.com.evil.test/test'):
            headers=Message();headers['Location']=location
            with patch('bilio_sidecar.source.urllib.request.build_opener') as build:
                build.return_value.open.side_effect=HTTPError('https://b23.tv/test',302,'redirect',headers,None)
                with self.assertRaises(ValueError):resolve_short_link('https://b23.tv/test')
                self.assertEqual(build.return_value.open.call_count,1)

    def test_official_redirect_retains_part_identity_only(self):
        headers=Message();headers['Location']='https://www.bilibili.com/video/BVtest?p=2&spm=x'
        with patch('bilio_sidecar.source.urllib.request.build_opener') as build:
            build.return_value.open.side_effect=HTTPError('https://b23.tv/test',302,'redirect',headers,None)
            self.assertEqual(resolve_short_link('https://b23.tv/test'),'https://www.bilibili.com/video/BVtest?p=2')
