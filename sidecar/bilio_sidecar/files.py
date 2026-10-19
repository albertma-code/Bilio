"""Per-task staging and exclusive final publication on the selected volume."""
import os
import re
import shutil
import tempfile
import subprocess
import hashlib
from pathlib import Path

MIN_FREE = 256 * 1024 * 1024


def workspace(output_dir, work_id):
    if not isinstance(work_id, str) or not re.fullmatch(r"[a-f0-9]{32}", work_id):
        raise ValueError("无效的下载工作目录标识")
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise ValueError("保存位置不是文件夹")
    if shutil.disk_usage(root).free < MIN_FREE:
        raise OSError("磁盘空间不足，请至少预留 256 MiB 后重试")
    # Probe actual writes; os.access can lie for ACLs and removed external disks.
    with tempfile.NamedTemporaryFile(dir=root,prefix='.bilio-probe-') as probe:
        linked=probe.name+'.link'
        try:
            os.link(probe.name,linked)
        except OSError as error:
            raise OSError('目录不可写或文件系统不支持安全发布，请选择本机 APFS/HFS+ 目录') from error
        else:
            os.unlink(linked)
    staging = root / ".bilio-partials"
    staging.mkdir(mode=0o700, exist_ok=True)
    if staging.is_symlink() or not staging.is_dir():
        raise ValueError("下载临时目录异常，请选择其他目录")
    work = staging / work_id
    work.mkdir(mode=0o700, exist_ok=True)
    if work.is_symlink() or work.resolve().parent != staging.resolve():
        raise ValueError("下载临时目录不安全，请选择其他目录")
    return root, work


def publish(path, root, work, policy='keep_both'):
    source = Path(path).resolve()
    if source.parent != work.resolve() or not source.is_file() or source.stat().st_size == 0:
        raise ValueError("下载结束，但最终文件为空或不在任务目录中")
    if policy not in ('keep_both','reuse_verified','replace'):
        raise ValueError('无效的文件冲突策略')
    destination=root/source.name
    if policy=='reuse_verified' and destination.is_file() and not destination.is_symlink():
        before=destination.stat()
        with source.open('rb') as a,destination.open('rb') as b:
            identical=hashlib.file_digest(a,'sha256').digest()==hashlib.file_digest(b,'sha256').digest()
        after=destination.stat()
        if identical and (before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns):
            source.unlink();return str(destination)
    with source.open('rb') as file:os.fsync(file.fileno())
    if policy=='replace':
        os.replace(source,destination)
        return str(destination)
    # Hard links are an atomic no-replace operation. No check-then-rename race,
    # and an interrupted publication cannot leave a partly copied final video.
    for index in range(1, 10000):
        name = source.name if index == 1 else f"{source.stem} ({index}){source.suffix}"
        target = root / name
        try:
            os.link(source, target)
        except FileExistsError:
            continue
        except OSError as error:
            raise OSError("无法安全发布文件；请使用支持硬链接的本机磁盘目录，临时文件已保留") from error
        source.unlink()
        return str(target)
    raise ValueError("重名文件过多，请选择其他保存目录")


def verify_media(path):
    from .ytdlp import _get_ffmpeg_path
    ffmpeg = _get_ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError('缺少媒体验证组件，请重新安装 Bilio')
    result = subprocess.run([ffmpeg, '-v', 'error', '-nostdin', '-i', str(path),
        '-map', '0:v:0', '-map', '0:a:0', '-t', '0.1', '-f', 'null', '-'],
        stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError('媒体验证失败，输出缺少可解码的音视频；临时文件已保留，请清理后重试')


def temporary_files(output_dir, work_id, clear=False):
    if not isinstance(work_id,str) or not re.fullmatch(r'[a-f0-9]{32}',work_id):
        return {'file_count':0,'bytes':0}
    root=Path(output_dir).expanduser().resolve()
    staging=root/'.bilio-partials';work=staging/work_id
    if staging.is_symlink() or work.is_symlink():
        raise ValueError('临时目录异常，未执行清理')
    if not work.exists():return {'file_count':0,'bytes':0}
    files=[p for p in work.rglob('*') if p.is_file() and not p.is_symlink()]
    summary={'file_count':len(files),'bytes':sum(p.stat().st_size for p in files)}
    if clear:shutil.rmtree(work)
    return summary


def download_safely(download, *, work_id, output_dir, progress_callback, conflict_policy='keep_both', **config):
    root, work = workspace(output_dir, work_id)
    def progress(data):
        required = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
        remaining = required if data.get('status') == 'processing' else max(0, required - (data.get("downloaded_bytes") or 0))
        if shutil.disk_usage(root).free < remaining + MIN_FREE:
            raise OSError("磁盘空间不足，已保留临时文件；释放空间后继续")
        progress_callback(data)
    path = download(output_dir=str(work), progress_callback=progress, **config)
    if not path:
        raise ValueError("下载结束，但未找到最终文件")
    if config["cancel_event"].is_set():
        _cancel()
    verify_media(path)
    result = publish(path, root, work, conflict_policy)
    try:
        work.rmdir()
    except OSError:
        pass
    return result


def _cancel():
    from .ytdlp import CancelRequested
    raise CancelRequested()
