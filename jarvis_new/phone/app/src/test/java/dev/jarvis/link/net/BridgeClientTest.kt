package dev.jarvis.link.net

import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.net.SocketTimeoutException
import java.util.Base64

class BridgeClientTest {
    private lateinit var server: MockWebServer
    private var cfg = BridgeConfig()
    private lateinit var client: BridgeClient

    @Before
    fun setUp() {
        server = MockWebServer().also { it.start() }
        cfg = BridgeConfig(server.hostName, server.port, "s3cret")
        client = BridgeClient({ cfg }, sleeper = {})
    }

    @After
    fun tearDown() {
        try { server.shutdown() } catch (_: Exception) { }
    }

    private fun json(body: String, code: Int = 200) = MockResponse().setResponseCode(code)
        .setHeader("Content-Type", "application/json").setBody(body)

    private fun failure(block: () -> Unit): BridgeError {
        try {
            block()
        } catch (e: BridgeException) {
            return e.error
        }
        throw AssertionError("expected BridgeException")
    }

    // ── auth, config, offline ────────────────────────────────────

    @Test
    fun sendsBearerTokenOnEveryCall() {
        server.enqueue(json("""{"ok":true,"version":"9"}"""))
        assertEquals("9", client.health().getString("version"))
        val r = server.takeRequest()
        assertEquals("GET", r.method)
        assertEquals("/health", r.path)
        assertEquals("Bearer s3cret", r.getHeader("Authorization"))
    }

    @Test
    fun offlineModeHoldsCallsWithoutTouchingTheNetwork() {
        cfg = cfg.copy(offline = true)
        assertEquals(BridgeError.Offline, failure { client.health() })
        assertEquals(0, server.requestCount)
    }

    @Test
    fun missingTokenAndHostAreReportedBeforeAnyRequest() {
        cfg = cfg.copy(token = "")
        assertEquals(BridgeError.NotConfigured(BridgeConfig.Problem.NO_TOKEN), failure { client.status() })
        cfg = cfg.copy(host = "", token = "x")
        assertEquals(BridgeError.NotConfigured(BridgeConfig.Problem.NO_HOST), failure { client.status() })
        assertEquals(0, server.requestCount)
    }

    @Test
    fun http401IsAClearTokenMessage() {
        server.enqueue(json("""{"ok":false,"error":"unauthorized"}""", 401))
        val e = failure { client.health() }
        assertEquals(BridgeError.Unauthorized, e)
        assertTrue(e.message.contains("token"))
    }

    @Test
    fun unreachableHostIsExplained() {
        val port = server.port
        server.shutdown()
        cfg = cfg.copy(httpPort = port)
        val e = failure { client.health() }
        assertTrue(e is BridgeError.Unreachable)
        assertTrue(e.message.contains("Tailscale"))
    }

    @Test
    fun socketTimeoutBecomesTimeoutError() {
        val http = OkHttpClient.Builder().addInterceptor { throw SocketTimeoutException("read timed out") }.build()
        val c = BridgeClient({ cfg }, http, sleeper = {})
        val e = failure { c.talk("AAAA") }
        assertTrue(e is BridgeError.Timeout)
        assertTrue(e.transient)
    }

    @Test
    fun nonJsonReplyOnTheWrongPortIsMalformed() {
        server.enqueue(MockResponse().setHeader("Content-Type", "text/html").setBody("<html>router</html>"))
        assertTrue(failure { client.health() } is BridgeError.Malformed)
    }

    // ── retries ──────────────────────────────────────────────────

    @Test
    fun getRetriesTransientGatewayErrors() {
        server.enqueue(json("{}", 503))
        server.enqueue(json("""{"ok":true}""", 200))
        client.health()
        assertEquals(2, server.requestCount)
    }

    @Test
    fun postIsNeverReplayed() {
        server.enqueue(json("""{"ok":false,"error":"wake listener unavailable"}""", 503))
        server.enqueue(json("""{"ok":true}"""))
        val e = failure { client.summon() }
        assertEquals(BridgeError.Server(503, "wake listener unavailable"), e)
        assertEquals(1, server.requestCount)
    }

    // ── /tool ────────────────────────────────────────────────────

    @Test
    fun toolBodyAndSuccessShape() {
        server.enqueue(json("""{"ok":true,"volume":42,"muted":false}"""))
        val r = client.tool(Tool.VOLUME_SET, Tool.volumeSet(42))
        assertTrue(r.ok)
        assertEquals(42, r.volume)
        assertEquals(false, r.muted)
        val req = server.takeRequest()
        assertEquals("/tool", req.path)
        val body = JSONObject(req.body.readUtf8())
        assertEquals("volume_set", body.getString("tool"))
        assertEquals(42, body.getJSONObject("args").getInt("level"))
        assertFalse(body.has("guest"))
    }

