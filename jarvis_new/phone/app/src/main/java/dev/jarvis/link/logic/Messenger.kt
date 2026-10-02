package dev.jarvis.link.logic

import dev.jarvis.link.net.Bridge
import dev.jarvis.link.net.Limits

/** A text exchange result. */
data class Reply(
    val text: String,
    val warning: String? = null,
    val summoned: Boolean = false,
    val viaRoute: Boolean = false,
)

/**
 * Text path to Jarvis. [send] with `instant = true` tries `POST /route`
 * first (milliseconds, no LLM) and falls back to `POST /chat` when the
 * bridge says no-route; chat always carries the shared history.
 */
class Messenger(
    private val bridge: Bridge,
    private val actions: PcActions,
    private val conversation: Conversation,
    private val speaker: Speaker,
) {
    fun send(raw: String, instant: Boolean): Outcome<Reply> {
        val text = raw.trim()
        if (text.isEmpty()) return Outcome.Fail("Type a message first.")
        if (text.length > Limits.CHAT_MAX) {
            return Outcome.Fail("That is ${text.length} characters; the limit is ${Limits.CHAT_MAX}.")
        }
        val history = conversation.history() // before this turn
        conversation.add(Role.USER, text)

        if (instant && text.length <= Limits.ROUTE_MAX) {
            when (val r = attempt { bridge.route(text) }) {
                is Outcome.Ok -> r.value?.let { rr ->
                    actions.handleAction(rr.action)
                    conversation.add(Role.JARVIS, rr.reply)
                    speaker.speak(rr.reply)
                    return Outcome.Ok(Reply(rr.reply, viaRoute = true))
                }
                is Outcome.Fail -> if (r.fatal) return r
            }
        }
        return when (val r = attempt { bridge.chat(text, history) }) {
            is Outcome.Fail -> r
            is Outcome.Ok -> {
                val c = r.value
                actions.handleAction(c.action)
                when {
                    c.summoned -> Outcome.Ok(Reply("", summoned = true))
                    c.reply.isEmpty() -> Outcome.Ok(Reply("", warning = c.warning ?: "Jarvis had no reply."))
                    else -> {
                        conversation.add(Role.JARVIS, c.reply)
                        speaker.speak(c.reply)
                        Outcome.Ok(Reply(c.reply, c.warning))
                    }
                }
            }
        }
    }

    /** Have the laptop Jarvis say [text] aloud (`POST /summon {text}`). */
    fun speakOnLaptop(text: String): Outcome<Unit> {
        val t = text.trim().take(Limits.SEED_MAX)
        if (t.isEmpty()) return Outcome.Fail("Nothing to say.")
        return when (val r = attempt { bridge.summon(t) }) {
            is Outcome.Fail -> r
            is Outcome.Ok -> if (r.value.ok) Outcome.Ok(Unit)
            else Outcome.Fail(r.value.error ?: "The laptop did not answer.")
        }
    }

    /** Wake the laptop agent (`POST /summon`, no seed). */
    fun summon(): Outcome<Unit> = when (val r = attempt { bridge.summon(null) }) {
        is Outcome.Fail -> r
        is Outcome.Ok -> if (r.value.ok) Outcome.Ok(Unit)
        else Outcome.Fail(r.value.error ?: "The laptop did not answer.")
    }
}
