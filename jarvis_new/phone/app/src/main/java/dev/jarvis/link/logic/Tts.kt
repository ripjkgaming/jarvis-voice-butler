package dev.jarvis.link.logic

/** Spoken-reply shaping for on-device TTS. Pure JVM. */
object TtsReply {
    const val MAX_CHARS = 300

    /** Jarvis speaks the first non-blank reply line, trimmed and capped. */
    fun firstLine(reply: String): String =
        reply.lineSequence().map { it.trim() }
            .firstOrNull { it.isNotEmpty() }?.take(MAX_CHARS) ?: ""
}