    @Test
    fun http400FromToolIsAToolFailureNotABadRequest() {
        server.enqueue(json("""{"ok":false,"error":"no such output 'X'"}""", 400))
        val r = client.tool(Tool.SCREENSHOT, Tool.screenshot("X"))
        assertFalse(r.ok)
        assertEquals("no such output 'X'", r.error)
    }

    @Test
    fun guestStampsBodiesAndBlocksUnsafeToolsLocally() {
        cfg = cfg.copy(guest = true)
        server.enqueue(json("""{"ok":true}"""))
        client.tool(Tool.MEDIA_NEXT)
        assertTrue(JSONObject(server.takeRequest().body.readUtf8()).getBoolean("guest"))
        val e = failure { client.tool(Tool.LOCK) }
        assertTrue(e is BridgeError.Guest)
        assertEquals(1, server.requestCount)
    }

    @Test
    fun serverGuestRefusalMapsToGuestError() {
        server.enqueue(json("""{"ok":false,"error":"guest mode refuses 'open_app'"}""", 403))
        // owner config, but the PC says guest: still a Guest error
        assertTrue(failure { client.tool(Tool.OPEN_APP, Tool.openApp("x")) } is BridgeError.Guest)
    }

    @Test
    fun screenshotArgsOmitOutputForAll() {
        assertFalse(Tool.screenshot("all").has("output"))
        assertFalse(Tool.screenshot(null).has("output"))
        assertEquals("HDMI-A-2", Tool.screenshot("HDMI-A-2").getString("output"))
    }

    @Test
    fun screensStateOutputsParse() {
        server.enqueue(json("""{"ok":true,"outputs":[{"name":"DP-1","enabled":true},{"name":"eDP-1","enabled":false}]}"""))
        val outs = client.tool(Tool.SCREENS_STATE).outputs
        assertEquals(listOf("DP-1", "eDP-1"), outs.map { it.name })
        assertEquals(listOf<Boolean?>(true, false), outs.map { it.enabled })
    }

    // ── /route ───────────────────────────────────────────────────

    @Test
    fun routeNoRouteReturnsNull() {
        server.enqueue(json("""{"ok":false,"error":"no-route"}""", 404))
        assertNull(client.route("what is the meaning of life"))
    }

    @Test
    fun routeOnOldBridgeIsNotFoundNotNull() {
        server.enqueue(json("""{"ok":false,"error":"unknown route"}""", 404))
        val e = failure { client.route("volume up") }
        assertTrue(e is BridgeError.NotFound)
        assertFalse(e.fatal)
    }

    @Test
    fun routeParsesReplyAndAction() {
        server.enqueue(json("""{"ok":true,"reply":"Locked, Sir.","action":{"tool":"lock","ok":true}}"""))
        val r = client.route("lock the pc")!!
        assertEquals("Locked, Sir.", r.reply)
        assertEquals("lock", r.action!!.tool)
        assertTrue(r.action!!.ok)
        assertEquals("""{"text":"lock the pc"}""", server.takeRequest().body.readUtf8())
    }

    @Test
    fun routeReplyWithoutActionIsAClarification() {
        server.enqueue(json("""{"ok":true,"reply":"Did you mean volume?"}"""))
        val r = client.route("vol")!!
        assertNull(r.action)
    }

    @Test
    fun routeSkipsOverlongText() {
        assertNull(client.route("x".repeat(Limits.ROUTE_MAX + 1)))
        assertEquals(0, server.requestCount)
    }

    // ── /talk /chat ──────────────────────────────────────────────

    @Test
    fun talkParsesAudioAndWarning() {
        server.enqueue(json("""{"ok":true,"transcript":"hi","reply":"Hello","audio_b64":"AAAA","audio_rate":22050}"""))
        val t = client.talk("QUJD")
        assertEquals("hi", t.transcript)
        assertEquals(22050, t.audioRate)
        assertNull(t.warning)
        val body = JSONObject(server.takeRequest().body.readUtf8())
        assertEquals(16000, body.getInt("rate"))
        assertEquals("QUJD", body.getString("audio_b64"))
    }

    @Test
    fun talkBrainOutageKeepsTranscript() {
        server.enqueue(json("""{"ok":true,"transcript":"hi","reply":"","audio_b64":"","warning":"quota"}"""))
        val t = client.talk("QUJD")
        assertEquals("quota", t.warning)
        assertEquals("", t.reply)
    }

