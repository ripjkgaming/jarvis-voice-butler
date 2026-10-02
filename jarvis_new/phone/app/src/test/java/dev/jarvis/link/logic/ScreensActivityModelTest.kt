package dev.jarvis.link.logic

import dev.jarvis.link.net.BridgeError
import dev.jarvis.link.net.Caption
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Base64

class ScreensModelTest {
    private val env = TestEnv()
    private fun model() = ScreensModel(env.config, env.actions, env.scope, env.dispatcher)
    private fun outputsJson() = tr(true, "outputs" to JSONArray()
        .put(JSONObject().put("name", "DP-1").put("enabled", true))
        .put(JSONObject().put("name", "HDMI-A-2").put("enabled", true)))

    @Test
    fun loadsOutputs() {
        env.bridge.onTool = { _, _ -> outputsJson() }
        val m = model()
        m.loadOutputs()
        assertEquals(listOf("DP-1", "HDMI-A-2"), m.state.value.outputs.map { it.name })
    }

    @Test
    fun vanishedDisplayFallsBackToAll() {
        env.bridge.onTool = { _, _ -> outputsJson() }
        val m = model()
        m.select("eDP-1")
        m.loadOutputs()
        assertEquals(ScreensState.ALL, m.state.value.selected)
        m.select("DP-1")
        m.loadOutputs()
        assertEquals("DP-1", m.state.value.selected)
    }

    @Test
    fun captureSendsOutputOnlyForASingleDisplay() {
        val png = byteArrayOf(1, 2, 3)
        env.bridge.onTool = { name, _ ->
            if (name == "screenshot") tr(true, "image_b64" to Base64.getEncoder().encodeToString(png)) else outputsJson()
        }
        val m = model()
        m.capture()
        assertFalse(env.bridge.toolCalls.last().second.has("output"))
        assertArrayEquals(png, m.state.value.image)
        assertEquals(1, m.state.value.imageSeq)
        m.select("HDMI-A-2")
        m.capture()
        assertEquals("HDMI-A-2", env.bridge.toolCalls.last().second.getString("output"))
        assertEquals(2, m.state.value.imageSeq)
    }

    @Test
    fun unreadableImageIsReported() {
        env.bridge.onTool = { _, _ -> tr(true, "image_b64" to "!!!not base64!!!") }
        val m = model()
        m.capture()
        assertNull(m.state.value.image)
        assertTrue(m.state.value.notice.contains("unreadable"))
        assertFalse(m.state.value.busy)
    }

    @Test
    fun captureFailureShowsBridgeMessage() {
        env.bridge.onTool = { _, _ -> tr(false, error = "need spectacle or imagemagick") }
        val m = model()
        m.capture()
        assertEquals("need spectacle or imagemagick", m.state.value.notice)
    }

    @Test
    fun blackoutAndWakeTargetSelection() {
        env.bridge.onTool = { _, _ -> outputsJson() }
        val m = model()
        m.blackout()
        assertEquals("screen_off", env.bridge.toolCalls.first { it.first != "screens_state" }.first)
        env.bridge.toolCalls.clear()
        m.wake()
        assertEquals("screens_restore", env.bridge.toolCalls.first().first)
        env.bridge.toolCalls.clear()
        m.select("DP-1")
        m.wake()
        val on = env.bridge.toolCalls.first { it.first == "screen_on" }
        assertEquals("DP-1", on.second.getString("output"))
    }

    @Test
    fun guestFlagIsExposed() {
        env.prefs.guestMode = true
        val m = model()
        m.loadOutputs()
        assertTrue(m.state.value.guest)
        assertTrue(m.state.value.notice.contains("Guest"))
    }

    @Test
    fun decodeImageToleratesNewlines() {
        val b64 = Base64.getMimeEncoder(4, "\n".toByteArray()).encodeToString(ByteArray(40) { it.toByte() })
        assertEquals(40, ScreensModel.decodeImage(b64)!!.size)
        assertNull(ScreensModel.decodeImage(""))
        assertNull(ScreensModel.decodeImage(null))
    }
}

class ActivityModelTest {
    private val env = TestEnv()
    private fun model() = ActivityModel(env.bridge, env.scope, env.dispatcher, pollMs = 5000)

    @Test
    fun refreshFillsAllThreeFeeds() {
        env.bridge.onActions = { listOf("a", "b") }
        env.bridge.onCaptions = { listOf(Caption(1, "user", "hi")) }
        env.bridge.onSys = { JSONObject().put("ok", true).put("cpu", 12).put("phone", JSONObject().put("battery", 71).put("charging", true)) }
        val m = model()
        m.refresh()
        val s = m.state.value
        assertEquals(listOf("a", "b"), s.actions)
        assertEquals("hi", s.captions.single().text)
        assertEquals(listOf("cpu: 12", "phone battery: 71% (charging)"), s.system)
        assertFalse(s.loading)
        assertNull(s.error)
    }

    @Test
    fun failureKeepsStaleDataAndShowsError() {
        env.bridge.onActions = { listOf("a") }
        val m = model()
        m.refresh()
        env.bridge.onActions = { bridgeFail(BridgeError.Timeout("h:1")) }
        m.refresh()
        assertEquals(listOf("a"), m.state.value.actions)
        assertTrue(m.state.value.error!!.contains("did not answer"))
    }

    @Test
    fun pollingRepeatsUntilStopped() {
        val m = model()
        m.startPolling()
        m.startPolling() // idempotent
        assertEquals(1, m.state.value.refreshes)
        env.advance(5000)
        assertEquals(2, m.state.value.refreshes)
        env.advance(5000)
        assertEquals(3, m.state.value.refreshes)
        m.stopPolling()
        env.advance(20_000)
        assertEquals(3, m.state.value.refreshes)
    }

    @Test
    fun sysLinesSkipNestedAndNull() {
        val lines = ActivityModel.sysLines(
            JSONObject().put("ok", true).put("b", "x").put("a", 1).put("n", JSONObject.NULL)
                .put("arr", JSONArray().put(1)).put("obj", JSONObject().put("k", 1)),
        )
        assertEquals(listOf("a: 1", "b: x"), lines)
    }
}
