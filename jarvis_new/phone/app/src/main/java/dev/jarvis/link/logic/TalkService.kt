package dev.jarvis.link.logic

import dev.jarvis.link.net.Bridge
import dev.jarvis.link.net.BridgeConfig
import dev.jarvis.link.net.Limits
import dev.jarvis.link.net.TalkReply
import java.util.Base64

/** What a push-to-talk exchange produced. */
data class TalkOutcome(
    val transcript: String,
    val reply: String,
    val audioB64: String,
    val audioRate: Int,
    val warning: String?,
) {
    /** The line to show for Jarvis: the reply, else the outage warning. */
    val display: String get() = reply.ifEmpty { warning ?: "(no reply)" }
}

/**
 * One spoken exchange: PCM -> `POST /talk` -> shown + spoken reply. Shared
 * by Home push-to-talk, the assistant session, the lock-screen one-shot
 * and the recognition service so they all validate, retry-free post, and
 * log identically.
 */
class TalkService(
    private val bridge: Bridge,
    private val actions: PcActions,
    private val conversation: Conversation,
    private val speaker: Speaker,
    private val player: AudioPlayer,
    private val config: () -> BridgeConfig,
) {
    /** Validate recorded audio against the bridge's limits. Null when fine. */
    fun check(pcm: ByteArray): String? = when {
        pcm.size < Limits.TALK_MIN_BYTES -> "That was too short. Hold the button and speak for at least a second."
        else -> null
    }

    /**
     * Transcribe + answer [pcm]. [play] false skips audio/TTS (the
     * recognition service only wants the transcript).
     */
    fun exchange(pcm: ByteArray, play: Boolean = true): Outcome<TalkOutcome> {
        check(pcm)?.let { return Outcome.Fail(it, code = CODE_TOO_SHORT) }
        val bounded = if (pcm.size > Limits.TALK_MAX_BYTES) pcm.copyOf(Limits.TALK_MAX_BYTES) else pcm
        val b64 = Base64.getEncoder().encodeToString(bounded)
        return when (val r = attempt { bridge.talk(b64, 16000) }) {
            is Outcome.Fail -> r
            is Outcome.Ok -> finish(r.value, play)
        }
    }

    private fun finish(t: TalkReply, play: Boolean): Outcome<TalkOutcome> {
        val out = TalkOutcome(t.transcript.trim(), t.reply, t.audioB64, t.audioRate, t.warning)
        if (out.transcript.isEmpty()) return Outcome.Fail("Didn't catch that. Try again a little closer to the mic.", code = CODE_NO_MATCH)
        actions.handleAction(t.action)
        conversation.add(Role.USER, out.transcript)
        if (out.reply.isNotEmpty()) conversation.add(Role.JARVIS, out.reply)
        if (play) {
            if (out.audioB64.isNotEmpty()) {
                try {
                    player.play(out.audioB64, out.audioRate)
                } catch (_: Exception) {
                    speaker.speak(out.display)
                }
            } else if (out.reply.isNotEmpty()) {
                speaker.speak(out.reply)
            }
        }
        return Outcome.Ok(out)
    }

    companion object {
        const val CODE_NO_MATCH = "no_match"
        const val CODE_TOO_SHORT = "too_short"
    }
}