    @Test
    fun talkVoiceStackMissingIsAServerError() {
        server.enqueue(json("""{"ok":false,"error":"voice stack missing (faster-whisper)"}""", 501))
        val e = failure { client.talk("QUJD") }
        assertEquals(BridgeError.Server(501, "voice stack missing (faster-whisper)"), e)
    }

    @Test
    fun chatSendsHistoryPairsAndCapsAtTwenty() {
        server.enqueue(json("""{"ok":true,"reply":"Indeed."}"""))
        val history = (1..30).map { listOf(if (it % 2 == 0) "jarvis" else "user", "m$it") }
        val r = client.chat("hello", history)
        assertEquals("Indeed.", r.reply)
        val body = JSONObject(server.takeRequest().body.readUtf8())
        val arr = body.getJSONArray("history")
        assertEquals(20, arr.length())
        assertEquals("m11", arr.getJSONArray(0).getString(1))
        assertEquals("jarvis", arr.getJSONArray(19).getString(0))
        assertEquals(false, body.getBoolean("voice"))
    }

    @Test
    fun chatVoiceSummonedAndOutageShapes() {
        server.enqueue(json("""{"ok":true,"voice":"summoned"}"""))
        assertTrue(client.chat("say hi", voice = true).summoned)
        server.enqueue(json("""{"ok":true,"reply":"","warning":"model busy"}"""))
        val r = client.chat("hello")
        assertEquals("", r.reply)
        assertEquals("model busy", r.warning)
    }

    @Test
    fun chatLimitsAreCheckedLocally() {
        assertTrue(failure { client.chat("x".repeat(2001)) } is BridgeError.BadRequest)
        assertTrue(failure { client.chat("x".repeat(501), voice = true) } is BridgeError.BadRequest)
        assertTrue(failure { client.chat("   ") } is BridgeError.BadRequest)
        assertEquals(0, server.requestCount)
    }

    // ── /type ────────────────────────────────────────────────────

    @Test
    fun longTextIsTypedInOrderedChunks() {
        repeat(3) { server.enqueue(json("""{"ok":true}""")) }
        val text = ("word ".repeat(250)).trim() // 1249 chars
        client.typeText(text)
        assertEquals(3, server.requestCount)
        val sent = (1..3).joinToString("") { JSONObject(server.takeRequest().body.readUtf8()).getString("text") }
        assertEquals(text, sent)
    }

    @Test
    fun typingIsRefusedForGuestsWithoutARequest() {
        cfg = cfg.copy(guest = true)
        assertTrue(failure { client.typeText("hi") } is BridgeError.Guest)
        assertTrue(failure { client.pressKey(Keys.ENTER) } is BridgeError.Guest)
        assertEquals(0, server.requestCount)
    }

    @Test
    fun keyPressUsesXkbNameReturnNotEnter() {
        server.enqueue(json("""{"ok":true}"""))
        client.pressKey(Keys.ENTER)
        assertEquals("""{"key":"Return"}""", server.takeRequest().body.readUtf8())
    }

    @Test
    fun wtypeFailureSurfacesTheBridgeMessage() {
        server.enqueue(json("""{"ok":false,"error":"Unknown key 'Enter'"}""", 500))
        assertEquals(BridgeError.Server(500, "Unknown key 'Enter'"), failure { client.pressKey("Enter") })
    }

    // ── room / captions / actions ────────────────────────────────

    @Test
    fun roomNullMeansNoCallNotTheStringNull() {
        server.enqueue(json("""{"ok":true,"room":null,"waking":true,"boot":false}"""))
        val r = client.room()
        assertNull(r.room)
        assertTrue(r.waking)
        server.enqueue(json("""{"ok":true,"room":"jarvis-123","waking":false,"boot":false}"""))
        assertEquals("jarvis-123", client.room().room)
    }

    @Test
    fun captionsKeepChronologicalOrderAndClampLimit() {
        server.enqueue(json("""{"ok":true,"captions":[{"ts":1,"role":"user","text":"a"},{"ts":2,"role":"jarvis","text":"b"}]}"""))
        val caps = client.captions(500)
        assertEquals(listOf("a", "b"), caps.map { it.text })
        assertEquals("/captions?limit=50", server.takeRequest().path)
    }

    @Test
    fun actionsParse() {
        server.enqueue(json("""{"ok":true,"actions":["one","two"]}"""))
        assertEquals(listOf("one", "two"), client.actions(10))
        assertEquals("/actions?limit=10", server.takeRequest().path)
    }

    // ── summon / token ───────────────────────────────────────────

