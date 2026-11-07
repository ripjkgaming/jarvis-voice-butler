from PIL import Image
import pytest
from wa_image import inspect_png, verify_image


def test_png_fingerprint_detects_changed_media(tmp_path):
    p = tmp_path / 'image.png'
    Image.new('RGB', (12, 8), 'red').save(p)
    snapshot = inspect_png(str(p))
    assert snapshot['width'] == 12 and verify_image(snapshot)
    Image.new('RGB', (12, 8), 'blue').save(p)
    assert not verify_image(snapshot)


def test_rejects_invalid_png(tmp_path):
    p = tmp_path / 'image.png'
    p.write_bytes(b'Not a real PNG')
    with pytest.raises(ValueError):
        inspect_png(str(p))

@pytest.mark.asyncio
async def test_image_approval_uses_image_sender_only(tmp_path, monkeypatch):
    import wa_approvals
    from system import whatsapp as wa
    from types import SimpleNamespace
    import time
    p = tmp_path / 'image.png'
    Image.new('RGB', (12, 8), 'red').save(p)
    path = tmp_path / 'drafts.json'
    row = wa_approvals.queue(path, 'Group', '', image=inspect_png(str(p)))
    lines = []
    sent = []
    class Speech:
        interrupted = False
        def exception(self): return None
        async def wait_for_playout(self): pass
    class Session:
        def say(self, text, **kwargs):
            lines.append(text)
            return Speech()
    async def resolve(name): return {'ok': True, 'name': name, 'jid': '123@g.us'}
    async def text_send(*args): raise AssertionError('text sender must not send images')
    async def image_send(target, caption, image, authorized):
        assert authorized() and caption == '' and verify_image(image)
        sent.append(target)
        return {'ok': True}
    monkeypatch.setattr(wa, 'send_approved_image', image_send)
    gate = wa_approvals.SpokenApproval(Session(), 'owner', lambda: 'owner', resolve=resolve, send=text_send)
    reviewed = await gate.review(path, row['id'])
    assert 'Awaiting' in reviewed['say'] and 'PNG image' in lines[0]
    gate.user_state(SimpleNamespace(new_state='speaking'))
    gate.user_input(SimpleNamespace(transcript=gate.confirmation, is_final=True, created_at=time.time(), item_id='1'))
    await gate.drain()
    assert sent == [{'name': 'Group', 'jid': '123@g.us'}]

@pytest.mark.asyncio
async def test_changed_image_refuses_review(tmp_path):
    import wa_approvals
    p = tmp_path / 'image.png'
    Image.new('RGB', (12, 8), 'red').save(p)
    path = tmp_path / 'drafts.json'
    row = wa_approvals.queue(path, 'Group', '', image=inspect_png(str(p)))
    Image.new('RGB', (12, 8), 'green').save(p)
    class Session: pass
    gate = wa_approvals.SpokenApproval(Session(), 'owner', lambda: 'owner')
    result = await gate.review(path, row['id'])
    assert 'image changed' in result['say'] and not gate.confirmation
