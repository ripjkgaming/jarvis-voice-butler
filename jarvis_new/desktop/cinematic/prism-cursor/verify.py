#!/usr/bin/env python3
"""Check cursor binaries with the system Xcursor decoder and verify precision."""
import ctypes as C
import hashlib
import json
from pathlib import Path
import struct

from build import ALIASES, ANIMATED, DELAY, FRAMES, OUT, SIZES, hotspot


class Image(C.Structure):
    _fields_ = [(name, C.c_uint) for name in ('version', 'size', 'width', 'height', 'xhot', 'yhot', 'delay')] + [('pixels', C.POINTER(C.c_uint32))]


class Images(C.Structure):
    _fields_ = [('nimage', C.c_int), ('images', C.POINTER(C.POINTER(Image))), ('name', C.c_char_p)]


def verify():
    lib = C.CDLL('libXcursor.so.1')
    lib.XcursorFilenameLoadImages.argtypes = [C.c_char_p, C.c_int]
    lib.XcursorFilenameLoadImages.restype = C.POINTER(Images)
    lib.XcursorImagesDestroy.argtypes = [C.POINTER(Images)]
    decoded = 0
    for kind, aliases in ALIASES.items():
        filename = OUT / 'cursors' / kind
        data = filename.read_bytes()
        magic, header, version, ntoc = struct.unpack_from('<4I', data)
        frames = FRAMES if kind in ANIMATED else 1
        assert (magic, header, version, ntoc) == (0x72756358, 16, 0x10000, len(SIZES) * frames)
        for alias in aliases:
            assert (OUT / 'cursors' / alias).resolve() == filename.resolve()
        for size in SIZES:
            images = lib.XcursorFilenameLoadImages(str(filename).encode(), size)
            assert images, (kind, size, 'decoder rejected file')
            try:
                assert images.contents.nimage == frames
                hashes = set()
                tips = set()
                for i in range(frames):
                    im = images.contents.images[i].contents
                    assert (im.width, im.height, im.xhot, im.yhot, im.delay) == (size, size, *hotspot(kind, size), DELAY)
                    assert 0 <= im.xhot < size and 0 <= im.yhot < size
                    pixels = C.string_at(im.pixels, size * size * 4)
                    assert any(pixels)
                    hashes.add(hashlib.sha256(pixels).hexdigest())
                    # Opaque targeting tip remains identical throughout the animation.
                    tips.add(im.pixels[im.yhot * size + im.xhot])
                    for value in im.pixels[:size * size]:
                        alpha = value >> 24
                        assert all(((value >> shift) & 255) <= alpha for shift in (0, 8, 16)), 'not premultiplied ARGB'
                assert len(hashes) > 1 if kind in ANIMATED else len(hashes) == 1
                if kind in {'left_ptr', 'pointer', 'progress', 'help'}:
                    assert len(tips) == 1 and next(iter(tips)) >> 24 > 180, (kind, size, 'unstable click point')
                decoded += frames
            finally:
                lib.XcursorImagesDestroy(images)
    result = {'roles': len(ALIASES), 'sizes': list(SIZES), 'decoded_images': decoded,
              'aliases': sum(map(len, ALIASES.values())), 'decoder': 'system libXcursor',
              'fixed_hotspots': True, 'stable_targeting_pixels': True,
              'static_precision_tools': True, 'premultiplied_alpha': True}
    (Path(__file__).parent / 'validation.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    verify()
