package dev.jarvis.link.logic

import dev.jarvis.link.net.BridgeConfig
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PrefsTest {
    private fun fresh(): Prefs = Prefs(MemoryStore())

    @Test
    fun defaults() {
        val p = fresh()
        assertFalse(p.micUplink)
        assertFalse(p.hotwordEnabled)
        assertTrue(p.ttsEnabled)
        assertEquals(4317, p.httpPort)
        assertEquals(4318, p.micPort)
    }

    @Test
    fun tokenSetterNormalizes() {
        val p = fresh()
        p.token = "Bearer abc123"
        assertEquals("abc123", p.token)
        p.token = "\"quoted\""
        assertEquals("quoted", p.token)
    }

    @Test
    fun hostTrimmed() {
        val p = fresh()
        p.host = "  100.77.6.93  "
        assertEquals("100.77.6.93", p.host)
    }

    @Test
    fun configMirrorsFields() {
        val p = fresh()
        p.host = "1.2.3.4"
        p.httpPort = 8080
        p.token = "secret"
        p.guestMode = true
        p.offlineMode = true
        assertEquals(BridgeConfig("1.2.3.4", 8080, "secret", true, true), p.config())
    }

    @Test
    fun sessionIdStableAcrossReads() {
        val p = fresh()
        val first = p.sessionId
        assertTrue(first.isNotBlank())
        assertEquals(first, p.sessionId)
        assertEquals(first, p.sessionId)
    }
}
