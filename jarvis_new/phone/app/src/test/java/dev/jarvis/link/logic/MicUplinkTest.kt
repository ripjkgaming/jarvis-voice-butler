package dev.jarvis.link.logic

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MicUplinkTest {
    @Test
    fun handshakeIsJsonLine() {
        val s = MicUplink.handshake().toString(Charsets.UTF_8)
        assertTrue(s.endsWith("\n"))
        val o = JSONObject(s)
        assertEquals(16000, o.getInt("rate"))
        assertEquals(1, o.getInt("channels"))
    }

    @Test
    fun ackOk() {
        assertTrue(MicUplink.ackOk("{\"ok\":true}"))
        assertFalse(MicUplink.ackOk("{\"ok\":false}"))
        assertFalse(MicUplink.ackOk("{}"))
        assertFalse(MicUplink.ackOk("garbage"))
        assertFalse(MicUplink.ackOk(""))
        assertFalse(MicUplink.ackOk(null))
    }

    @Test
    fun isWake() {
        assertTrue(MicUplink.isWake("WAKE"))
        assertTrue(MicUplink.isWake("WAKE 123"))
        assertFalse(MicUplink.isWake(""))
        assertFalse(MicUplink.isWake("hello"))
        assertFalse(MicUplink.isWake("wake"))
    }

    @Test
    fun backoffScheduleAndCap() {
        assertEquals(2000L, MicUplink.backoff(1))
        assertEquals(5000L, MicUplink.backoff(2))
        assertEquals(15000L, MicUplink.backoff(3))
        assertEquals(30000L, MicUplink.backoff(4))
        assertEquals(60000L, MicUplink.backoff(5))
        assertEquals(60000L, MicUplink.backoff(6))
        assertEquals(60000L, MicUplink.backoff(100))
    }

    @Test
    fun pcmBytesLittleEndian() {
        val samples = shortArrayOf(0, 1, -1, 256, -256, Short.MAX_VALUE, Short.MIN_VALUE)
        val out = ByteArray(16)
        assertEquals(14, MicUplink.pcmBytes(samples, samples.size, out))
        assertEquals(0x00.toByte(), out[0])
        assertEquals(0x00.toByte(), out[1])
        assertEquals(0x01.toByte(), out[2])
        assertEquals(0x00.toByte(), out[3])
        assertEquals(0xFF.toByte(), out[4])
        assertEquals(0xFF.toByte(), out[5])
        assertEquals(0x00.toByte(), out[6])
        assertEquals(0x01.toByte(), out[7])
        assertEquals(0x00.toByte(), out[8])
        assertEquals(0xFF.toByte(), out[9])
        assertEquals(0xFF.toByte(), out[10])
        assertEquals(0x7F.toByte(), out[11])
        assertEquals(0x00.toByte(), out[12])
        assertEquals(0x80.toByte(), out[13])
    }

    @Test
    fun pcmBytesPartialCount() {
        val samples = shortArrayOf(1, 2, 3)
        val out = ByteArray(8)
        assertEquals(4, MicUplink.pcmBytes(samples, 2, out))
        assertEquals(0x01.toByte(), out[0])
        assertEquals(0x02.toByte(), out[2])
    }

    @Test
    fun wakeRouting() {
        assertEquals(WakeRouting.Target.ONE_SHOT, WakeRouting.decide(true))
        assertEquals(WakeRouting.Target.APP_AUTO_TALK, WakeRouting.decide(false))
    }
}
