package dev.jarvis.link.logic

import dev.jarvis.link.net.Ack
import dev.jarvis.link.net.BatterySample
import dev.jarvis.link.net.BridgeError
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PhoneModelTest {
    private val env = TestEnv()
    private fun model() = PhoneModel(env.actions, env.scope, env.dispatcher)

    @Test
    fun dialBuildsTelUriAndRejectsBadNumbers() {
        val m = model()
        assertNull(m.dial())
        assertTrue(m.state.value.notice.contains("valid phone number"))
        m.setNumber("+1 555 0100")
        assertEquals(PhoneAction.Dial("tel:+15550100"), m.dial())
        m.setNumber("call mom")
        assertNull(m.dial())
    }

    @Test
    fun smsCarriesTheMessageBody() {
        val m = model()
        m.setNumber("5550100"); m.setMessage("on my way")
        assertEquals(PhoneAction.Sms("smsto:5550100", "on my way"), m.sms())
    }

    @Test
    fun whatsappUsesDeepLinkOrFallsBackToShare() {
        val m = model()
        m.setNumber("+44 7700 900123"); m.setMessage("hi there")
        assertEquals(PhoneAction.WhatsApp("https://wa.me/447700900123?text=hi%20there", "hi there"), m.whatsapp())
        m.setNumber("")
        assertEquals(PhoneAction.WhatsApp(null, "hi there"), m.whatsapp())
    }

    @Test
    fun copyNeedsText() {
        val m = model()
        assertNull(m.copy())
        m.setMessage("x")
        assertEquals(PhoneAction.Copy("x"), m.copy())
    }

    @Test
    fun speakFallsBackToAGreeting() {
        val m = model()
        assertEquals(PhoneAction.Speak("At your service, Sir."), m.speak())
        m.setMessage("hello")
        assertEquals(PhoneAction.Speak("hello"), m.speak())
    }

    @Test
    fun pcMicToggle() {
        val m = model()
        m.togglePcMic()
        assertEquals(true, m.state.value.pcMicMuted)
        env.bridge.onMicMuted = { null }
        m.togglePcMic()
        assertTrue(m.state.value.notice.contains("unavailable"))
    }
}

class SosModelTest {
    private val env = TestEnv()
    private val effects = object : SosEffects {
        var started = 0; var stopped = 0
        override fun start() { started++ }
        override fun stop() { stopped++ }
    }
    private var fix: Pair<Double, Double>? = 51.5 to -0.12
    private fun model() = SosModel(env.config, env.actions, effects, { fix }, env.speaker, env.scope, env.dispatcher)

    @Test
    fun activateFiresAlarmAndNotifiesThePcWithLocation() {
        val m = model()
        m.activate()
        assertEquals(1, effects.started)
        assertTrue(m.state.value.active)
        assertEquals(true, m.state.value.pcNotified)
        assertEquals("51.50000, -0.12000", m.state.value.location)
        val (tool, args) = env.bridge.toolCalls.single()
        assertEquals("notify", tool)
        assertEquals("SOS from phone", args.getString("title"))
        assertTrue(args.getString("body").contains("maps.google.com/?q=51.50000,-0.12000"))
        assertTrue(args.getString("body").length <= 300)
        assertEquals(1, env.speaker.said.size)
    }

    @Test
    fun alarmStillFiresWhenThePcCannotBeReached() {
        env.bridge.onTool = { _, _ -> bridgeFail(BridgeError.Unreachable("h:1")) }
        val m = model()
        m.activate()
        assertTrue(m.state.value.active)
        assertEquals(false, m.state.value.pcNotified)
        assertTrue(m.state.value.notice.contains("PC not notified"))
    }

    @Test
    fun guestOfflineAndUnconfiguredStayLocal() {
        for (setup in listOf<() -> Unit>(
            { env.prefs.guestMode = true }, { env.prefs.offlineMode = true }, { env.prefs.token = "" },
        )) {
            setup()
            val m = model()
            m.activate()
            assertTrue(m.state.value.active)
            assertEquals(false, m.state.value.pcNotified)
            assertTrue(m.state.value.notice.contains("this phone only"))
        }
        assertTrue(env.bridge.calls.isEmpty())
    }

    @Test
    fun activateTwiceDoesNotRestartEffects() {
        val m = model()
        m.activate(); m.activate()
        assertEquals(1, effects.started)
    }

    @Test
    fun standDownStopsEffectsOnce() {
        val m = model()
        m.standDown()
        assertEquals(0, effects.stopped)
        m.activate()
        m.standDown(); m.standDown()
        assertEquals(1, effects.stopped)
        assertFalse(m.state.value.active)
    }

    @Test
    fun unknownLocationIsHandled() {
        fix = null
        val m = model()
        m.activate()
        assertEquals("unknown", m.state.value.location)
        assertTrue(env.bridge.toolCalls.single().second.getString("body").contains("Location unknown"))
    }

    @Test
    fun messageStaysUnderBridgeBodyLimit() {
        assertTrue(SosModel.message(-33.123456789 to 151.987654321).length <= 300)
    }
}

class CameraModelTest {
    private val env = TestEnv()

