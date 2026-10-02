package dev.jarvis.link.net

/**
 * Size limits enforced by bridge.py. Checking them on the phone turns a
 * confusing HTTP 400 into an immediate, specific message (and lets long
 * text be split instead of rejected).
 */
object Limits {
    /** `_TYPE_MAX` in bridge.py. */
    const val TYPE_MAX = 500
    /** handle_chat: text 1..2000. */
    const val CHAT_MAX = 2000
    /** Voice seeds (/summon text, /chat voice=true) cap at 500. */
    const val SEED_MAX = 500
    /** handle_route: text 1..500. */
    const val ROUTE_MAX = 500
    /** handle_type: key name max 40 chars. */
    const val KEY_MAX = 40
    /** handle_talk: >= 0.1 s of 16 kHz mono PCM16 (3200 bytes). */
    const val TALK_MIN_BYTES = 3200
    /** `_TALK_AUDIO_MAX`. */
    const val TALK_MAX_BYTES = 2 * 1024 * 1024
    /** store_camera_frame: <= 8 MB decoded. */
    const val CAMERA_MAX_BYTES = 8 * 1024 * 1024
    /** handle_chat: only the last 20 history turns count. */
    const val HISTORY_MAX = 20
    /** notify tool: title 120, body 300. */
    const val NOTIFY_TITLE = 120
    const val NOTIFY_BODY = 300
    /** volume_set: 0..150. */
    const val VOLUME_MAX = 150

    /** Split [text] into pieces of at most [max] chars, preferring whitespace. */
    fun chunk(text: String, max: Int = TYPE_MAX): List<String> {
        if (text.isEmpty()) return emptyList()
        val out = mutableListOf<String>()
        var rest = text
        while (rest.length > max) {
            var cut = rest.lastIndexOf(' ', max - 1)
            if (cut < max / 2) cut = max
            else cut += 1 // keep the space with the left piece so typing is lossless
            // never split a surrogate pair
            if (cut < rest.length && Character.isLowSurrogate(rest[cut])) cut -= 1
            out.add(rest.substring(0, cut))
            rest = rest.substring(cut)
        }
        if (rest.isNotEmpty()) out.add(rest)
        return out
    }
}