    @Test
    fun summonSendsOptionalSeedAndChecksLength() {
        server.enqueue(json("""{"ok":true}"""))
        assertTrue(client.summon("  hello  ").ok)
        assertEquals("""{"text":"hello"}""", server.takeRequest().body.readUtf8())
        server.enqueue(json("""{"ok":true}"""))
        client.summon(null)
        assertEquals("{}", server.takeRequest().body.readUtf8())
        assertTrue(failure { client.summon("x".repeat(501)) } is BridgeError.BadRequest)
    }

    @Test
    fun summonSurfacesOkFalseFromTheWakeListener() {
        server.enqueue(json("""{"ok":false,"error":"not listening"}"""))
        val a = client.summon()
        assertFalse(a.ok)
        assertEquals("not listening", a.error)
    }

    @Test
    fun tokenRequestAndBothResponseShapes() {
        server.enqueue(json("""{"ok":true,"serverUrl":"wss://lk","participantToken":"T1"}"""))
        assertEquals(CallToken("wss://lk", "T1"), client.token("jarvis-9", false))
        val body = JSONObject(server.takeRequest().body.readUtf8())
        assertEquals("jarvis-9", body.getString("room"))
        assertFalse(body.getBoolean("dispatch"))
        server.enqueue(json("""{"ok":true,"url":"wss://lk2","token":"T2"}"""))
        assertEquals(CallToken("wss://lk2", "T2"), client.token())
    }

    @Test
    fun tokenRefusalCarriesTheBridgeError() {
        server.enqueue(json("""{"ok":false,"error":"livekit not configured"}""", 503))
        val e = failure { client.token() }
        assertEquals(BridgeError.Server(503, "livekit not configured"), e)
    }

    @Test
    fun parseTokenWithoutFieldsFailsWithBridgeText() {
        val e = failure { BridgeClient.parseToken(JSONObject("""{"error":"nope"}""")) }
        assertEquals("nope", e.message)
    }

    // ── camera / mic / telemetry ─────────────────────────────────

    @Test
    fun cameraFrameIsBase64AndCapped() {
        server.enqueue(json("""{"ok":true,"name":"1.jpg","bytes":3}"""))
        client.cameraFrame(byteArrayOf(1, 2, 3))
        val b = JSONObject(server.takeRequest().body.readUtf8()).getString("image_b64")
        assertEquals(listOf<Byte>(1, 2, 3), Base64.getDecoder().decode(b).toList())
        assertTrue(failure { client.cameraFrame(ByteArray(Limits.CAMERA_MAX_BYTES + 1)) } is BridgeError.BadRequest)
        assertTrue(failure { client.cameraFrame(ByteArray(0)) } is BridgeError.BadRequest)
    }

    @Test
    fun cameraLatestReturnsBytesOrNullWhenNoFrames() {
        server.enqueue(MockResponse().setHeader("Content-Type", "image/jpeg").setBody(okio.Buffer().write(byteArrayOf(9, 8, 7))))
        assertEquals(listOf<Byte>(9, 8, 7), client.cameraLatest()!!.toList())
        server.enqueue(json("""{"ok":false,"error":"no frames yet"}""", 404))
        assertNull(client.cameraLatest())
    }

    @Test
    fun micStatusAndMute() {
        server.enqueue(json("""{"ok":true,"muted":true}"""))
        assertEquals(true, client.micMuted())
        server.enqueue(json("""{"ok":false,"error":"wake listener unavailable"}"""))
        assertNull(client.micMuted())
        server.enqueue(json("""{"ok":true,"muted":false}"""))
        assertTrue(client.setMicMuted(false).ok)
        server.takeRequest(); server.takeRequest()
        assertEquals("""{"muted":false}""", server.takeRequest().body.readUtf8())
    }

    @Test
    fun telemetryPayloadClampsAndDoesNotCarryGuest() {
        assertEquals("""{"battery":100,"charging":true}""", BridgeClient.telemetryPayload(BatterySample(250, true)))
        assertEquals("""{"battery":0,"charging":false}""", BridgeClient.telemetryPayload(BatterySample(-5, false)))
        cfg = cfg.copy(guest = true)
        server.enqueue(json("""{"ok":true}"""))
        client.postTelemetry(BatterySample(55, false))
        val req = server.takeRequest()
        assertEquals("/phone/telemetry", req.path)
        val sent = JSONObject(req.body.readUtf8())
        assertEquals(55, sent.getInt("battery"))
        assertFalse(sent.getBoolean("charging"))
        assertFalse(sent.has("guest"))
    }

    @Test
    fun telemetryRejectionIsAnError() {
        server.enqueue(json("""{"ok":false,"error":"body must be {...}"}""", 400))
        assertTrue(failure { client.postTelemetry(BatterySample(5, false)) } is BridgeError.BadRequest)
    }
}
