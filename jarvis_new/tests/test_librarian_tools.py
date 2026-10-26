"""Wrapper tests use host-module fakes, never LiveKit/network/private sessions."""
import asyncio
import sys
import threading
import types
import unittest
from unittest.mock import patch

from system.librarian_tools import LibrarianTools


class LocalError(Exception): pass


class ToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # This managed executor does not reliably wake a selector from worker
        # shutdown callbacks (also reproducible without librarian code). Keep a
        # bounded test-loop tick so normal asyncio executor cleanup can settle.
        loop=asyncio.get_running_loop()
        def tick():
            if not loop.is_closed(): loop.call_later(.01,tick)
        loop.call_later(.01,tick)
        await asyncio.to_thread(lambda: None)
        self.phone=types.SimpleNamespace(private=lambda:False,guest_required=lambda:False,
                                         approval_ticket=lambda:None,check_approval_effect=lambda *a,**k:None)
        self.system=types.SimpleNamespace(require_local=lambda:None,LocalSystemError=LocalError)
        self.patcher=patch.dict(sys.modules,{'phone_session':self.phone,'system':self.system})
        self.patcher.start()

    async def asyncTearDown(self): self.patcher.stop()

    async def test_private_guest_and_nonlocal_fail_before_read(self):
        calls=[]
        service=types.SimpleNamespace(find=lambda *a,**k:calls.append(1))
        tools=LibrarianTools(service)
        for field in ('private','guest_required'):
            setattr(self.phone,field,lambda:True)
            out=await tools.librarian_find('secret')
            self.assertEqual(out['status'],'denied')
            setattr(self.phone,field,lambda:False)
        def forbidden(): raise LocalError('not local')
        self.system.require_local=forbidden
        self.assertEqual((await tools.librarian_find('secret'))['status'],'denied')
        self.assertEqual(calls,[])

    async def test_revocation_during_read_prevents_result(self):
        def find(*args,**kwargs):
            self.phone.guest_required=lambda:True
            return {'status':'ok','say':'secret'}
        result=await LibrarianTools(types.SimpleNamespace(find=find)).librarian_find('secret')
        self.assertEqual(result['status'],'denied')
        self.assertNotIn('secret',str(result))

    async def test_cancel_sets_cooperative_event(self):
        entered=threading.Event()
        release=threading.Event()
        seen=[]
        def find(*args,cancel=None,safety=None):
            entered.set()
            release.wait(1)
            seen.append(cancel.is_set())
            return {'status':'cancelled'}
        task=asyncio.create_task(LibrarianTools(types.SimpleNamespace(find=find)).librarian_find('test'))
        for _ in range(100):
            if entered.is_set(): break
            await asyncio.sleep(.001)
        self.assertTrue(entered.is_set())
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        release.set()
        for _ in range(100):
            if seen: break
            await asyncio.sleep(.001)
        self.assertEqual(seen,[True])

    async def test_wrapper_only_exports_three_read_tools(self):
        tools=LibrarianTools(types.SimpleNamespace())
        fake=types.ModuleType('livekit.agents')
        fake.function_tool=lambda:lambda fn:fn
        with patch.dict(sys.modules,{'livekit':types.ModuleType('livekit'),'livekit.agents':fake}):
            names=[tool.__name__ for tool in tools.tools]
        self.assertEqual(names,['librarian_find','librarian_answer','librarian_duplicates'])
