package dev.jarvis.link.net

import org.json.JSONObject

/** Tool names accepted by `POST /tool` (run_phone_tool in bridge.py). */
object Tool {
    const val VOLUME_GET = "volume_get"
    const val VOLUME_UP = "volume_up"
    const val VOLUME_DOWN = "volume_down"
    const val VOLUME_SET = "volume_set"
    const val VOLUME_MUTE = "volume_mute"
    const val VOLUME_UNMUTE = "volume_unmute"
    const val MEDIA_PLAY_PAUSE = "media_play_pause"
    const val MEDIA_NEXT = "media_next"
    const val MEDIA_PREV = "media_prev"
    const val PLAY_MEDIA = "play_media"
    const val OPEN_APP = "open_app"
    const val CLOSE_APP = "close_app"
    const val SCREENSHOT = "screenshot"
    const val NOTIFY = "notify"
    const val LOCK = "lock"
    const val UNLOCK = "unlock"
    const val SCREENS_STATE = "screens_state"
    /** Blackout one output (or all when no output given). NOTE: bridge has no `screens_off`. */
    const val SCREEN_OFF = "screen_off"
    const val SCREEN_ON = "screen_on"
    const val SCREENS_RESTORE = "screens_restore"
    const val REMOTE_START = "remote_start"
    const val REMOTE_STOP = "remote_stop"
    const val REMOTE_STATUS = "remote_status"

    // Argument builders: keep key names in one place.
    fun volumeSet(level: Int): JSONObject =
        JSONObject().put("level", level.coerceIn(0, Limits.VOLUME_MAX))

    fun openApp(name: String): JSONObject = JSONObject().put("app", name.trim())

    fun playMedia(query: String): JSONObject = JSONObject().put("query", query.trim())

    fun screenshot(output: String?): JSONObject {
        val o = JSONObject()
        if (!output.isNullOrBlank() && !output.equals("all", ignoreCase = true)) o.put("output", output)
        return o
    }

    fun output(name: String?): JSONObject {
        val o = JSONObject()
        if (!name.isNullOrBlank()) o.put("output", name)
        return o
    }

    fun notify(title: String, body: String): JSONObject =
        JSONObject()
            .put("title", title.take(Limits.NOTIFY_TITLE))
            .put("body", body.take(Limits.NOTIFY_BODY))
}

/**
 * Mirror of `_GUEST_VOICE_TOOLS` in bridge.py. The bridge enforces it
 * (403); checking here too gives instant feedback and keeps guest-mode
 * buttons disabled instead of failing after a round trip.
 */
object GuestPolicy {
    val ALLOWED_TOOLS: Set<String> = setOf(
        Tool.VOLUME_GET, Tool.VOLUME_UP, Tool.VOLUME_DOWN, Tool.VOLUME_MUTE,
        Tool.VOLUME_UNMUTE, Tool.VOLUME_SET, Tool.MEDIA_PLAY_PAUSE, Tool.MEDIA_NEXT,
        Tool.MEDIA_PREV, Tool.PLAY_MEDIA,
    )

    fun allows(tool: String): Boolean = tool in ALLOWED_TOOLS

    /** `/type` is refused outright for guests. */
    const val TYPING_ALLOWED = false
}

/** Keys accepted by the on-screen keypad, as wtype/xkb names (NOT "Enter"). */
object Keys {
    const val ENTER = "Return"
    const val ESCAPE = "Escape"
    const val TAB = "Tab"
    const val UP = "Up"
    const val DOWN = "Down"
    const val LEFT = "Left"
    const val RIGHT = "Right"
    const val BACKSPACE = "BackSpace"
}