    @Test
    fun uploadSendsBytes() {
        val m = CameraModel(env.bridge, env.scope, env.dispatcher)
        m.upload(byteArrayOf(1, 2))
        assertEquals("Sent to Jarvis", m.state.value.notice)
        assertArrayEquals(byteArrayOf(1, 2), env.bridge.frames.single())
        assertFalse(m.state.value.busy)
    }

    @Test
    fun oversizedPhotoIsShrunkFirst() {
        var shrunk = false
        val m = CameraModel(env.bridge, env.scope, env.dispatcher) { shrunk = true; ByteArray(10) }
        m.upload(ByteArray(9 * 1024 * 1024))
        assertTrue(shrunk)
        assertEquals(10, env.bridge.frames.single().size)
    }

    @Test
    fun emptyPhotoIsRejectedLocally() {
        val m = CameraModel(env.bridge, env.scope, env.dispatcher)
        m.upload(ByteArray(0))
        assertTrue(env.bridge.frames.isEmpty())
        assertTrue(m.state.value.notice.contains("empty"))
    }

    @Test
    fun uploadFailureShowsMessage() {
        env.bridge.onFrame = { bridgeFail(BridgeError.Timeout("h:1")) }
        val m = CameraModel(env.bridge, env.scope, env.dispatcher)
        m.upload(byteArrayOf(1))
        assertTrue(m.state.value.notice.contains("did not answer"))
        assertFalse(m.state.value.busy)
    }

    @Test
    fun latestFrameLoadsOrReportsNone() {
        val m = CameraModel(env.bridge, env.scope, env.dispatcher)
        m.loadLatest()
        assertEquals("No frames on the PC yet.", m.state.value.notice)
        env.bridge.onLatest = { byteArrayOf(5) }
        m.loadLatest()
        assertArrayEquals(byteArrayOf(5), m.state.value.latest)
        assertEquals(1, m.state.value.latestSeq)
    }
}

class SettingsModelTest {
    private val env = TestEnv()
    private val control = object : ServiceControl {
        var mic: String? = null; var hot: String? = null; var tts = 0
        val micCalls = mutableListOf<Boolean>()
        override fun applyMicUplink(on: Boolean): String? { micCalls += on; return mic }
        override fun applyHotword(on: Boolean): String? = hot
        override fun refreshTts() { tts++ }
    }
    private fun model() = SettingsModel(env.prefs, env.actions, control, env.scope, env.dispatcher)

    @Test
    fun loadMirrorsPrefs() {
        env.prefs.httpPort = 5000
        val s = model().state.value
        assertEquals("100.1.2.3", s.host)
        assertEquals("5000", s.httpPort)
        assertEquals("tok", s.token)
    }

    @Test
    fun savePersistsCleanValues() {
        val m = model()
        assertTrue(m.save("http://100.5.6.7:4400/path", "", "", "Bearer abc123"))
        assertEquals("100.5.6.7", env.prefs.host)
        assertEquals(4400, env.prefs.httpPort) // port taken from the pasted URL
        assertEquals(4318, env.prefs.micPort)
        assertEquals("abc123", env.prefs.token)
        assertEquals("Saved.", m.state.value.notice)
    }

    @Test
    fun explicitPortFieldBeatsPortInHost() {
        val m = model()
        m.save("100.5.6.7:4400", "4500", "4318", "t")
        assertEquals(4500, env.prefs.httpPort)
    }

    @Test
    fun invalidFieldsAreReportedAndNothingIsSaved() {
        val m = model()
        assertFalse(m.save("bad host!", "99999", "abc", "has space"))
        assertEquals(setOf("host", "port", "micPort", "token"), m.state.value.errors.keys)
        assertEquals("100.1.2.3", env.prefs.host)
        assertEquals("tok", env.prefs.token)
    }

    @Test
    fun missingHostAndTokenHaveSpecificMessages() {
        val m = model()
        m.save("", "4317", "4318", "")
        assertTrue(m.state.value.errors["host"]!!.contains("No PC address"))
        assertTrue(m.state.value.errors["token"]!!.contains("token"))
    }

    @Test
    fun saveAndTestPingsOnlyWhenValid() {
        val m = model()
        m.saveAndTest("bad host", "4317", "4318", "t")
        assertTrue(env.bridge.calls.isEmpty())
        m.saveAndTest("100.1.2.3", "4317", "4318", "t")
        assertTrue(m.state.value.notice.startsWith("OK: Bridge 1.0"))
        assertFalse(m.state.value.testing)
    }

    @Test
    fun failedTestShowsTheReason() {
        env.bridge.onHealth = { bridgeFail(BridgeError.Unauthorized) }
        val m = model()
        m.testConnection()
        assertTrue(m.state.value.notice.startsWith("Failed:"))
    }

    @Test
    fun toggleRefusedByServicesStaysOffAndExplains() {
        control.mic = "Grant the microphone permission first"
        val m = model()
        m.setMicUplink(true)
        assertFalse(env.prefs.micUplink)
        assertFalse(m.state.value.micUplink)
        assertEquals("Grant the microphone permission first", m.state.value.notice)
        control.mic = null
        m.setMicUplink(true)
        assertTrue(env.prefs.micUplink)
        m.setMicUplink(false)
        assertFalse(env.prefs.micUplink)
        assertEquals(listOf(true, true, false), control.micCalls)
    }

