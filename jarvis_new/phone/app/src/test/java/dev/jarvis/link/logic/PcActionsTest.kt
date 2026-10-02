package dev.jarvis.link.logic

import dev.jarvis.link.net.BridgeError
import dev.jarvis.link.net.Tool
import dev.jarvis.link.net.ToolAction
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PcActionsTest {
    private val env = TestEnv()
    private val a get() = env.actions

    @Test
    fun toolFailureFromBridgeBecomesFail() {
        env.bridge.onTool = { _, _ -> tr(false, error = "pactl failed") }
        assertEquals("pactl failed", (a.volumeGet() as Outcome.Fail).message)
    }

    @Test
    fun transportErrorsKeepTheirMessage() {
        env.bridge.onTool = { _, _ -> bridgeFail(BridgeError.Unauthorized) }
        val f = a.lock() as Outcome.Fail
        assertTrue(f.message.contains("token"))
        assertTrue(f.fatal)
    }

    @Test
    fun guestIsRefusedBeforeTheNetwork() {
        env.prefs.guestMode = true
        assertTrue(a.lock() is Outcome.Fail)
        assertTrue(a.openApp("firefox") is Outcome.Fail)
        assertTrue(a.typeText("hi") is Outcome.Fail)
        assertTrue(a.pressEnter() is Outcome.Fail)
        assertTrue(a.volumeGet() is Outcome.Ok)
        assertEquals(listOf("tool:volume_get"), env.bridge.calls)
    }

    @Test
    fun unlockNeedsFingerprintAndOwnerMode() {
        assertTrue(a.unlock(false) is Outcome.Fail)
        assertEquals(0, env.bridge.calls.size)
        assertTrue(a.unlock(true) is Outcome.Ok)
        assertEquals("unlock", env.bridge.toolCalls.single().first)
        env.prefs.guestMode = true
        assertTrue((a.unlock(true) as Outcome.Fail).message.contains("guest"))
    }

    @Test
    fun blackoutUsesScreenOffNeverScreensOff() {
        a.blackout()
        a.blackout("DP-1")
        assertEquals(Tool.SCREEN_OFF, env.bridge.toolCalls[0].first)
        assertFalse(env.bridge.toolCalls[0].second.has("output"))
        assertEquals("DP-1", env.bridge.toolCalls[1].second.getString("output"))
    }

    @Test
    fun volumeSetClampsToBridgeRange() {
        a.volumeSet(999)
        a.volumeSet(-4)
        assertEquals(150, env.bridge.toolCalls[0].second.getInt("level"))
        assertEquals(0, env.bridge.toolCalls[1].second.getInt("level"))
    }

    @Test
    fun openAppTrimsAndRejectsBlank() {
        assertTrue(a.openApp("   ") is Outcome.Fail)
        a.openApp("  Firefox ")
        assertEquals("Firefox", env.bridge.toolCalls.single().second.getString("app"))
    }

    @Test
    fun typeTextReportsChunkCount() {
        val long = "ab ".repeat(400)
        assertEquals(3, (a.typeText(long) as Outcome.Ok).value)
        assertTrue(a.typeText("") is Outcome.Fail)
    }

    @Test
    fun enterIsTheXkbReturnKey() {
        a.pressEnter()
        assertEquals(listOf("Return"), env.bridge.keys)
    }

    @Test
    fun pcMicToggleFlipsCurrentState() {
        env.bridge.onMicMuted = { false }
        assertEquals(true, (a.togglePcMic() as Outcome.Ok).value)
        assertTrue(env.bridge.calls.contains("setmic:true"))
        env.bridge.onMicMuted = { true }
        assertEquals(false, (a.togglePcMic() as Outcome.Ok).value)
    }

    @Test
    fun pcMicUnavailableIsAnError() {
        env.bridge.onMicMuted = { null }
        assertTrue((a.togglePcMic() as Outcome.Fail).message.contains("wake listener"))
        env.bridge.onMicMuted = { false }
        env.bridge.onSetMic = { dev.jarvis.link.net.Ack(false, "wake listener unavailable") }
        assertTrue(a.togglePcMic() is Outcome.Fail)
    }

    @Test
    fun remoteStartHandsHostAndPortToTheLauncher() {
        env.bridge.onTool = { _, _ -> tr(true, "host" to "100.9.9.9", "port" to 21118) }
        assertTrue(a.remoteStart() is Outcome.Ok)
        assertEquals(listOf<Pair<String?, Int?>>("100.9.9.9" to 21118), env.remote)
    }

    @Test
    fun remoteStartWithoutPortPassesNull() {
        env.bridge.onTool = { _, _ -> tr(true, "host" to "h") }
        a.remoteStart()
        assertEquals(listOf<Pair<String?, Int?>>("h" to null), env.remote)
    }

    @Test
    fun onlySuccessfulRemoteStartActionsOpenTheClient() {
        a.handleAction(null)
        a.handleAction(ToolAction("lock", true, raw = JSONObject().put("tool", "lock")))
        a.handleAction(ToolAction("remote_start", false, raw = JSONObject()))
        assertTrue(env.remote.isEmpty())
        a.handleAction(ToolAction("remote_start", true, raw = JSONObject().put("host", "h").put("port", 5)))
        assertEquals(listOf<Pair<String?, Int?>>("h" to 5), env.remote)
    }

    @Test
    fun pingSummarisesHealthAndStatus() {
        val s = (a.ping() as Outcome.Ok).value
        assertEquals("Bridge 1.0, up 2h, agent jarvis", s)
    }

    @Test
    fun pingWarnsWhenLiveKitMissingAndSurvivesStatusFailure() {
        env.bridge.onStatus = { JSONObject().put("livekit_configured", false) }
        assertTrue((a.ping() as Outcome.Ok).value.contains("LiveKit not configured"))
        env.bridge.onStatus = { bridgeFail(BridgeError.Server(500, "x")) }
        assertTrue((a.ping() as Outcome.Ok).value.startsWith("Bridge 1.0"))
        env.bridge.onHealth = { bridgeFail(BridgeError.Unreachable("h:1")) }
        assertTrue(a.ping() is Outcome.Fail)
    }

    @Test
    fun uptimeFormatting() {
        assertEquals("30s", PcActions.formatUptime(30))
        assertEquals("10m", PcActions.formatUptime(600))
        assertEquals("3h", PcActions.formatUptime(3 * 3600))
        assertEquals("3d", PcActions.formatUptime(3 * 86400))
    }

    @Test
    fun attemptWrapsUnexpectedExceptions() {
        val r = attempt<Int> { throw IllegalStateException("boom") }
        assertTrue((r as Outcome.Fail).message.contains("boom"))
        assertNull(attempt { 1 }.failure)
    }
}
