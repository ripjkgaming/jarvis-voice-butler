package dev.jarvis.link.logic

import dev.jarvis.link.net.Ack
import dev.jarvis.link.net.BridgeError
import dev.jarvis.link.net.ChatReply
import dev.jarvis.link.net.RouteReply
import dev.jarvis.link.net.ToolAction
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MessengerTest {
    private val env = TestEnv()

    @Test
    fun instantRouteWinsAndSkipsChat() {
        env.bridge.onRoute = { RouteReply("Locked, Sir.", ToolAction("lock", true)) }
        val r = (env.messenger.send("lock the pc", true) as Outcome.Ok).value
        assertTrue(r.viaRoute)
        assertEquals("Locked, Sir.", r.text)
        assertFalse(env.bridge.calls.contains("chat"))
        assertEquals(listOf("Locked, Sir."), env.speaker.said)
        assertEquals(listOf(Role.USER, Role.JARVIS), env.conversation.messages.value.map { it.role })
    }

    @Test
    fun noRouteFallsBackToChatWithHistoryBeforeThisTurn() {
        env.conversation.add(Role.USER, "earlier")
        env.conversation.add(Role.JARVIS, "answer")
        env.bridge.onRoute = { null }
        env.bridge.onChat = { _, _, _ -> ChatReply("Indeed.", null, false, null) }
        val r = (env.messenger.send("tell me more", true) as Outcome.Ok).value
        assertFalse(r.viaRoute)
        val (text, history, voice) = env.bridge.chatCalls.single()
        assertEquals("tell me more", text)
        assertEquals(listOf(listOf("user", "earlier"), listOf("jarvis", "answer")), history)
        assertFalse(voice)
        assertEquals("Indeed.", env.conversation.messages.value.last().text)
    }

    @Test
    fun transportFailureOnRouteDoesNotRetryViaChat() {
        env.bridge.onRoute = { bridgeFail(BridgeError.Unreachable("h:1")) }
        val f = env.messenger.send("volume up", true) as Outcome.Fail
        assertTrue(f.message.contains("Cannot reach"))
        assertFalse(env.bridge.calls.contains("chat"))
    }

    @Test
    fun oldBridgeWithoutRouteStillChats() {
        env.bridge.onRoute = { bridgeFail(BridgeError.NotFound("too old")) }
        assertTrue(env.messenger.send("hello", true) is Outcome.Ok)
        assertTrue(env.bridge.calls.contains("chat"))
    }

    @Test
    fun chatOnlyModeNeverRoutes() {
        env.messenger.send("hello", false)
        assertFalse(env.bridge.calls.contains("route"))
    }

    @Test
    fun routeActionOpensRemoteClient() {
        env.bridge.onRoute = {
            RouteReply("Remote ready.", ToolAction("remote_start", true, raw = JSONObject().put("host", "h").put("port", 1)))
        }
        env.messenger.send("start remote", true)
        assertEquals(listOf<Pair<String?, Int?>>("h" to 1), env.remote)
    }

    @Test
    fun brainOutageIsAWarningNotAReply() {
        env.bridge.onChat = { _, _, _ -> ChatReply("", "quota exhausted", false, null) }
        val r = (env.messenger.send("hello", false) as Outcome.Ok).value
        assertEquals("", r.text)
        assertEquals("quota exhausted", r.warning)
        assertEquals(1, env.conversation.messages.value.size) // only the user turn
        assertTrue(env.speaker.said.isEmpty())
    }

    @Test
    fun summonedVoiceCallIsReported() {
        env.bridge.onChat = { _, _, _ -> ChatReply("", null, true, null) }
        assertTrue((env.messenger.send("hello", false) as Outcome.Ok).value.summoned)
    }

    @Test
    fun blankAndOverlongTextAreRejectedLocally() {
        assertTrue(env.messenger.send("  ", true) is Outcome.Fail)
        assertTrue(env.messenger.send("x".repeat(2001), true) is Outcome.Fail)
        assertTrue(env.bridge.calls.isEmpty())
        assertTrue(env.conversation.messages.value.isEmpty())
    }

    @Test
    fun failedChatKeepsTheUserTurnForTheNextTry() {
        env.bridge.onChat = { _, _, _ -> bridgeFail(BridgeError.Timeout("h:1")) }
        val f = env.messenger.send("hello", false) as Outcome.Fail
        assertTrue(f.transient)
        assertEquals(listOf("hello"), env.conversation.messages.value.map { it.text })
    }

    @Test
    fun speakOnLaptopTrimsToSeedLimitAndReportsFailure() {
        var sent: String? = null
        env.bridge.onSummon = { sent = it; Ack(true, null) }
        assertTrue(env.messenger.speakOnLaptop("y".repeat(900)) is Outcome.Ok)
        assertEquals(500, sent!!.length)
        env.bridge.onSummon = { Ack(false, "not listening") }
        assertEquals("not listening", (env.messenger.speakOnLaptop("hi") as Outcome.Fail).message)
        assertTrue(env.messenger.speakOnLaptop(" ") is Outcome.Fail)
    }

    @Test
    fun plainSummonChecksAck() {
        assertTrue(env.messenger.summon() is Outcome.Ok)
        env.bridge.onSummon = { Ack(false, null) }
        assertTrue(env.messenger.summon() is Outcome.Fail)
    }
}