    @Test
    fun hotwordRefusalAndSuccess() {
        control.hot = "no speech service"
        val m = model()
        m.setHotword(true)
        assertFalse(env.prefs.hotwordEnabled)
        control.hot = null
        m.setHotword(true)
        assertTrue(env.prefs.hotwordEnabled)
    }

    @Test
    fun simpleTogglesPersist() {
        val m = model()
        m.setTts(false); m.setTtsMale(false); m.setAutostart(false); m.setPauseOnMusic(false); m.setOffline(true)
        assertFalse(env.prefs.ttsEnabled); assertFalse(env.prefs.ttsMale); assertFalse(env.prefs.autostart)
        assertFalse(env.prefs.hotwordPauseOnMusic); assertTrue(env.prefs.offlineMode)
        assertEquals(1, control.tts)
    }

    @Test
    fun guestOnLocksThePcGuestOffDoesNot() {
        val m = model()
        m.setGuest(true)
        assertEquals("lock", env.bridge.toolCalls.single().first)
        assertEquals("Guest mode on: PC locked.", m.state.value.notice)
        env.bridge.toolCalls.clear()
        m.setGuest(false)
        assertTrue(env.bridge.toolCalls.isEmpty())
        assertFalse(env.prefs.guestMode)
    }

    @Test
    fun guestOnReportsLockFailure() {
        env.bridge.onTool = { _, _ -> tr(false, error = "loginctl failed") }
        val m = model()
        m.setGuest(true)
        assertTrue(m.state.value.notice.contains("could not be locked"))
        assertTrue(env.prefs.guestMode) // guest limits apply regardless
    }

    @Test
    fun remoteStartReportsHost() {
        env.bridge.onTool = { _, _ -> tr(true, "host" to "100.9.9.9") }
        val m = model()
        m.startRemote()
        assertEquals("Remote ready on 100.9.9.9", m.state.value.notice)
        assertEquals(1, env.remote.size)
    }
}

class TelemetryReporterTest {
    private val env = TestEnv()
    private var sample: BatterySample? = BatterySample(50, false)
    private fun reporter() = TelemetryReporter(env.bridge, env.config, { sample }, env.scope, env.dispatcher, { 5L }, 60_000)

    @Test
    fun pushesAtStartThenEveryMinute() {
        val r = reporter()
        r.acquire()
        assertEquals(1, env.bridge.telemetry.size)
        env.advance(60_000)
        assertEquals(2, env.bridge.telemetry.size)
        env.advance(60_000)
        assertEquals(3, r.state.value.pushes)
        r.release()
        env.advance(300_000)
        assertEquals(3, env.bridge.telemetry.size)
        assertFalse(r.state.value.running)
    }

    @Test
    fun referenceCountedStartAndStop() {
        val r = reporter()
        r.acquire(); r.acquire()
        assertEquals(1, env.bridge.telemetry.size) // one loop only
        r.release()
        env.advance(60_000)
        assertEquals(2, env.bridge.telemetry.size) // still running for the other user
        r.release()
        env.advance(60_000)
        assertEquals(2, env.bridge.telemetry.size)
        r.release() // extra release is harmless
    }

    @Test
    fun skipsWhenOfflineUnconfiguredOrNoBattery() {
        val r = reporter()
        env.prefs.offlineMode = true
        r.acquire()
        env.advance(60_000)
        assertTrue(env.bridge.telemetry.isEmpty())
        r.release()
        env.prefs.offlineMode = false
        sample = null
        r.acquire()
        assertTrue(env.bridge.telemetry.isEmpty())
        r.release()
    }

    @Test
    fun failureIsRecordedNotThrownAndRetriedNextTick() {
        var fail = true
        env.bridge.onTelemetry = { if (fail) bridgeFail(BridgeError.Unreachable("h:1")) }
        val r = reporter()
        r.acquire()
        assertTrue(r.state.value.lastError!!.contains("Cannot reach"))
        fail = false
        env.advance(60_000)
        assertNull(r.state.value.lastError)
        assertEquals(5L, r.state.value.lastOkAt)
        r.release()
    }

    @Test
    fun chargingFlipPushesImmediatelyButSameStateDoesNot() {
        val r = reporter()
        r.acquire()
        r.onBatteryChanged(BatterySample(50, false)) // first sample after push: same state
        assertEquals(1, env.bridge.telemetry.size)
        r.onBatteryChanged(BatterySample(51, true))
        assertEquals(2, env.bridge.telemetry.size)
        r.onBatteryChanged(BatterySample(52, true))
        assertEquals(2, env.bridge.telemetry.size)
        r.release()
    }

    @Test
    fun chargingFlipWithoutAnyUserDoesNothing() {
        val r = reporter()
        r.onBatteryChanged(BatterySample(50, false))
        r.onBatteryChanged(BatterySample(50, true))
        assertTrue(env.bridge.telemetry.isEmpty())
    }
}
