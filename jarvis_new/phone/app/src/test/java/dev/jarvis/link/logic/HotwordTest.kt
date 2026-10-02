package dev.jarvis.link.logic

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class HotwordTest {
    private var now = 1000L
    private lateinit var ctl: HotwordController

    @Before
    fun setUp() {
        HotwordGate.reset()
        now = 1000L
        ctl = HotwordController(clock = { now })
    }

    @After
    fun tearDown() {
        HotwordGate.reset()
    }

    @Test
    fun isWakeWordWholeWordOnly() {
        assertTrue(Hotword.isWakeWord("hey jarvis"))
        assertTrue(Hotword.isWakeWord("JARVIS"))
        assertTrue(Hotword.isWakeWord("Hey Jarvis, lights on"))
        assertTrue(Hotword.isWakeWord("jarvis-like"))
        assertFalse(Hotword.isWakeWord(""))
        assertFalse(Hotword.isWakeWord("hello there"))
        assertFalse(Hotword.isWakeWord("jarv"))
        assertFalse(Hotword.isWakeWord("jarviss"))
        assertFalse(Hotword.isWakeWord("ajarvis"))
    }

    @Test
    fun backoffSequenceAndCap() {
        assertEquals(0L, Hotword.backoffMs(0))
        assertEquals(0L, Hotword.backoffMs(-3))
        assertEquals(1000L, Hotword.backoffMs(1))
        assertEquals(2000L, Hotword.backoffMs(2))
        assertEquals(4000L, Hotword.backoffMs(3))
        assertEquals(8000L, Hotword.backoffMs(4))
        assertEquals(16000L, Hotword.backoffMs(5))
        assertEquals(30000L, Hotword.backoffMs(6))
        assertEquals(30000L, Hotword.backoffMs(10))
    }

    @Test
    fun needsBackoff() {
        for (e in listOf(1, 2, 3, 4, 8, 10, 11, 12, 13)) assertTrue("error $e", Hotword.needsBackoff(e))
        for (e in listOf(5, 6, 7, 9)) assertFalse("error $e", Hotword.needsBackoff(e))
    }

    @Test
    fun isFatalOnlyPermissions() {
        assertTrue(Hotword.isFatal(Hotword.ERROR_INSUFFICIENT_PERMISSIONS))
        assertFalse(Hotword.isFatal(Hotword.ERROR_NETWORK))
        assertFalse(Hotword.isFatal(Hotword.ERROR_NO_MATCH))
    }

    @Test
    fun armListensWhenGateOpen() {
        assertEquals(HotwordStep.Listen, ctl.arm(true, false, false))
    }

    @Test
    fun armStopsWhenOff() {
        val step = ctl.arm(false, false, false)
        assertTrue(step is HotwordStep.Stop)
    }

    @Test
    fun armWaitsWhenGateBlocked() {
        HotwordGate.pttHeld = true
        assertEquals(HotwordStep.Wait(Hotword.GATE_RECHECK_MS), ctl.arm(true, false, false))
        HotwordGate.pttHeld = false
        HotwordGate.sessionActive = true
        assertEquals(HotwordStep.Wait(Hotword.GATE_RECHECK_MS), ctl.arm(true, false, false))
        HotwordGate.sessionActive = false
        assertEquals(HotwordStep.Wait(Hotword.GATE_RECHECK_MS), ctl.arm(true, true, true))
        assertEquals(HotwordStep.Listen, ctl.arm(true, false, true))
    }

    @Test
    fun wakeArmsCooldown() {
        assertEquals(HotwordStep.Wake(5000L), ctl.heard(listOf("hey jarvis"), true))
        assertEquals(HotwordStep.Wait(Hotword.GATE_RECHECK_MS), ctl.arm(true, false, false))
        now += 4999
        assertEquals(HotwordStep.Wait(Hotword.GATE_RECHECK_MS), ctl.arm(true, false, false))
        now += 1
        assertEquals(HotwordStep.Listen, ctl.arm(true, false, false))
    }

    @Test
    fun heardPartialVsFinal() {
        assertNull(ctl.heard(listOf("hello there"), false))
        assertEquals(HotwordStep.Restart(0), ctl.heard(listOf("hello there"), true))
        assertEquals(HotwordStep.Wake(5000L), ctl.heard(listOf("say jarvis please"), false))
    }

    @Test
    fun wakeResetsFailures() {
        ctl.error(Hotword.ERROR_NETWORK)
        ctl.error(Hotword.ERROR_NETWORK)
        assertEquals(2, ctl.consecutiveFailures)
        ctl.heard(listOf("jarvis"), true)
        assertEquals(0, ctl.consecutiveFailures)
    }

    @Test
    fun errorBackoffGrows() {
        assertEquals(HotwordStep.Restart(1000), ctl.error(Hotword.ERROR_NETWORK_TIMEOUT))
        assertEquals(HotwordStep.Restart(2000), ctl.error(Hotword.ERROR_NETWORK))
        assertEquals(HotwordStep.Restart(4000), ctl.error(Hotword.ERROR_SERVER))
        assertEquals(3, ctl.consecutiveFailures)
    }

    @Test
    fun nonBackoffErrorResetsToFastRestart() {
        ctl.error(Hotword.ERROR_NETWORK)
        assertEquals(HotwordStep.Restart(500), ctl.error(Hotword.ERROR_NO_MATCH))
        assertEquals(0, ctl.consecutiveFailures)
    }

    @Test
    fun permissionsErrorStops() {
        assertTrue(ctl.error(Hotword.ERROR_INSUFFICIENT_PERMISSIONS) is HotwordStep.Stop)
    }

    @Test
    fun startFailedUsesTwoSecondFloor() {
        assertEquals(HotwordStep.Restart(2000), ctl.startFailed())
        assertEquals(HotwordStep.Restart(2000), ctl.startFailed())
        assertEquals(HotwordStep.Restart(4000), ctl.startFailed())
    }
}
