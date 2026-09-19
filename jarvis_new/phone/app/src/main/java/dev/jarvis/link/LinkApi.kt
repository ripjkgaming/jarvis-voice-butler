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

    fun typeText(text: String): JSONObject =
        post("/type", JSONObject().put("text", text))

    fun pressKey(key: String): JSONObject =
        post("/type", JSONObject().put("key", key))

    fun tool(tool: String, args: JSONObject = JSONObject()): JSONObject =
        post("/tool", JSONObject().put("tool", tool).put("args", args))

    fun talk(audioB64: String, rate: Int = 16000): TalkReply {
        val o = post("/talk", JSONObject().put("audio_b64", audioB64).put("rate", rate))
        return TalkReply(
            o.optString("transcript"),
            o.optString("reply"),
            o.optString("audio_b64"),
            o.optInt("audio_rate", 22050),
            o.optString("warning").ifEmpty { null },
        )
    }

    data class ChatReply(val reply: String, val warning: String?)

    fun chat(text: String, history: org.json.JSONArray = org.json.JSONArray()): ChatReply {
        val o = post("/chat", JSONObject().put("text", text).put("history", history))
        val reply = o.optString("reply")
        return ChatReply(
            reply.ifEmpty { o.optString("warning", "(no reply)") },
            o.optString("warning").ifEmpty { null },
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

    class ApiException(val code: Int, val body: String) : Exception("HTTP $code $body")
}
