package dev.jarvis.link.logic

import dev.jarvis.link.net.BridgeError
import dev.jarvis.link.net.ChatReply
import dev.jarvis.link.net.Tool
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ChatModelTest {
    private val env = TestEnv()
    private fun model() = ChatModel(env.messenger, env.conversation, env.scope, env.dispatcher)

    @Test
    fun sendAppendsBothTurnsThroughSharedLog() {
        val m = model()
        m.send("hello")
        assertEquals(listOf("hello", "ok"), m.state.value.messages.map { it.text })
        assertFalse(m.state.value.busy)
        assertEquals("", m.state.value.notice)
    }

    @Test
    fun chatNeverUsesTheInstantRoute() {
        model().send("lock the pc")
        assertFalse(env.bridge.calls.contains("route"))
    }

    @Test
    fun errorShowsInNoticeAndUnblocks() {
        env.bridge.onChat = { _, _, _ -> bridgeFail(BridgeError.Unreachable("h:1")) }
        val m = model()
        m.send("hello")
        assertTrue(m.state.value.notice.contains("Cannot reach"))
        assertFalse(m.state.value.busy)
    }

    @Test
    fun warningOnlyReplyIsShownInTheNotice() {
        env.bridge.onChat = { _, _, _ -> ChatReply("", "quota", false, null) }
        val m = model()
        m.send("hello")
        assertEquals("quota", m.state.value.notice)
    }

    @Test
    fun clearEmptiesTheLog() {
        val m = model()
        m.send("hello")
        m.clear()
        assertTrue(m.state.value.messages.isEmpty())
    }

    @Test
    fun speakOnLaptopReports() {
        val m = model()
        m.speakOnLaptop("hello sir")
        assertEquals("Sent to the laptop to speak.", m.state.value.notice)
    }
}

class ControlModelTest {
    private val env = TestEnv()
    private fun model() = ControlModel(env.config, env.actions, env.scope, env.dispatcher)

    @Test
    fun refreshReadsVolumeAndMute() {
        env.bridge.onTool = { _, _ -> tr(true, "volume" to 33, "muted" to true) }
        val m = model()
        m.refresh()
        assertEquals(33, m.state.value.volume)
        assertEquals(true, m.state.value.muted)
    }

    @Test
    fun setVolumeIsOptimisticThenTakesTheTrueReadBack() {
        env.bridge.onTool = { name, args ->
            if (name == "volume_set") tr(true, "volume" to args.getInt("level") - 3, "muted" to false) else tr()
        }
        val m = model()
        m.setVolume(80)
        assertEquals(77, m.state.value.volume)
        assertEquals("Volume 77%", m.state.value.notice)
    }

    @Test
    fun setVolumeAllowsBoostUpTo150AndClamps() {
        val m = model()
        m.setVolume(400)
        assertEquals(150, env.bridge.toolCalls.last().second.getInt("level"))
    }

    @Test
    fun setVolumeFailureReReadsTheTrueLevel() {
        env.bridge.onTool = { name, _ ->
            if (name == "volume_set") tr(false, error = "pactl failed") else tr(true, "volume" to 20, "muted" to false)
        }
        val m = model()
        m.setVolume(90)
        assertEquals(20, m.state.value.volume)
    }

    @Test
    fun toggleMuteRollsBackOnFailure() {
        env.bridge.onTool = { _, _ -> tr(false, error = "pactl failed") }
        val m = model()
        m.toggleMute()
        assertEquals(false, m.state.value.muted)
        assertEquals("pactl failed", m.state.value.notice)
    }

    @Test
    fun toggleMuteSendsMuteThenUnmute() {
        env.bridge.onTool = { name, _ ->
            if (name == "volume_get") tr(true, "volume" to 50, "muted" to (env.bridge.toolCalls.count { it.first == "volume_mute" } > env.bridge.toolCalls.count { it.first == "volume_unmute" })) else tr()
        }
        val m = model()
        m.toggleMute()
        assertEquals(true, m.state.value.muted)
        m.toggleMute()
        assertEquals(false, m.state.value.muted)
        assertEquals(listOf("volume_mute", "volume_get", "volume_unmute", "volume_get"), env.bridge.toolCalls.map { it.first })
    }

    @Test
    fun mediaShowsNowPlaying() {
        env.bridge.onTool = { _, _ -> tr(true, "state" to "Playing") }
        val m = model()
        m.media(Tool.MEDIA_NEXT)
        assertEquals("Playing", m.state.value.nowPlaying)
    }

    @Test(expected = IllegalArgumentException::class)
    fun mediaRejectsNonMediaTools() {
        model().media(Tool.LOCK)
    }

    @Test
    fun openAppReportsBridgeSayOrFailure() {
        env.bridge.onTool = { _, _ -> tr(true, "app" to "firefox") }
        val m = model()
        m.openApp("firefox")
        assertEquals("Opened firefox", m.state.value.notice)
        env.bridge.onTool = { _, _ -> tr(false, error = "firefox not installed") }
        m.openApp("firefox")
        assertEquals("firefox not installed", m.state.value.notice)
        m.openApp("  ")
        assertEquals("Name an app first.", m.state.value.notice)
    }

    @Test
    fun typeTextAndKeys() {
        val m = model()
        m.typeText("hello")
        assertEquals("Typed", m.state.value.notice)
        m.typeText("w ".repeat(600))
        assertEquals("Typed (3 parts)", m.state.value.notice)
        m.pressKey("Return")
        assertEquals("Sent Return", m.state.value.notice)
        assertEquals(listOf("Return"), env.bridge.keys)
    }

    @Test
    fun guestSeesBlockedTypingMessageAndFlag() {
        env.prefs.guestMode = true
        val m = model()
        m.refresh()
        assertTrue(m.state.value.guest)
        m.typeText("x")
        assertTrue(m.state.value.notice.contains("Guest"))
        assertTrue(env.bridge.typed.isEmpty())
    }

    @Test
    fun pcMicToggleShowsState() {
        val m = model()
        m.togglePcMic()
        assertEquals(true, m.state.value.pcMicMuted)
        assertEquals("PC mic muted", m.state.value.notice)
        assertNull(model().state.value.pcMicMuted)
    }
}
