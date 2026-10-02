package dev.jarvis.link.net

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Test

/**
 * Opt-in contract check against a REAL running bridge.py. Skipped unless
 * JARVIS_LIVE_BRIDGE=host:port and JARVIS_LIVE_TOKEN are set, e.g.
 *
 *   cd jarvis_new && JARVIS_BRIDGE_TOKEN=t JARVIS_BRIDGE_PORT=14317 uv run python src/bridge.py &
 *   JARVIS_LIVE_BRIDGE=127.0.0.1:14317 JARVIS_LIVE_TOKEN=t ./gradlew testDebugUnitTest --tests '*LiveBridgeTest'
 *
 * Only exercises endpoints that work without desktop hardware (no pactl,
 * wtype or screen); the bridge's own errors for the rest are asserted too.
 */
class LiveBridgeTest {
    private lateinit var cfg: BridgeConfig
    private lateinit var client: BridgeClient

    @Before
    fun setUp() {
        val target = System.getenv("JARVIS_LIVE_BRIDGE") ?: System.getProperty("jarvis.live.bridge")
        val token = System.getenv("JARVIS_LIVE_TOKEN") ?: System.getProperty("jarvis.live.token")
        assumeTrue("no live bridge configured", target != null && token != null)
        val (h, p) = target!!.split(":")
        cfg = BridgeConfig(h, p.toInt(), token!!)
        client = BridgeClient({ cfg })
    }

    private fun failure(block: () -> Unit): BridgeError =
        try { block(); throw AssertionError("expected failure") } catch (e: BridgeException) { e.error }

    @Test
    fun healthAndStatus() {
        assertTrue(client.health().getBoolean("ok"))
        assertTrue(client.status().has("agent_name"))
    }

    @Test
    fun wrongTokenIsUnauthorized() {
        cfg = cfg.copy(token = "wrong")
        assertEquals(BridgeError.Unauthorized, failure { client.health() })
    }

    @Test
    fun feeds() {
        assertNotNull(client.captions(5))
        assertNotNull(client.actions(5))
        assertNull(client.room().room)
        assertTrue(client.sys().getBoolean("ok"))
    }

    @Test
    fun unknownToolIsAToolFailureWithBridgeText() {
        val r = client.tool("definitely_not_a_tool")
        assertFalse(r.ok)
        assertTrue(r.error!!.contains("unknown tool"))
    }

    @Test
    fun guestIsRefusedByTheServerToo() {
        // bypass the client-side guard to prove the server enforces it
        val raw = client.exec("POST", "/tool", org.json.JSONObject().put("tool", "lock").put("args", org.json.JSONObject()).put("guest", true))
        assertEquals(403, raw.code)
        assertTrue(client.errorFor(raw) is BridgeError.Guest)
        val typed = client.exec("POST", "/type", org.json.JSONObject().put("text", "x").put("guest", true))
        assertEquals(403, typed.code)
    }

    @Test
    fun routeAnswersTimeInstantlyWithAnAction() {
        val r = client.route("what time is it")
        assertNotNull(r)
        assertEquals("tell_time", r!!.action!!.tool)
    }

    @Test
    fun routeGibberishIsNoRouteOrAClarification() {
        // Low-confidence text gets either 404 no-route (null) or a clarifying reply without an action.
        val r = client.route("qwertyuiop asdfghjkl zxcvbnm")
        assertTrue(r == null || r.action == null)
    }

    @Test
    fun telemetryRoundTripsIntoSys() {
        client.postTelemetry(BatterySample(63, true))
        val phone = client.sys().optJSONObject("phone")
        assertNotNull(phone)
        assertEquals(63, phone!!.getInt("battery"))
        assertTrue(phone.getBoolean("charging"))
    }

    @Test
    fun cameraFrameRoundTrips() {
        val jpeg = byteArrayOf(0xFF.toByte(), 0xD8.toByte(), 1, 2, 3, 4)
        client.cameraFrame(jpeg)
        assertEquals(jpeg.toList(), client.cameraLatest()!!.toList())
    }

    @Test
    fun talkRejectsTinyAudioWithBridgeMessage() {
        val e = failure { client.talk(java.util.Base64.getEncoder().encodeToString(ByteArray(100))) }
        assertTrue(e is BridgeError.BadRequest)
        assertTrue(e.message.contains("0.1s"))
    }

    @Test
    fun unavailableWakeListenerSurfacesAs503() {
        val e = failure { client.summon() }
        assertEquals(BridgeError.Server(503, "wake listener unavailable"), e)
        assertNull(client.micMuted())
    }

    @Test
    fun chatRejectsOverlongTextBeforeSending() {
        assertTrue(failure { client.chat("x".repeat(2001)) } is BridgeError.BadRequest)
    }

    @Test
    fun typeBeyondLimitIsRefusedByTheBridgeAsABadRequest() {
        val raw = client.exec("POST", "/type", org.json.JSONObject().put("text", "x".repeat(501)))
        assertEquals(400, raw.code)
    }

    @Test
    fun enterKeyNameIsUnderstoodAsAKeyNotRejectedAsBad() {
        // No compositor in CI: the bridge answers 500 from wtype, never 400 "bad key".
        val raw = client.exec("POST", "/type", org.json.JSONObject().put("key", Keys.ENTER))
        assertTrue(raw.code == 200 || raw.code == 500)
    }
}
