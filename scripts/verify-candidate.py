"""Write a non-sensitive manifest for a locally built macOS candidate."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import platform
import plistlib
import subprocess
import tomllib


def digest(path):
    with path.open('rb') as file:return hashlib.file_digest(file,'sha256').hexdigest()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--report',required=True);args=parser.parse_args()
    root=Path(__file__).resolve().parent.parent
    package=json.loads((root/'package.json').read_text())
    tauri=json.loads((root/'src-tauri/tauri.conf.json').read_text())
    cargo=tomllib.loads((root/'src-tauri/Cargo.toml').read_text())
    tree=ast.parse((root/'sidecar/bilio_sidecar/__init__.py').read_text())
    engine=next(node.value.value for node in tree.body if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='__version__' for t in node.targets))
    app=root/'src-tauri/target/release/bundle/macos/Bilio.app'
    plist=plistlib.loads((app/'Contents/Info.plist').read_bytes())
    versions={'frontend':package['version'],'tauri':tauri['version'],'rust':cargo['package']['version'],'engine':engine,'bundle':plist['CFBundleShortVersionString']}
    assert len(set(versions.values()))==1,versions
    subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(app)],check=True)
    source=hashlib.sha256()
    paths=[root/'package.json',root/'package-lock.json',root/'requirements.txt',root/'src-tauri/Cargo.toml',root/'src-tauri/Cargo.lock',root/'src-tauri/tauri.conf.json']
    for folder in ('src','src-tauri/src','sidecar/bilio_sidecar'):
        paths.extend(path for path in (root/folder).rglob('*') if path.suffix in ('.ts','.vue','.css','.rs','.py'))
    for path in sorted(paths):source.update(str(path.relative_to(root)).encode());source.update(path.read_bytes())
    git=subprocess.run(['git','rev-parse','HEAD'],cwd=root,capture_output=True,text=True,check=True).stdout.strip()
    dirty=bool(subprocess.run(['git','status','--porcelain'],cwd=root,capture_output=True,text=True,check=True).stdout)
    report={'versions':versions,'architecture':platform.machine(),'macos':platform.mac_ver()[0],
        'git_head':git,'dirty_worktree':dirty,'source_sha256':source.hexdigest(),
        'native_sha256':digest(app/'Contents/MacOS/bilio'),'engine_sha256':digest(app/'Contents/MacOS/bilio-sidecar'),
        'distribution':'local ad-hoc; not notarized; not public-release accepted','signature_verified':True}
    Path(args.report).write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
