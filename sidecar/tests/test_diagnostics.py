import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from bilio_sidecar.errors import user_error
from bilio_sidecar.updates import check_latest


class DiagnosticTest(unittest.TestCase):
    def test_report_contains_only_counts_not_user_fields_or_raw_codes(self):
        from bilio_sidecar import commands
        secret='synthetic-secret-marker'
        queue=SimpleNamespace(storage=None,snapshots=lambda:[{'status':'failed','errorCode':secret,
            'errorMessage':secret,'config':{'title':secret,'url':'https://example.invalid/?token='+secret,'account_ref':secret,'output_dir':'/Users/'+secret}}])
        with patch.object(commands,'_downloads',queue):
            result=list(commands.dispatch({'id':1,'cmd':'diagnostics'}))[0]['data']
        self.assertNotIn(secret,json.dumps(result));self.assertEqual(result['errors'],{'unknown':1})

    def test_errors_redact_credentials_signed_urls_and_user_path(self):
        result=user_error(RuntimeError('SESSDATA=test-secret https://media.invalid/?token=secret /Users/example/private'))
        self.assertNotIn('test-secret',result);self.assertNotIn('token=secret',result);self.assertNotIn('/Users/example',result)

    def test_release_check_handles_new_current_offline_without_installing(self):
        class Response:
            def __init__(self,version):self.version=version
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return json.dumps({'tag_name':self.version}).encode()
        for version,status in [('v99.0.0','available'),('v0.4.0','current'),('v0.1.0','local_ahead')]:
            with patch('urllib.request.urlopen',return_value=Response(version)):
                self.assertEqual(check_latest()['status'],status)
        with patch('urllib.request.urlopen',side_effect=OSError('network')):
            self.assertEqual(check_latest(),{'status':'unavailable'})
