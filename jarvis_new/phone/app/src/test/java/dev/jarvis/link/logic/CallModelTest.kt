package dev.jarvis.link.logic

import dev.jarvis.link.net.Ack
import dev.jarvis.link.net.BridgeError
import dev.jarvis.link.net.Caption
import dev.jarvis.link.net.RoomInfo
import kotlinx.coroutines.CompletableDeferred
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class FakeEngine : CallEngine {
    override var listener: CallEngine.Listener? = null
    var connected: Pair<String, String>? = null
    var failWith: Exception? = null
    var gate: CompletableDeferred<Unit>? = null
    var mic = true
    var disconnects = 0
    override suspend fun connect(url: String, token: String) {
        gate?.await()
        failWith?.let { throw it }
        connected = url to token
    }
    override suspend fun setMicEnabled(enabled: Boolean) { mic = enabled }
    override suspend fun disconnect() { disconnects++; connected = null }
}

class FakeHost : CallHost {
    var starts = 0; var ends = 0
    override fun onCallStarting() { starts++ }
    override fun onCallEnded() { ends++ }
}

class CallModelTest {
    private val env = TestEnv()
    private val engine = FakeEngine()
    private val host = FakeHost()
    private fun model() = CallModel(env.bridge, env.config, engine, host, env.messenger, env.scope, env.dispatcher, 30_000, 3_000)

    @Test
    fun joinMintsTokenConnectsAndGoesLive() {
        val m = model()
        m.join()
        assertEquals(CallPhase.LIVE, m.state.value.phase)
        assertEquals("wss://lk" to "tok", engine.connected)
        assertEquals(1, host.starts)
        assertTrue(env.bridge.calls.contains("token::true"))
    }

    @Test
    fun joiningAnExistingRoomDoesNotDispatchTheAgent() {
        env.bridge.onRoom = { RoomInfo("jarvis-77", false, false) }
        val m = model()
        m.joinLaptop()
        assertEquals(CallPhase.LIVE, m.state.value.phase)
        assertTrue(env.bridge.calls.contains("token:jarvis-77:false"))
    }

    @Test
    fun noLaptopCallIsAMessageNotAJoin() {
        val m = model()
        m.joinLaptop()
        assertEquals(CallPhase.IDLE, m.state.value.phase)
        assertEquals("No live laptop call right now.", m.state.value.notice)
        env.bridge.onRoom = { RoomInfo(null, true, false) }
        m.joinLaptop()
        assertTrue(m.state.value.notice.contains("waking"))
        assertEquals(0, host.starts)
    }

    @Test
    fun tokenFailureIsAFaultAndReleasesTheService() {
        env.bridge.onToken = { _, _ -> bridgeFail(BridgeError.Server(503, "livekit not configured")) }
        val m = model()
        m.join()
        assertEquals(CallPhase.ERROR, m.state.value.phase)
        assertEquals("livekit not configured", m.state.value.detail)
        assertEquals(host.starts, host.ends)
    }

    @Test
    fun transientTokenFailureIsRetriedOnce() {
        var n = 0
        env.bridge.onToken = { _, _ -> if (n++ == 0) bridgeFail(BridgeError.Timeout("h:1")) else dev.jarvis.link.net.CallToken("u", "t") }
        val m = model()
        m.join()
        assertEquals(CallPhase.LIVE, m.state.value.phase)
        assertEquals(2, n)
    }

    @Test
    fun permanentTokenFailureIsNotRetried() {
        var n = 0
        env.bridge.onToken = { _, _ -> n++; bridgeFail(BridgeError.Unauthorized) }
        model().join()
        assertEquals(1, n)
    }

    @Test
    fun connectFailureShowsEngineMessageAndDisconnects() {
        engine.failWith = IllegalStateException("ICE failed")
        val m = model()
        m.join()
        assertEquals(CallPhase.ERROR, m.state.value.phase)
        assertEquals("ICE failed", m.state.value.detail)
        assertEquals(1, engine.disconnects)
        assertEquals(host.starts, host.ends)
    }

    @Test
    fun joinTimesOut() {
        engine.gate = CompletableDeferred() // never completes
        val m = model()
        m.join()
        assertEquals(CallPhase.JOINING, m.state.value.phase)
        env.advance(30_001)
        assertEquals(CallPhase.ERROR, m.state.value.phase)
        assertTrue(m.state.value.detail.contains("timed out"))
        assertEquals(1, engine.disconnects)
    }

