package dev.jarvis.link

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/** HTTP client for the PC bridge phone API. All calls are synchronous;
 *  callers must run them off the main thread (fragments use threads). */
class LinkApi(prefs: Prefs) {
    private val base = prefs.baseUrl
    private val token = prefs.token
    private val guest = prefs.guestMode

    /** Stamp guest mode onto control/voice bodies (server-enforced). */
    private fun g(o: JSONObject): JSONObject =
        if (guest) o.put("guest", true) else o
    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(90, TimeUnit.SECONDS)
        .build()
    private val json = "application/json".toMediaType()

    data class TalkReply(
        val transcript: String,
        val reply: String,
        val audioB64: String,
        val audioRate: Int,
        val warning: String?,
        val action: JSONObject? = null,
    )

    private fun req(path: String, body: RequestBody?) =
        Request.Builder().url(base + path)
            .header("Authorization", "Bearer $token")
            .method(if (body == null) "GET" else "POST", body)
            .build()

    private fun post(path: String, obj: JSONObject): JSONObject {
        client.newCall(req(path, obj.toString().toRequestBody(json))).execute().use {
            if (!it.isSuccessful) throw ApiException(it.code, it.body?.string() ?: "")
            return JSONObject(it.body?.string() ?: "{}")
        }
    }

    private fun get(path: String): JSONObject {
        client.newCall(req(path, null)).execute().use {
            if (!it.isSuccessful) throw ApiException(it.code, it.body?.string() ?: "")
            return JSONObject(it.body?.string() ?: "{}")
        }
    }

    fun health(): JSONObject = get("/health")
    fun status(): JSONObject = get("/status")

    /** Live transcript tail (newest first on the bridge). Empty when unreachable. */
    fun captions(limit: Int = 20): JSONObject = get("/captions?limit=$limit")

    /** Live laptop HUD room, if a call exists there. Flutter link_api.dart room(). */
    fun room(): JSONObject = get("/room")

    /** Summon laptop Jarvis; optional spoken-text seed (Flutter summonSpoken). */
    fun summon(text: String? = null): JSONObject {
        val o = JSONObject()
        if (!text.isNullOrEmpty()) o.put("text", text)
        return post("/summon", g(o))
    }

    /** Mint a 15-minute LiveKit join token (Flutter livekitToken). */
    fun livekitToken(room: String = "", dispatch: Boolean = true): JSONObject =
        post("/token", g(JSONObject().put("room", room).put("dispatch", dispatch)))

    fun typeText(text: String): JSONObject =
        post("/type", g(JSONObject().put("text", text)))

    fun pressKey(key: String): JSONObject =
        post("/type", g(JSONObject().put("key", key)))

    fun tool(tool: String, args: JSONObject = JSONObject()): JSONObject =
        post("/tool", g(JSONObject().put("tool", tool).put("args", args)))

    class NoRouteException(msg: String) : Exception(msg)

    /** Instant command path (no LLM): reply text, or NoRoute → use chat. */
    fun route(text: String): String = routeFull(text).reply

    data class RouteReply(val reply: String, val action: JSONObject? = null)

    /** Instant command path with the bridge `action` surfaced (remote_start…). */
    fun routeFull(text: String): RouteReply {
        try {
            val o = post("/route", g(JSONObject().put("text", text)))
            return RouteReply(o.optString("reply", "(done)"), o.optJSONObject("action"))
        } catch (e: ApiException) {
            if (e.code == 404) throw NoRouteException(e.message?.ifEmpty { "no-route" } ?: "no-route")
            throw e
        }
    }

    fun talk(audioB64: String, rate: Int = 16000): TalkReply {
        val o = post("/talk", g(JSONObject().put("audio_b64", audioB64).put("rate", rate)))
        return TalkReply(
            o.optString("transcript"),
            o.optString("reply"),
            o.optString("audio_b64"),
            o.optInt("audio_rate", 22050),
            o.optString("warning").ifEmpty { null },
            o.optJSONObject("action"),
        )
    }

    data class ChatReply(
        val reply: String,
        val warning: String?,
        val summoned: Boolean = false,
        val action: JSONObject? = null,
    )

    fun chat(
        text: String,
        history: org.json.JSONArray = org.json.JSONArray(),
        voice: Boolean = false,
    ): ChatReply {
        val o = post(
            "/chat",
            g(JSONObject().put("text", text).put("history", history).put("voice", voice)),
        )
        if (o.optString("voice") == "summoned") {
            return ChatReply("(voice call summoned — answer comes spoken)", null, true, o.optJSONObject("action"))
        }
        val reply = o.optString("reply")
        return ChatReply(
            reply.ifEmpty { o.optString("warning", "(no reply)") },
            o.optString("warning").ifEmpty { null },
            false,
            o.optJSONObject("action"),
        )
    }

    fun cameraFrame(imageB64: String): JSONObject =
        post("/camera/frame", JSONObject().put("image_b64", imageB64))

    fun cameraLatest(): ByteArray {
        val r = Request.Builder().url(base + "/camera/latest")
            .header("Authorization", "Bearer $token").get().build()
        client.newCall(r).execute().use {
            if (!it.isSuccessful) throw ApiException(it.code, "")
            return it.body?.bytes() ?: ByteArray(0)
        }
    }

    fun micStatus(): JSONObject = get("/mic")
    fun setMuted(muted: Boolean): JSONObject =
        post("/mic", JSONObject().put("muted", muted))

    /** Recent PC action log lines (newest last). Empty list when unreachable. */
    fun actions(limit: Int = 100): List<String> {
        val arr = get("/actions?limit=$limit").optJSONArray("actions") ?: return emptyList()
        return (0 until arr.length()).map { arr.opt(it)?.toString() ?: "" }
    }

    fun sys(): JSONObject = get("/sys")

    companion object {
        /** Pure JSON payload builder for POST /phone/telemetry.
         *  Battery is clamped to 0..100. No Android APIs — JVM-testable. */
        fun telemetryPayload(battery: Int, charging: Boolean): String =
            "{\"battery\":${battery.coerceIn(0, 100)},\"charging\":$charging}"
    }

    /** Report real phone battery to the laptop bridge. Synchronous;
     *  callers must run off the main thread. Fails via ApiException. */
    fun postTelemetry(battery: Int, charging: Boolean): JSONObject =
        post("/phone/telemetry", JSONObject(telemetryPayload(battery, charging)))

    class ApiException(val code: Int, val body: String) : Exception("HTTP $code $body")
}
