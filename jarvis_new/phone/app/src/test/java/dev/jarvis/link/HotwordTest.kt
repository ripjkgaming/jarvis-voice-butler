package dev.jarvis.link

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class HotwordTest {
    @Test
    fun wakeWordWholeWordOnly() {
        assertTrue(Hotword.isWakeWord("Jarvis"))
        assertTrue(Hotword.isWakeWord("hey jarvis, lights on"))
        assertTrue(Hotword.isWakeWord("OK, JARVIS!"))
        assertFalse(Hotword.isWakeWord("jarvison"))
        assertFalse(Hotword.isWakeWord("hello there"))
        assertFalse(Hotword.isWakeWord(""))
    }

    @Test
    fun backoffSchedule() {
        assertEquals(0L, Hotword.backoffMs(0))
        assertEquals(1000L, Hotword.backoffMs(1))
        assertEquals(2000L, Hotword.backoffMs(2))
        assertEquals(16000L, Hotword.backoffMs(5))
        assertEquals(30000L, Hotword.backoffMs(6))
        assertEquals(30000L, Hotword.backoffMs(99))
    }

    @Test
    fun errorRouting() {
        // busy(8)/server(4)/network(2,1)/audio(3) back off…
        for (e in listOf(8, 4, 2, 1, 3)) assertTrue(Hotword.needsBackoff(e))
        // …no-match(7)/timeout(6)/client(5) restart at once.
        for (e in listOf(7, 6, 5)) assertFalse(Hotword.needsBackoff(e))
    }

    @Test
    fun gateArbitration() {
        HotwordGate.pttHeld = false
        HotwordGate.sessionActive = false
        assertTrue(HotwordGate.allowed(true, true, false))
        assertFalse(HotwordGate.allowed(false, true, false))
        HotwordGate.pttHeld = true
        assertFalse(HotwordGate.allowed(true, false, false))
        HotwordGate.pttHeld = false
        HotwordGate.sessionActive = true
        assertFalse(HotwordGate.allowed(true, false, false))
        HotwordGate.sessionActive = false
        assertFalse(HotwordGate.allowed(true, true, true))
        assertTrue(HotwordGate.allowed(true, false, true))
    }
}
