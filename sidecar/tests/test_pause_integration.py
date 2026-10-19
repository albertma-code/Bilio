"""Real spawned worker, HTTP ranges and ffmpeg; no site or account access."""
import http.server
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from imageio_ffmpeg import get_ffmpeg_exe
from bilio_sidecar.jobs import DownloadQueue
from bilio_sidecar.worker import isolated_download


class PauseIntegrationTest(unittest.TestCase):
    def wait(self, predicate, seconds=20):
        deadline=time.monotonic()+seconds
        while not predicate():
            if time.monotonic()>deadline:self.fail('worker did not reach expected state')
            time.sleep(.02)

    def test_pause_stops_writes_and_resumes_exact_media_using_http_range(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture=Path(directory)/'fixture.mp4'
            subprocess.run([get_ffmpeg_exe(),'-v','error','-f','lavfi','-i','testsrc2=size=640x360:rate=20',
                '-f','lavfi','-i','sine=frequency=440','-t','5','-c:v','libx264','-crf','15','-c:a','aac',str(fixture)],check=True)
            payload=fixture.read_bytes();ranges=[]
            class Handler(http.server.BaseHTTPRequestHandler):
                def log_message(self,*args):pass
                def do_HEAD(self):
                    self.send_response(200);self.send_header('Content-Type','video/mp4');self.send_header('Content-Length',str(len(payload)));self.end_headers()
                def do_GET(self):
                    header=self.headers.get('Range')
                    offset=int(header.split('=')[1].split('-')[0]) if header else 0;ranges.append(offset)
                    self.send_response(206 if offset else 200)
                    self.send_header('Content-Type','video/mp4');self.send_header('Content-Length',str(len(payload)-offset))
                    self.send_header('Accept-Ranges','bytes')
                    if offset:self.send_header('Content-Range',f'bytes {offset}-{len(payload)-1}/{len(payload)}')
                    self.end_headers()
                    try:
                        for start in range(offset,len(payload),8192):
                            self.wfile.write(payload[start:start+8192]);self.wfile.flush();time.sleep(.015)
                    except (BrokenPipeError,ConnectionResetError):pass
            server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
            threading.Thread(target=server.serve_forever,daemon=True).start()
            jobs=DownloadQueue(isolated_download,lambda message:None,1)
            try:
                output=Path(directory)/'output'
                jobs.submit(1,{'url':f'http://127.0.0.1:{server.server_port}/fixture.mp4','title':'Range fixture','policy':{'mode':'best'},'output_dir':str(output)})
                self.wait(lambda:jobs.snapshots()[0]['downloadedBytes']>32768)
                jobs.pause(1);self.wait(lambda:jobs.snapshots()[0]['status']=='paused',5)
                part_files=list(output.rglob('*.part'));self.assertTrue(part_files)
                sizes=[p.stat().st_size for p in part_files];time.sleep(.2)
                self.assertEqual(sizes,[p.stat().st_size for p in part_files])
                self.assertFalse(list(output.glob('*.mp4')))
                jobs.resume(1)
                self.wait(lambda:jobs.snapshots()[0]['status'] in ('completed','failed'))
                task=jobs.snapshots()[0]
                self.assertEqual(task['status'],'completed',task.get('errorMessage'))
                self.assertEqual(Path(task['filepath']).read_bytes(),payload)
                self.assertTrue(any(offset>0 for offset in ranges),ranges)
            finally:
                jobs.close();server.shutdown();server.server_close()
