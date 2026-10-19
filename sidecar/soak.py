"""Opt-in local engine soak. Uses synthetic media and an isolated temp DB."""
import argparse
import http.server
import json
import hashlib
import signal
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from imageio_ffmpeg import get_ffmpeg_exe
from bilio_sidecar.jobs import DownloadQueue, TERMINAL
from bilio_sidecar.storage import TaskStorage
from bilio_sidecar.worker import isolated_download
from bilio_sidecar.ytdlp import parse_url


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--seconds',type=int,required=True);parser.add_argument('--report',required=True)
    args=parser.parse_args();started=time.monotonic();counts={'completed':0,'failed':0,'cancelled':0,'parses':0,'cycles':0}
    def digest():
        hasher=hashlib.sha256()
        for path in sorted((Path(__file__).parent/'bilio_sidecar').glob('*.py')):
            hasher.update(path.name.encode());hasher.update(path.read_bytes())
        return hasher.hexdigest()
    initial_digest=digest()
    report={'fixture':'local synthetic media; not Bilibili acceptance','requested_seconds':args.seconds,'passed':False,'source_sha256':initial_digest}
    with tempfile.TemporaryDirectory(prefix='bilio-soak-') as directory:
        fixture=Path(directory)/'fixture.mp4'
        subprocess.run([get_ffmpeg_exe(),'-v','error','-f','lavfi','-i','color=c=gray:s=160x90:r=10','-f','lavfi','-i','sine=frequency=440','-t','0.5','-c:v','libx264','-c:a','aac',str(fixture)],check=True)
        payload=fixture.read_bytes()
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*unused):pass
            def do_HEAD(self):self.respond(False)
            def do_GET(self):self.respond(True)
            def respond(self,body):
                self.send_response(200);self.send_header('Content-Type','video/mp4');self.send_header('Content-Length',str(len(payload)));self.end_headers()
                if body:
                    try:self.wfile.write(payload)
                    except (BrokenPipeError,ConnectionResetError):pass
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        storage=TaskStorage(str(Path(directory)/'tasks.sqlite3'));jobs=DownloadQueue(isolated_download,lambda event:None,2,storage)
        counter=0
        try:
            while time.monotonic()-started<args.seconds:
                cycle=time.monotonic();ids=[]
                jobs.set_concurrency(counts['cycles']%3+1)
                for index in range(6):
                    counter+=1;ids.append(counter)
                    jobs.submit(counter,{'url':f'http://127.0.0.1:{server.server_port}/fixture.mp4?job={counter}',
                        'title':'Synthetic soak fixture','policy':{'mode':'exact','format_id':'missing'} if index==5 else {'mode':'best'},'output_dir':str(Path(directory)/'output')})
                jobs.pause(ids[4]);jobs.cancel(ids[3]);jobs.resume(ids[4])
                info=parse_url(f'http://127.0.0.1:{server.server_port}/fixture.mp4');assert info['formats'];counts['parses']+=1
                deadline=time.monotonic()+90
                while any(t['status'] not in TERMINAL for t in jobs.snapshots()):
                    if time.monotonic()>deadline:raise RuntimeError('queue stalled during soak')
                    time.sleep(.1)
                for task in jobs.snapshots():
                    counts[task['status']]+=1
                    if task['status']=='completed':
                        assert Path(task['filepath']).read_bytes()==payload
                assert sum(t['status']=='failed' for t in jobs.snapshots())==1
                jobs.clear_finished();counts['cycles']+=1
                print(json.dumps({'elapsed_seconds':round(time.monotonic()-started),'counts':counts}),flush=True)
                remaining=min(60-(time.monotonic()-cycle),args.seconds-(time.monotonic()-started))
                if remaining>0:time.sleep(remaining)
            report['passed']=True
        except Exception as error:
            report['failure_type']=type(error).__name__
            raise
        finally:
            jobs.close();server.shutdown();server.server_close()
            report.update(elapsed_seconds=round(time.monotonic()-started,3),counts=counts)
            report['source_unchanged']=digest()==initial_digest
            report['passed']=report['passed'] and report['source_unchanged']
            Path(args.report).write_text(json.dumps(report,indent=2)+'\n')
    print('SOAK PASS' if report['passed'] else 'SOAK NOT ACCEPTED',flush=True)


if __name__=='__main__':
    import multiprocessing
    multiprocessing.freeze_support();main()
