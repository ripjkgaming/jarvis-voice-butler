package dev.jarvis.link.logic

import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceFramesTest {

    @Test
    fun micFrameIsKindPrefixedSlice() {
        val pcm = byteArrayOf(9, 1, 2, 3, 9)
        assertArrayEquals(byteArrayOf(0x01, 1, 2, 3), VoiceFrames.micFrame(pcm, 1, 3))
    }

    @Test
    fun videoFrameUsesBigEndianTimestamp() {
        val frame = VoiceFrames.videoFrame(0x0102030405L, byteArrayOf(0x7F))!!
        assertArrayEquals(
            byteArrayOf(0x03, 0, 0, 0, 0x01, 0x02, 0x03, 0x04, 0x05, 0x7F),
            frame,
        )
    }

    @Test
    fun videoFrameRejectsOversize() {
        assertNull(VoiceFrames.videoFrame(1L, ByteArray(VoiceFrames.MAX_FRAME_BYTES)))
        assertNotNull(VoiceFrames.videoFrame(1L, ByteArray(VoiceFrames.MAX_FRAME_BYTES - 9)))
    }

    @Test
    fun agentAudioAcceptsEvenPcmOnly() {
        assertArrayEquals(byteArrayOf(1, 2), VoiceFrames.agentAudio(byteArrayOf(0x02, 1, 2)))
        assertNull(VoiceFrames.agentAudio(byteArrayOf(0x02, 1, 2, 3)))
        assertNull(VoiceFrames.agentAudio(byteArrayOf(0x01, 1, 2)))
        assertNull(VoiceFrames.agentAudio(byteArrayOf(0x02)))
        assertNull(VoiceFrames.agentAudio(ByteArray(VoiceFrames.MAX_FRAME_BYTES + 2) { if (it == 0) 0x02 else 0 }))
    }

    @Test
    fun chatFrameShape() {
        val obj = JSONObject(VoiceFrames.chatFrame("hi there")!!)
        assertEquals("lk.chat", obj.getString("topic"))
        assertEquals("hi there", obj.getJSONObject("payload").getString("text"))
    }

    @Test
    fun chatFrameRejectsBlankAndLong() {
        assertNull(VoiceFrames.chatFrame("   "))
        assertNull(VoiceFrames.chatFrame("x".repeat(VoiceFrames.CHAT_MAX_CHARS + 1)))
        assertNotNull(VoiceFrames.chatFrame("x".repeat(VoiceFrames.CHAT_MAX_CHARS)))
    }

    @Test
    fun parsesStateCaptionFlushHello() {
        assertEquals(
            TopicFrame.State("speaking", "listening"),
            TopicFrames.parse("""{"topic":"state","payload":{"agent":"speaking","user":"listening"}}"""),
        )
        assertEquals(
            TopicFrame.Caption("hello", true),
            TopicFrames.parse("""{"topic":"caption","payload":{"text":"hello","final":true}}"""),
        )
        assertEquals(TopicFrame.Flush, TopicFrames.parse("""{"topic":"flush","payload":{}}"""))
        assertEquals(
            TopicFrame.Hello("me", "room-1", "speaker"),
            TopicFrames.parse("""{"topic":"hello","payload":{"identity":"me","room":"room-1","mode":"speaker"}}"""),
        )
    }

    @Test
    fun parsesTasksPingPong() {
        assertEquals(
            TopicFrame.Tasks("""{"id":3}"""),
            TopicFrames.parse("""{"topic":"jarvis-tasks","payload":{"id":3}}"""),
        )
        assertEquals(TopicFrame.Ping, TopicFrames.parse("""{"topic":"ping","payload":{}}"""))
        assertEquals(TopicFrame.Pong, TopicFrames.parse("""{"topic":"pong","payload":{}}"""))
    }

    @Test
    fun knownTopicWithBadPayloadIsOther() {
        assertEquals(TopicFrame.Other("caption"), TopicFrames.parse("""{"topic":"caption","payload":{"text":5}}"""))
        assertEquals(TopicFrame.Other("state"), TopicFrames.parse("""{"topic":"state"}"""))
    }

    @Test
    fun unknownTopicIsOther() {
        assertEquals(TopicFrame.Other("mystery"), TopicFrames.parse("""{"topic":"mystery","payload":1}"""))
    }

    @Test
    fun malformedOrTopicless_isNull() {
        assertNull(TopicFrames.parse("not json"))
        assertNull(TopicFrames.parse("[1,2]"))
        assertNull(TopicFrames.parse("""{"payload":{}}"""))
        assertNull(TopicFrames.parse("""{"topic":7}"""))
    }

    @Test
    fun oversizeTextIsNull() {
        val big = """{"topic":"jarvis-tasks","payload":"${"a".repeat(VoiceFrames.MAX_FRAME_BYTES)}"}"""
        assertNull(TopicFrames.parse(big))
    }

    @Test
    fun bargeValidationMatchesServer() {
        assertEquals(
            TopicFrame.Barge("user_start", 4, 0),
            TopicFrames.parse("""{"topic":"jarvis.voice.barge.v1","payload":{"kind":"user_start","user_seq":4}}"""),
        )
        assertEquals(
            TopicFrame.Barge("reply_start", 4, 2),
            TopicFrames.parse("""{"topic":"jarvis.voice.barge.v1","payload":{"kind":"reply_start","user_seq":4,"reply_seq":2}}"""),
        )
        // reply_start needs a positive reply_seq.
        assertEquals(
            TopicFrame.Other("jarvis.voice.barge.v1"),
            TopicFrames.parse("""{"topic":"jarvis.voice.barge.v1","payload":{"kind":"reply_start","user_seq":4,"reply_seq":0}}"""),
        )
        // Unknown kind, negative seq, float seq, and string seq are all rejected.
        assertEquals(
            TopicFrame.Other("jarvis.voice.barge.v1"),
            TopicFrames.parse("""{"topic":"jarvis.voice.barge.v1","payload":{"kind":"stop","user_seq":1}}"""),
        )
        assertEquals(
            TopicFrame.Other("jarvis.voice.barge.v1"),
            TopicFrames.parse("""{"topic":"jarvis.voice.barge.v1","payload":{"kind":"user_start","user_seq":-1}}"""),
        )
        assertEquals(
            TopicFrame.Other("jarvis.voice.barge.v1"),
            TopicFrames.parse("""{"topic":"jarvis.voice.barge.v1","payload":{"kind":"user_start","user_seq":1.5}}"""),
        )
        assertEquals(
            TopicFrame.Other("jarvis.voice.barge.v1"),
            TopicFrames.parse("""{"topic":"jarvis.voice.barge.v1","payload":{"kind":"user_start","user_seq":"1"}}"""),
        )
    }

    @Test
    fun closeCodesMapToTerminalReasons() {
        assertEquals(CloseOutcome.Requested, CloseCodes.outcome(4010))
        assertEquals(CloseOutcome.Terminal(DisconnectReason.JOIN_FAILURE), CloseCodes.outcome(4001))
        assertEquals(CloseOutcome.Terminal(DisconnectReason.REPLAYED_TOKEN), CloseCodes.outcome(4002))
        assertEquals(CloseOutcome.Terminal(DisconnectReason.CALL_ALREADY_ACTIVE), CloseCodes.outcome(4003))
        assertEquals(CloseOutcome.Terminal(DisconnectReason.OBSERVER_VIOLATION), CloseCodes.outcome(4004))
        assertEquals(CloseOutcome.Terminal(DisconnectReason.ROOM_DELETED), CloseCodes.outcome(4011))
        assertEquals(CloseOutcome.Terminal(DisconnectReason.DUPLICATE_IDENTITY), CloseCodes.outcome(4012))
        assertEquals(CloseOutcome.Terminal(DisconnectReason.PARTICIPANT_REMOVED), CloseCodes.outcome(4013))
    }

    @Test
    fun unlistedCodesAreLost() {
        assertEquals(CloseOutcome.Lost(DisconnectReason.LOST), CloseCodes.outcome(1006))
        assertEquals(CloseOutcome.Lost(DisconnectReason.LOST), CloseCodes.outcome(1000))
        assertEquals(CloseOutcome.Lost(DisconnectReason.LOST), CloseCodes.outcome(4000))
    }

    @Test
    fun jitterBufferPrimesBeforePlaying() {
        val jb = JitterBuffer(primeBytes = 4, maxBytes = 100)
        jb.push(byteArrayOf(1, 2))
        assertArrayEquals(ByteArray(3), jb.read(3))
        assertEquals(2, jb.buffered())
        jb.push(byteArrayOf(3, 4))
        assertArrayEquals(byteArrayOf(1, 2, 3), jb.read(3))
        assertEquals(1, jb.buffered())
    }

    @Test
    fun jitterBufferUnderflowIsSilenceAndRePrimes() {
        val jb = JitterBuffer(primeBytes = 2, maxBytes = 100)
        jb.push(byteArrayOf(5, 6))
        assertArrayEquals(byteArrayOf(5, 6, 0, 0), jb.read(4))
        jb.push(byteArrayOf(7))
        // One byte is below the prime threshold again: silence until it refills.
        assertArrayEquals(ByteArray(2), jb.read(2))
        jb.push(byteArrayOf(8))
        assertArrayEquals(byteArrayOf(7, 8), jb.read(2))
    }

    @Test
    fun jitterBufferOverflowDropsOldest() {
        val jb = JitterBuffer(primeBytes = 1, maxBytes = 4)
        jb.push(byteArrayOf(1, 2, 3))
        jb.push(byteArrayOf(4, 5, 6))
        assertEquals(4, jb.buffered())
        assertArrayEquals(byteArrayOf(3, 4, 5, 6), jb.read(4))
    }

    @Test
    fun jitterBufferFlushClearsAndRePrimes() {
        val jb = JitterBuffer(primeBytes = 2, maxBytes = 100)
        jb.push(byteArrayOf(1, 2, 3, 4))
        assertArrayEquals(byteArrayOf(1, 2), jb.read(2))
        jb.flush()
        assertEquals(0, jb.buffered())
        jb.push(byteArrayOf(9))
        assertTrue(jb.read(1).all { it == 0.toByte() })
    }
}
