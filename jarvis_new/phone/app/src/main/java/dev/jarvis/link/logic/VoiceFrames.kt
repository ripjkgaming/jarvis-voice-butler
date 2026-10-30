package dev.jarvis.link.logic

import org.json.JSONException
import org.json.JSONObject

/**
 * Wire protocol for the WebSocket voice transport (docs/voice-transport.md).
 * Pure JVM: no Android imports, so every rule here is unit-testable.
 */
object VoiceFrames {
    const val KIND_MIC: Byte = 0x01
    const val KIND_AGENT_AUDIO: Byte = 0x02
    const val KIND_VIDEO: Byte = 0x03
    const val MIC_BLOCK_BYTES = 1024
    const val MAX_FRAME_BYTES = 64 * 1024
    const val MIC_RATE = 16000
    const val AGENT_RATE = 24000
    const val TOPIC_CHAT = "lk.chat"
    const val CHAT_MAX_CHARS = 2000

    /** [KIND_MIC] followed by pcm[offset until offset + length]. */
    fun micFrame(pcm: ByteArray, offset: Int, length: Int): ByteArray {
        val out = ByteArray(length + 1)
        out[0] = KIND_MIC
        System.arraycopy(pcm, offset, out, 1, length)
        return out
    }

    /** [KIND_VIDEO] + 8-byte big-endian timestamp (ms) + JPEG. Null when oversize. */
    fun videoFrame(timestampMs: Long, jpeg: ByteArray): ByteArray? {
        val total = 1 + 8 + jpeg.size
        if (total > MAX_FRAME_BYTES) return null
        val out = ByteArray(total)
        out[0] = KIND_VIDEO
        for (i in 0 until 8) {
            out[1 + i] = ((timestampMs ushr (56 - 8 * i)) and 0xFF).toByte()
        }
        System.arraycopy(jpeg, 0, out, 9, jpeg.size)
        return out
    }

    /** PCM16 payload of a kind 0x02 frame, or null when the frame is not valid agent audio. */
    fun agentAudio(frame: ByteArray): ByteArray? {
        if (frame.size < 2 || frame.size > MAX_FRAME_BYTES) return null
        if (frame[0] != KIND_AGENT_AUDIO) return null
        val payloadLen = frame.size - 1
        if (payloadLen % 2 != 0) return null
        return frame.copyOfRange(1, frame.size)
    }

    /** Text frame for a typed chat line. Null when blank or too long. */
    fun chatFrame(text: String): String? {
        if (text.isBlank() || text.length > CHAT_MAX_CHARS) return null
        return JSONObject()
            .put("topic", TOPIC_CHAT)
            .put("payload", JSONObject().put("text", text))
            .toString()
    }

    fun pongFrame(): String = JSONObject().put("topic", "pong").put("payload", JSONObject()).toString()
}

sealed class TopicFrame {
    data class State(val agent: String, val user: String) : TopicFrame()
    data class Caption(val text: String, val final: Boolean) : TopicFrame()
    object Flush : TopicFrame()
    data class Hello(val identity: String, val room: String, val mode: String) : TopicFrame()
    /** jarvis-tasks: raw payload JSON text, untouched. */
    data class Tasks(val payloadJson: String) : TopicFrame()
    /** jarvis.voice.barge.v1, validated like the server does. */
    data class Barge(val kind: String, val userSeq: Long, val replySeq: Long) : TopicFrame()
    object Ping : TopicFrame()
    object Pong : TopicFrame()
    /** Any other topic, or a known topic whose payload did not validate. */
    data class Other(val topic: String) : TopicFrame()
}

object TopicFrames {
    private const val TOPIC_BARGE = "jarvis.voice.barge.v1"
    private val BARGE_KINDS = setOf("user_start", "reply_start")
    private const val MAX_SEQ = 1_000_000_000L

    /**
     * Parse one incoming text frame. Null when the text is oversize, not a JSON
     * object, or has no string "topic".
     */
    fun parse(text: String): TopicFrame? {
        if (text.toByteArray(Charsets.UTF_8).size > VoiceFrames.MAX_FRAME_BYTES) return null
        val obj = try {
            JSONObject(text)
        } catch (_: JSONException) {
            return null
        }
        val topic = obj.opt("topic") as? String ?: return null
        val payload = obj.opt("payload")
        val p = payload as? JSONObject
        return when (topic) {
            "state" -> {
                if (p == null) TopicFrame.Other(topic)
                else TopicFrame.State(p.optString("agent", ""), p.optString("user", ""))
            }
            "caption" -> {
                if (p == null || p.opt("text") !is String) TopicFrame.Other(topic)
                else TopicFrame.Caption(p.getString("text"), p.optBoolean("final", false))
            }
            "flush" -> TopicFrame.Flush
            "hello" -> {
                if (p == null) TopicFrame.Other(topic)
                else TopicFrame.Hello(p.optString("identity", ""), p.optString("room", ""), p.optString("mode", ""))
            }
            "jarvis-tasks" -> TopicFrame.Tasks(payload?.toString() ?: "null")
            TOPIC_BARGE -> barge(p) ?: TopicFrame.Other(topic)
            "ping" -> TopicFrame.Ping
            "pong" -> TopicFrame.Pong
            else -> TopicFrame.Other(topic)
        }
    }

