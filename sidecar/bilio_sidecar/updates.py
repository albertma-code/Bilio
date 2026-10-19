"""Read public release metadata only; never download or execute an update."""
import json
import urllib.request
import urllib.error
from urllib.parse import quote
from packaging.version import Version, InvalidVersion
from . import __version__


def check_latest():
    request=urllib.request.Request('https://api.github.com/repos/albertma-code/Bilio/releases/latest',
        headers={'User-Agent':'Bilio-update-check','Accept':'application/vnd.github+json'})
    try:
        with urllib.request.urlopen(request,timeout=10) as response:
            release=json.loads(response.read(512*1024))
        tag=release.get('tag_name','')
        if not isinstance(tag,str) or len(tag)>100:return {'status':'unavailable'}
        latest=Version(tag.lstrip('v'));current=Version(__version__)
        if latest.is_prerelease or release.get('draft') or release.get('prerelease'):
            return {'status':'no_release'}
        return {'status':'available' if latest>current else 'current' if latest==current else 'local_ahead',
                'version':str(latest),'url':'https://github.com/albertma-code/Bilio/releases/tag/'+quote(tag,safe='')}
    except urllib.error.HTTPError as error:
        return {'status':'no_release' if error.code==404 else 'unavailable'}
    except (OSError,ValueError,InvalidVersion):
        return {'status':'unavailable'}