    @Test
    fun hangupWhileJoiningCancelsAndStaysIdle() {
        engine.gate = CompletableDeferred()
        val m = model()
        m.join()
        m.hangup()
        engine.gate!!.complete(Unit)
        env.advance(31_000)
        assertEquals(CallPhase.IDLE, m.state.value.phase)
        assertEquals(1, host.ends)
    }

    @Test
    fun doubleJoinIsIgnored() {
        val m = model()
        m.join(); m.join()
        assertEquals(1, host.starts)
        assertEquals(1, env.bridge.calls.count { it.startsWith("token") })
    }

    @Test
    fun offlineAndUnconfiguredRefuseToJoin() {
        env.prefs.offlineMode = true
        val m = model()
        m.join()
        assertEquals(CallPhase.ERROR, m.state.value.phase)
        assertEquals(0, host.starts)
        env.prefs.offlineMode = false
        env.prefs.token = ""
        m.join()
        assertTrue(m.state.value.detail.contains("token"))
    }

    @Test
    fun muteTogglesTheMicOnlyWhileLive() {
        val m = model()
        m.setMuted(true)
        assertFalse(m.state.value.muted)
        m.join()
        m.toggleMute()
        assertTrue(m.state.value.muted)
        assertFalse(engine.mic)
        m.toggleMute()
        assertTrue(engine.mic)
    }

    @Test
    fun muteFailureRevertsTheFlag() {
        val m = model()
        m.join()
        val bad = object : CallEngine by engine {
            override suspend fun setMicEnabled(enabled: Boolean) { throw IllegalStateException("mic gone") }
        }
        val m2 = CallModel(env.bridge, env.config, bad, host, env.messenger, env.scope, env.dispatcher)
        m2.join()
        m2.setMuted(true)
        assertFalse(m2.state.value.muted)
        assertTrue(m2.state.value.notice.contains("mic gone"))
    }

    @Test
    fun unexpectedDisconnectIsAFaultNotAQuietIdle() {
        val m = model()
        m.join()
        engine.listener!!.onLost("network changed")
        assertEquals(CallPhase.ERROR, m.state.value.phase)
        assertEquals("Call lost: network changed", m.state.value.detail)
        assertEquals(1, host.ends)
    }

    @Test
    fun lostAfterHangupIsIgnored() {
        val m = model()
        m.join()
        m.hangup()
        engine.listener!!.onLost("late event")
        assertEquals(CallPhase.IDLE, m.state.value.phase)
        assertEquals(1, host.ends)
    }

    @Test
    fun speakingOnlyTracksWhileLive() {
        val m = model()
        engine.listener!!.onSpeaking(true)
        assertFalse(m.state.value.agentSpeaking)
        m.join()
        engine.listener!!.onSpeaking(true)
        assertTrue(m.state.value.agentSpeaking)
        m.hangup()
        assertFalse(m.state.value.agentSpeaking)
    }

    @Test
    fun rejoinAfterFaultWorks() {
        engine.failWith = IllegalStateException("x")
        val m = model()
        m.join()
        engine.failWith = null
        m.join()
        assertEquals(CallPhase.LIVE, m.state.value.phase)
    }

    @Test
    fun captionsStayChronologicalAndPollEveryThreeSeconds() {
        var n = 0
        env.bridge.onCaptions = { n++; listOf(Caption(1, "user", "a"), Caption(2, "jarvis", "b")) }
        val m = model()
        m.startPolling()
        m.startPolling()
        assertEquals(1, n)
        assertEquals(listOf("a", "b"), m.state.value.captions.map { it.text })
        env.advance(3000)
        assertEquals(2, n)
        m.stopPolling()
        env.advance(30_000)
        assertEquals(2, n)
    }

    @Test
    fun captionErrorKeepsOldLinesAndShowsNotice() {
        env.bridge.onCaptions = { listOf(Caption(1, "user", "a")) }
        val m = model()
        m.refreshCaptions()
        env.bridge.onCaptions = { bridgeFail(BridgeError.Unreachable("h:1")) }
        m.refreshCaptions()
        assertEquals(1, m.state.value.captions.size)
        assertTrue(m.state.value.notice.contains("Cannot reach"))
    }

    @Test
    fun summonReports() {
        val m = model()
        m.summon()
        assertEquals("Summoned.", m.state.value.notice)
        env.bridge.onSummon = { Ack(false, "not listening") }
        m.summon()
        assertEquals("not listening", m.state.value.notice)
    }
}