    private fun barge(p: JSONObject?): TopicFrame.Barge? {
        if (p == null) return null
        val kind = p.opt("kind") as? String ?: return null
        if (kind !in BARGE_KINDS) return null
        val userSeq = seq(p.opt("user_seq")) ?: return null
        if (userSeq < 0 || userSeq > MAX_SEQ) return null
        var replySeq = 0L
        if (kind == "reply_start") {
            replySeq = seq(p.opt("reply_seq")) ?: return null
            if (replySeq <= 0 || replySeq > MAX_SEQ) return null
        }
        return TopicFrame.Barge(kind, userSeq, replySeq)
    }

    /** Accepts only JSON integers (org.json may hand back Int or Long). */
    private fun seq(v: Any?): Long? = when (v) {
        is Int -> v.toLong()
        is Long -> v
        else -> null
    }
}

enum class DisconnectReason(val message: String) {
    CLIENT_INITIATED("Call ended."),
    JOIN_FAILURE("Call token rejected."),
    ROOM_DELETED("The call room was closed."),
    DUPLICATE_IDENTITY("This device joined the call somewhere else."),
    PARTICIPANT_REMOVED("Access to the call was revoked."),
    CALL_ALREADY_ACTIVE("Another call is already active on the PC."),
    OBSERVER_VIOLATION("The call session sent a forbidden frame."),
    REPLAYED_TOKEN("The call token was already used."),
    LOST("Connection lost."),
}

sealed class CloseOutcome {
    /** 4010: the client asked for the close. Never reported as lost. */
    object Requested : CloseOutcome()
    /** Server-decided terminal close: report as lost with the reason, no reconnect. */
    data class Terminal(val reason: DisconnectReason) : CloseOutcome()
    /** Any other code, including abnormal drops: reported as lost. */
    data class Lost(val reason: DisconnectReason) : CloseOutcome()
}

object CloseCodes {
    const val BAD_TOKEN = 4001
    const val REPLAYED_TOKEN = 4002
    const val CALL_ALREADY_ACTIVE = 4003
    const val OBSERVER_VIOLATION = 4004
    const val CLIENT_REQUESTED = 4010
    const val ROOM_DELETED = 4011
    const val DUPLICATE_IDENTITY = 4012
    const val OWNER_REVOKED = 4013

    fun outcome(code: Int): CloseOutcome = when (code) {
        CLIENT_REQUESTED -> CloseOutcome.Requested
        BAD_TOKEN -> CloseOutcome.Terminal(DisconnectReason.JOIN_FAILURE)
        REPLAYED_TOKEN -> CloseOutcome.Terminal(DisconnectReason.REPLAYED_TOKEN)
        CALL_ALREADY_ACTIVE -> CloseOutcome.Terminal(DisconnectReason.CALL_ALREADY_ACTIVE)
        OBSERVER_VIOLATION -> CloseOutcome.Terminal(DisconnectReason.OBSERVER_VIOLATION)
        ROOM_DELETED -> CloseOutcome.Terminal(DisconnectReason.ROOM_DELETED)
        DUPLICATE_IDENTITY -> CloseOutcome.Terminal(DisconnectReason.DUPLICATE_IDENTITY)
        OWNER_REVOKED -> CloseOutcome.Terminal(DisconnectReason.PARTICIPANT_REMOVED)
        else -> CloseOutcome.Lost(DisconnectReason.LOST)
    }
}

/**
 * Agent audio buffer. Playback waits for [primeBytes] before starting and
 * restarts priming after an underflow; overflow drops the oldest audio so
 * latency stays bounded.
 */
class JitterBuffer(
    /** 120 ms of 24 kHz mono PCM16. */
    private val primeBytes: Int = 5760,
    /** 2 s of 24 kHz mono PCM16. */
    private val maxBytes: Int = 96000,
) {
    private val queue = ArrayDeque<ByteArray>()
    private var queued = 0
    private var playing = false

    @Synchronized
    fun push(pcm: ByteArray) {
        if (pcm.isEmpty()) return
        queue.addLast(pcm)
        queued += pcm.size
        while (queued > maxBytes && queue.isNotEmpty()) {
            val head = queue.first()
            val excess = queued - maxBytes
            if (head.size <= excess) {
                queue.removeFirst()
                queued -= head.size
            } else {
                queue[0] = head.copyOfRange(excess, head.size)
                queued -= excess
            }
        }
    }

    /** Exactly [length] bytes; silence (zeros) for whatever is not available. */
    @Synchronized
    fun read(length: Int): ByteArray {
        val out = ByteArray(length)
        if (length <= 0) return out
        if (!playing) {
            if (queued < primeBytes) return out
            playing = true
        }
        var o = 0
        while (o < length && queue.isNotEmpty()) {
            val head = queue.first()
            val n = minOf(head.size, length - o)
            System.arraycopy(head, 0, out, o, n)
            o += n
            queued -= n
            if (n == head.size) queue.removeFirst() else queue[0] = head.copyOfRange(n, head.size)
        }
        if (o < length) playing = false
        return out
    }

    @Synchronized
    fun flush() {
        queue.clear()
        queued = 0
        playing = false
    }

    @Synchronized
    fun buffered(): Int = queued
}
