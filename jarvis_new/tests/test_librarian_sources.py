"""Transport/parser boundaries, mocked Google auth and HTTP only."""
import io
import json
import sys
import types
import unittest
import urllib.error
from unittest.mock import patch

from librarian_sources import GoogleReadTransport, SourceError, parse_doc, page_chunks, text_chunks, MAX_BYTES


class TransportTests(unittest.TestCase):
    def test_get_only_existing_account_and_bounded_media(self):
        token_calls=[]
        requests=[]
        google=types.SimpleNamespace(DRIVE_BASE='https://www.googleapis.com/drive/v3',
            GoogleError=type('GoogleError',(Exception,),{}),
            access_token=lambda **kwargs:token_calls.append(kwargs) or 'FAKE-token')
        class Opener:
            def open(self, req, timeout=None):
                requests.append(req)
                return io.BytesIO(json.dumps({'id':'fixture'}).encode())
        with patch.dict(sys.modules,{'google_api':google}), patch('urllib.request.build_opener',return_value=Opener()):
            value=GoogleReadTransport()('school','files/fixture',{'fields':'id'})
        self.assertEqual(value,{'id':'fixture'})
        self.assertEqual(token_calls,[{'account':'school'}])
        self.assertEqual(requests[0].get_method(),'GET')
        self.assertIsNone(requests[0].data)
        self.assertNotIn('token',requests[0].full_url)

    def test_http_errors_are_sanitized_and_distinct(self):
        google=types.SimpleNamespace(DRIVE_BASE='https://www.googleapis.com/drive/v3',
            GoogleError=type('GoogleError',(Exception,),{}),access_token=lambda **kwargs:'FAKE')
        for code,status in [(401,'unauthenticated'),(403,'denied'),(404,'not_found'),(429,'unavailable')]:
            class Opener:
                def open(self,req,timeout=None):
                    raise urllib.error.HTTPError(req.full_url,code,'sensitive server error',{},io.BytesIO(b'personal details'))
            with patch.dict(sys.modules,{'google_api':google}), patch('urllib.request.build_opener',return_value=Opener()):
                with self.assertRaises(SourceError) as error:
                    GoogleReadTransport()('personal','files/fixture',{'fields':'id'})
                self.assertEqual(error.exception.status,status)
                self.assertNotIn('personal details',str(error.exception))
                self.assertNotIn('sensitive',str(error.exception))

    def test_transport_cap_uses_bounded_read(self):
        calls=[]
        google=types.SimpleNamespace(DRIVE_BASE='https://www.googleapis.com/drive/v3',
            GoogleError=type('GoogleError',(Exception,),{}),access_token=lambda **kwargs:'FAKE')
        class Response(io.BytesIO):
            def read(self,n=-1):
                calls.append(n)
                return b'x'*(MAX_BYTES+1)
        class Opener:
            def open(self,*a,**k): return Response()
        with patch.dict(sys.modules,{'google_api':google}), patch('urllib.request.build_opener',return_value=Opener()):
            with self.assertRaises(SourceError) as error:
                GoogleReadTransport()('personal','files/fixture',{'alt':'media'},media=True)
        self.assertEqual(error.exception.status,'too_large')
        self.assertEqual(calls,[MAX_BYTES+1])

    def test_redirection_refused(self):
        handlers=[]
        def builder(*args):
            handlers.extend(args)
            return types.SimpleNamespace(open=lambda *a,**k:io.BytesIO(b'{}'))
        google=types.SimpleNamespace(DRIVE_BASE='https://www.googleapis.com/drive/v3',
            GoogleError=type('GoogleError',(Exception,),{}),access_token=lambda **kwargs:'FAKE')
        with patch.dict(sys.modules,{'google_api':google}),patch('urllib.request.build_opener',builder):
            GoogleReadTransport()('personal','files/fixture',{})
        self.assertIsNone(handlers[0]().redirect_request(None,None,302,'',{},'https://evil/'))

    def test_bounded_parsers_fail_instead_of_truncating_silently(self):
        for fn,arg in [(text_chunks,'x'*200001),(page_chunks,['x']*201),(page_chunks,['',''])]:
            with self.assertRaises(SourceError): fn(arg)
        payload={'body':{'content':[{'paragraph':{'elements':[{'textRun':{'content':'x'*200001}}]}}]}}
        with self.assertRaises(SourceError): parse_doc(payload)

    def test_doc_tables_keep_structural_paths(self):
        doc={'body':{'content':[{'table':{'tableRows':[{'tableCells':[{'content':[{'startIndex':3,'endIndex':7,'paragraph':{'elements':[{'textRun':{'content':'cell'}}]}}]}]}]}}]}}
        chunks=parse_doc(doc)
        self.assertEqual(chunks[0]['text'],'cell')
        self.assertEqual(chunks[0]['locator']['structural_path'],'/0/table/0/0/0')
