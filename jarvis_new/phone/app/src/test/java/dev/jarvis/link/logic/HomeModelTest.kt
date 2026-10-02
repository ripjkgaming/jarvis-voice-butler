package dev.jarvis.link.logic

import dev.jarvis.link.net.BatterySample
import dev.jarvis.link.net.BridgeError
import dev.jarvis.link.net.RouteReply
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class HomeModelTest {
    private val env = TestEnv()
    private val capture = FakeCapture()
    private var battery: BatterySample? = BatterySample(80, true)

    private fun model() = HomeModel(
        env.config, env.actions, env.messenger, env.talk, capture, env.conversation,
        { battery }, env.scope, env.dispatcher,
    )

    @Before fun before() = HotwordGate.reset()
    @After fun after() = HotwordGate.reset()

    @Test
    fun startsWithConfigFlagsAndBattery() {
        env.prefs.guestMode = true
        val m = model()
        assertTrue(m.state.value.guest)
        assertEquals(80, m.state.value.battery!!.percent)
        assertNull(m.state.value.configProblem)
    }

    @Test
    fun refreshOnlineShowsBridgeSummary() {
        val m = model()
        m.refreshLink()
        assertEquals(Link.ONLINE, m.state.value.link)
        assertTrue(m.state.value.linkDetail.startsWith("Bridge 1.0"))
    }

    @Test
    fun refreshFailureShowsReason() {
        env.bridge.onHealth = { bridgeFail(BridgeError.Unauthorized) }
        val m = model()
        m.refreshLink()
        assertEquals(Link.FAILED, m.state.value.link)
        assertTrue(m.state.value.linkDetail.contains("token"))
    }

    @Test
    fun refreshWithoutTokenNeverCallsTheBridge() {
        env.prefs.token = ""
        val m = model()
        m.refreshLink()
        assertEquals(Link.FAILED, m.state.value.link)
        assertNotNull(m.state.value.configProblem)
        assertTrue(env.bridge.calls.isEmpty())
    }

    @Test
    fun refreshOfflineSaysSo() {
        env.prefs.offlineMode = true
        val m = model()
        m.refreshLink()
        assertEquals(Link.UNKNOWN, m.state.value.link)
        assertTrue(m.state.value.offline)
        assertTrue(env.bridge.calls.isEmpty())
    }

    @Test
    fun sendShowsInstantReplyAndNotice() {
        env.bridge.onRoute = { RouteReply("Done, Sir.", null) }
        val m = model()
        m.send("volume up", instant = true)
        assertEquals("Done, Sir.", m.state.value.lastReply)
        assertEquals("Instant reply", m.state.value.notice)
        assertFalse(m.state.value.busy)
    }

    @Test
    fun sendFailureClearsBusyAndShowsMessage() {
        env.bridge.onRoute = { bridgeFail(BridgeError.Timeout("h:1")) }
        val m = model()
        m.send("volume up", instant = true)
        assertFalse(m.state.value.busy)
        assertTrue(m.state.value.notice.contains("did not answer"))
    }

    @Test
    fun plainSendUsesChatNotTheRoute() {
        val m = model()
        m.send("tell me a joke")
        assertFalse(env.bridge.calls.contains("route"))
        assertEquals("ok", m.state.value.lastReply)
        assertEquals("Done", m.state.value.notice)
    }

    @Test
    fun conversationFlowsIntoState() {
        val m = model()
        env.conversation.add(Role.USER, "hi")
        assertEquals(listOf("hi"), m.state.value.messages.map { it.text })
    }

    @Test
    fun talkRecordsThenSendsAfterWindow() {
        val m = model()
        m.startTalk()
        assertTrue(m.state.value.recording)
        assertTrue(HotwordGate.pttHeld)
        env.advance(HomeModel.TALK_WINDOW_MS)
        assertFalse(m.state.value.recording)
        assertFalse(HotwordGate.pttHeld)
        assertEquals(1, capture.stopped)
        assertEquals("hi there", m.state.value.lastReply)
        assertEquals("hello", m.state.value.lastTranscript)
    }

    @Test
    fun manualStopBeatsTheTimerAndStopsOnlyOnce() {
        val m = model()
        m.startTalk()
        m.stopTalk()
        env.advance(10_000)
        assertEquals(1, capture.stopped)
        assertEquals(1, env.bridge.calls.count { it == "talk" })
    }

    @Test
    fun micUnavailableReleasesTheGate() {
        capture.canStart = false
        val m = model()
        m.startTalk()
        assertFalse(m.state.value.recording)
        assertFalse(HotwordGate.pttHeld)
        assertTrue(m.state.value.notice.contains("Microphone"))
    }

    @Test
    fun talkWhileOfflineOrUnconfiguredDoesNotRecord() {
        env.prefs.offlineMode = true
        val m = model()
        m.startTalk()
        assertEquals(0, capture.started)
        env.prefs.offlineMode = false
        env.prefs.host = ""
        m.startTalk()
        assertEquals(0, capture.started)
    }

    @Test
    fun tooShortRecordingExplainsItself() {
        capture.pcm = ByteArray(100)
        val m = model()
        m.startTalk()
        m.stopTalk()
        assertTrue(m.state.value.notice.contains("too short"))
        assertFalse(env.bridge.calls.contains("talk"))
    }

    @Test
    fun abortReleasesMicAndGate() {
        val m = model()
        m.startTalk()
        m.abortTalk()
        assertFalse(m.state.value.recording)
        assertFalse(HotwordGate.pttHeld)
        env.advance(10_000)
        assertEquals(1, capture.stopped)
        assertFalse(env.bridge.calls.contains("talk"))
    }

    @Test
    fun quickActionsRunTheRightTools() {
        val m = model()
        m.lock(); m.blackout(); m.restoreScreens(); m.unlock(true)
        assertEquals(listOf("lock", "screen_off", "screens_restore", "unlock"), env.bridge.toolCalls.map { it.first })
        assertEquals("Unlock: done", m.state.value.notice)
    }

    @Test
    fun unlockWithoutFingerprintIsDenied() {
        val m = model()
        m.unlock(false)
        assertTrue(m.state.value.notice.contains("denied"))
        assertTrue(env.bridge.toolCalls.isEmpty())
    }

    @Test
    fun failedQuickActionShowsTheError() {
        env.bridge.onTool = { _, _ -> tr(false, error = "loginctl failed") }
        val m = model()
        m.lock()
        assertEquals("Lock failed: loginctl failed", m.state.value.notice)
        assertFalse(m.state.value.busy)
    }
}
