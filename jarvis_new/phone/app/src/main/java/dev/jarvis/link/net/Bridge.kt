package dev.jarvis.link.net

import org.json.JSONObject

/** One line of the live transcript (`GET /captions`). */
data class Caption(val ts: Long, val role: String, val text: String)

/** `GET /room`: the live laptop HUD call, if any. */
data class RoomInfo(val room: String?, val waking: Boolean, val boot: Boolean)

/** `POST /token`: LiveKit join parameters. */
data class CallToken(val url: String, val token: String)

/** A `tool`/`action` descriptor the bridge attaches to routed replies. */
data class ToolAction(
    val tool: String,
    val ok: Boolean,
    val error: String? = null,
    val denied: String? = null,
    val raw: JSONObject? = null,
)

/** Result of `POST /tool`. HTTP 400 from the bridge is a tool failure, not a bad request. */
data class ToolResult(val ok: Boolean, val error: String?, val json: JSONObject) {
    val volume: Int? get() = if (json.has("volume") && !json.isNull("volume")) json.optInt("volume") else null
    val muted: Boolean? get() = if (json.has("muted") && !json.isNull("muted")) json.optBoolean("muted") else null
    val imageB64: String? get() = json.optString("image_b64").ifEmpty { null }
    val state: String? get() = json.optString("state").takeIf { it.isNotEmpty() && it != "null" }
    val say: String? get() = json.optString("say").takeIf { it.isNotEmpty() && it != "null" }
    val app: String? get() = json.optString("app").takeIf { it.isNotEmpty() && it != "null" }
    val host: String? get() = json.optString("host").takeIf { it.isNotEmpty() && it != "null" }

    /** `screens_state` outputs. */
    val outputs: List<ScreenOutput>
        get() {
            val arr = json.optJSONArray("outputs") ?: return emptyList()
            return (0 until arr.length()).mapNotNull { i ->
                when (val o = arr.opt(i)) {
                    is JSONObject -> o.optString("name").takeIf { it.isNotEmpty() }?.let {
                        ScreenOutput(it, if (o.has("enabled")) o.optBoolean("enabled") else null)
                    }
                    is String -> ScreenOutput(o, null)
                    else -> null
                }
            }
        }
}

data class ScreenOutput(val name: String, val enabled: Boolean?)

/** `POST /route` reply. A null `action` means a confirm/clarify answer. */
data class RouteReply(val reply: String, val action: ToolAction?)

data class TalkReply(
    val transcript: String,
    val reply: String,
    val audioB64: String,
    val audioRate: Int,
    val warning: String?,
    val action: ToolAction?,
)

data class ChatReply(
    val reply: String,
    val warning: String?,
    val summoned: Boolean,
    val action: ToolAction?,
)

/** Result of `POST /summon` and `POST /mic`. */
data class Ack(val ok: Boolean, val error: String?)

/** Phone battery pushed to `POST /phone/telemetry`. */
data class BatterySample(val percent: Int, val charging: Boolean)

/**
 * The bridge contract as the phone uses it. [BridgeClient] is the HTTP
 * implementation; every model takes this interface so tests inject fakes.
 * All calls block and throw [BridgeException]; callers run them off the
 * main thread.
 */
interface Bridge {
    fun health(): JSONObject
    fun status(): JSONObject
    fun captions(limit: Int = 20): List<Caption>
    fun room(): RoomInfo
    fun summon(text: String? = null): Ack
    fun token(room: String = "", dispatch: Boolean = true): CallToken
    fun typeText(text: String)
    fun pressKey(key: String)
    fun tool(name: String, args: JSONObject = JSONObject()): ToolResult

    /** Null when the bridge answers 404 no-route (caller falls back to chat). */
    fun route(text: String): RouteReply?
    fun talk(audioB64: String, rate: Int = 16000): TalkReply
    fun chat(text: String, history: List<List<String>> = emptyList(), voice: Boolean = false): ChatReply
    fun cameraFrame(jpeg: ByteArray)
    fun cameraLatest(): ByteArray?
    fun micMuted(): Boolean?
    fun setMicMuted(muted: Boolean): Ack
    fun actions(limit: Int = 100): List<String>
    fun sys(): JSONObject
    fun postTelemetry(sample: BatterySample)
}
