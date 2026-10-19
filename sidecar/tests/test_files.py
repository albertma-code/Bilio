import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from bilio_sidecar.files import workspace, publish, download_safely


class FileSafetyTest(unittest.TestCase):
    def test_verified_reuse_and_explicit_atomic_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            root,work=workspace(directory,'a'*32)
            target=root/'video.mp4';target.write_bytes(b'old')
            source=work/'video.mp4';source.write_bytes(b'old')
            self.assertEqual(publish(source,root,work,'reuse_verified'),str(target))
            self.assertEqual(len(list(root.glob('*.mp4'))),1)
            source.write_bytes(b'new')
            self.assertEqual(publish(source,root,work,'replace'),str(target))
            self.assertEqual(target.read_bytes(),b'new')
    def test_collision_keeps_both_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as directory:
            root, work = workspace(directory, 'a' * 32)
            (root / 'video.mp4').write_bytes(b'original')
            source = work / 'video.mp4';source.write_bytes(b'new')
            result = publish(source, root, work)
            self.assertEqual(Path(result).name, 'video (2).mp4')
            self.assertEqual((root / 'video.mp4').read_bytes(), b'original')
            self.assertEqual(Path(result).read_bytes(), b'new')

    def test_no_space_and_symlink_workspaces_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('bilio_sidecar.files.shutil.disk_usage') as usage:
                usage.return_value.free = 1
                with self.assertRaisesRegex(OSError, '磁盘空间不足'):
                    workspace(directory, 'a' * 32)
            os.symlink(directory, os.path.join(directory, '.bilio-partials'))
            with self.assertRaisesRegex(ValueError, '临时目录异常'):
                workspace(directory, 'a' * 32)

    def test_empty_or_foreign_output_never_publishes(self):
        with tempfile.TemporaryDirectory() as directory:
            root, work = workspace(directory, 'a' * 32)
            empty = work / 'empty.mp4';empty.touch()
            outside = root / 'outside.mp4';outside.write_bytes(b'x')
            for path in (empty, outside):
                with self.assertRaises(ValueError): publish(path, root, work)

    def test_restart_uses_same_workspace_and_keeps_partial_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root, work = workspace(directory, 'a' * 32)
            (work / 'video.part').write_bytes(b'partial')
            seen = []
            def download(**config):
                current = Path(config['output_dir'])
                seen.append((current / 'video.part').read_bytes())
                result = current / 'video.mp4';result.write_bytes(b'complete')
                return str(result)
            with patch('bilio_sidecar.files.verify_media'):
                result = download_safely(download,work_id='a'*32,output_dir=directory,
                    progress_callback=lambda data:None,cancel_event=threading.Event())
            self.assertEqual(seen,[b'partial'])
            self.assertEqual(Path(result).read_bytes(),b'complete')
            self.assertTrue((work / 'video.part').exists())
