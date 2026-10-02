package dev.jarvis.link.device

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import dev.jarvis.link.net.Limits
import java.io.ByteArrayOutputStream

/** Re-encodes a too-large JPEG so it fits the bridge's 8 MB camera limit. */
object JpegShrinker {
    fun shrink(jpeg: ByteArray): ByteArray {
        var bmp = BitmapFactory.decodeByteArray(jpeg, 0, jpeg.size) ?: return jpeg
        var quality = 85
        while (true) {
            val out = ByteArrayOutputStream()
            bmp.compress(Bitmap.CompressFormat.JPEG, quality, out)
            val bytes = out.toByteArray()
            if (bytes.size <= Limits.CAMERA_MAX_BYTES - 64 * 1024 || (quality <= 40 && bmp.width < 800)) return bytes
            if (quality > 50) quality -= 15
            else bmp = Bitmap.createScaledBitmap(bmp, bmp.width * 3 / 4, bmp.height * 3 / 4, true)
        }
    }
}
