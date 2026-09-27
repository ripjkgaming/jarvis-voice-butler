package dev.jarvis.link

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SessionAudioProbeTest {
    private fun shortsToB64(s: ShortArray): String {
        val bytes = ByteArray(s.size * 2)
        for (i in s.indices) {
            bytes[i * 2] = (s[i].toInt() and 0xFF).toByte()
            bytes[i * 2 + 1] = ((s[i].toInt() shr 8) and 0xFF).toByte()
        }
        return java.util.Base64.getEncoder().encodeToString(bytes)
    }

    @Test
    fun decodeRoundTripsPcm16() {
        val want = shortArrayOf(0, 1, -1, 32767, -32768, 1234, -5678)
        SessionAudioProbe.offer(shortsToB64(want), 22050)
        assertArrayEquals(want, SessionAudioProbe.decodePcm16())
    }

    @Test
    fun takeClearsSlot() {
        SessionAudioProbe.offer(shortsToB64(shortArrayOf(7)), 22050)
        SessionAudioProbe.take()
        assertNull(SessionAudioProbe.take())
        assertNull(SessionAudioProbe.decodePcm16())
    }

    @Test
    fun emptyOfferIsIgnored() {
        SessionAudioProbe.offer("", 22050)
        assertNull(SessionAudioProbe.decodePcm16())
    }
}
