package dev.jarvis.link.net

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import java.io.IOException
import java.net.ConnectException
import java.net.NoRouteToHostException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.util.Base64
import java.util.concurrent.TimeUnit

/**
 * HTTP implementation of [Bridge] against bridge.py. Config is read on
 * every call so Settings changes apply immediately. Guest mode is stamped
 * onto the bodies the bridge checks (`/tool /type /route /talk /chat
 * /token /summon`). Idempotent GETs retry transient failures; POSTs are
 * never replayed (a repeated "lock" or "type" would be a second action).
 */
class BridgeClient(
    private val config: () -> BridgeConfig,
    private val http: OkHttpClient = defaultHttp(),
    private val sleeper: (Long) -> Unit = { Thread.sleep(it) },
) : Bridge {

    /** Raw HTTP outcome before status mapping. */
    class Raw(val code: Int, val json: JSONObject?, val bytes: ByteArray) {
        val ok: Boolean get() = code in 200..299
    }

    private fun cfg(): BridgeConfig {
        val c = config()
        if (c.offline) throw BridgeException(BridgeError.Offline)
        c.problem()?.let { throw BridgeException(BridgeError.NotConfigured(it)) }
        return c
    }

    private fun client(readTimeoutS: Long): OkHttpClient =
        http.newBuilder().readTimeout(readTimeoutS, TimeUnit.SECONDS)
            .callTimeout(readTimeoutS + 15, TimeUnit.SECONDS).build()

    private fun stamp(c: BridgeConfig, o: JSONObject): JSONObject =
        if (c.guest) o.put("guest", true) else o

    /** Execute one request. Throws only for transport-level failures and 401. */
    internal fun exec(
        method: String,
        path: String,
        body: JSONObject? = null,
        readTimeoutS: Long = 20,
        retries: Int = if (method == "GET") 2 else 0,
    ): Raw {
        val c = cfg()
        val url = try {
            Request.Builder().url(c.baseUrl + path)
        } catch (_: IllegalArgumentException) {
            throw BridgeException(BridgeError.NotConfigured(BridgeConfig.Problem.BAD_HOST))
        }
        val req = url.header("Authorization", "Bearer ${c.token}")
            .method(
                method,
                if (method == "GET") null
                else (body ?: JSONObject()).toString().toRequestBody(JSON),
            ).build()
        var attempt = 0
        while (true) {
            try {
                client(readTimeoutS).newCall(req).execute().use { r ->
                    val raw = readRaw(r)
                    if (raw.code == 401) throw BridgeException(BridgeError.Unauthorized)
                    if (attempt < retries && raw.code in 502..504) {
                        // fall through to retry below
                    } else {
                        return raw
                    }
                }
            } catch (e: BridgeException) {
                throw e
            } catch (e: IOException) {
                if (attempt >= retries) throw BridgeException(mapIo(e, c), e)
            }
            attempt++
            sleeper(400L * attempt)
        }
    }

    private fun readRaw(r: Response): Raw {
        val bytes = r.body?.bytes() ?: ByteArray(0)
        var json: JSONObject? = null
        val ct = r.header("Content-Type").orEmpty()
        if (ct.contains("json", ignoreCase = true) || (bytes.isNotEmpty() && bytes[0] == '{'.code.toByte())) {
            json = try {
                JSONObject(String(bytes, Charsets.UTF_8))
            } catch (_: JSONException) {
                null
            }
        }
        return Raw(r.code, json, bytes)
    }

    private fun mapIo(e: IOException, c: BridgeConfig): BridgeError = when (e) {
        is SocketTimeoutException -> BridgeError.Timeout(c.endpoint)
        is ConnectException, is NoRouteToHostException, is UnknownHostException ->
            BridgeError.Unreachable(c.endpoint)
        else -> {
            val m = e.message.orEmpty()
            if (m.contains("timeout", ignoreCase = true) || m.contains("timed out", ignoreCase = true)) {
                BridgeError.Timeout(c.endpoint)
            } else if (m.contains("unreachable", ignoreCase = true) || m.contains("refused", ignoreCase = true)) {
                BridgeError.Unreachable(c.endpoint)
            } else {
                BridgeError.Network(m.ifEmpty { e.javaClass.simpleName })
            }
        }
    }

    /** Map a non-2xx [raw] to a [BridgeError]. */
    internal fun errorFor(raw: Raw, c: BridgeConfig = config()): BridgeError {
        val detail = raw.json?.optString("error").orEmpty().takeIf { it != "null" }.orEmpty()
        return when (raw.code) {
            401 -> BridgeError.Unauthorized
            403 -> if (detail.startsWith("guest mode refuses")) {
                BridgeError.Guest(detail.removePrefix("guest mode refuses ").trim())
            } else BridgeError.Forbidden(detail)
            400 -> BridgeError.BadRequest(detail)
            404 -> BridgeError.NotFound(detail)
            in 500..599 -> BridgeError.Server(raw.code, detail)
            else -> if (raw.json == null) BridgeError.Malformed(c.endpoint)
            else BridgeError.Server(raw.code, detail)
        }
    }

    /** 2xx JSON object or a thrown [BridgeException]. */
    private fun ok(raw: Raw): JSONObject {
        if (!raw.ok) throw BridgeException(errorFor(raw))
        return raw.json ?: if (raw.bytes.isEmpty()) JSONObject()
        else throw BridgeException(BridgeError.Malformed(config().endpoint))
    }

    private fun get(path: String, timeoutS: Long = 20): JSONObject =
        ok(exec("GET", path, readTimeoutS = timeoutS))

    private fun post(path: String, body: JSONObject, timeoutS: Long = 30, guest: Boolean = true): JSONObject {
        val c = cfg()
        return ok(exec("POST", path, if (guest) stamp(c, body) else body, timeoutS))
    }

    // ── Bridge ───────────────────────────────────────────────────

    override fun health(): JSONObject = get("/health", 8)

    override fun status(): JSONObject = get("/status", 12)

    override fun captions(limit: Int): List<Caption> {
        val arr = get("/captions?limit=${limit.coerceIn(1, 50)}").optJSONArray("captions")
            ?: return emptyList()
        return (0 until arr.length()).mapNotNull { i ->
            val o = arr.optJSONObject(i) ?: return@mapNotNull null
            Caption(o.optLong("ts"), o.optString("role"), o.optString("text"))
        }
    }

    override fun room(): RoomInfo {
        val o = get("/room")
        // `room` is JSON null when no call exists; optString would yield "null".
        val room = if (o.isNull("room")) null else o.optString("room").trim().ifEmpty { null }
        return RoomInfo(room, o.optBoolean("waking", false), o.optBoolean("boot", false))
    }

    override fun summon(text: String?): Ack {
        val o = JSONObject()
        val seed = text?.trim().orEmpty()
        if (seed.isNotEmpty()) {
            if (seed.length > Limits.SEED_MAX) {
                throw BridgeException(BridgeError.BadRequest("Spoken text is limited to ${Limits.SEED_MAX} characters."))
            }
            o.put("text", seed)
        }
        val r = post("/summon", o)
        return Ack(r.optBoolean("ok", true), r.optString("error").ifEmpty { null })
    }

    override fun token(room: String, dispatch: Boolean): CallToken {
        val r = post("/token", JSONObject().put("room", room).put("dispatch", dispatch), 25)
        return parseToken(r)
    }

    override fun typeText(text: String) {
        val c = cfg()
        if (c.guest) throw BridgeException(BridgeError.Guest("type"))
        if (text.isEmpty()) throw BridgeException(BridgeError.BadRequest("Nothing to type."))
        // Bridge caps one /type at 500 chars: send longer text in order.
        for (piece in Limits.chunk(text)) {
            ok(exec("POST", "/type", JSONObject().put("text", piece), 15))
        }
    }

    override fun pressKey(key: String) {
        val c = cfg()
        if (c.guest) throw BridgeException(BridgeError.Guest("type"))
        if (key.isBlank() || key.length > Limits.KEY_MAX) {
            throw BridgeException(BridgeError.BadRequest("Bad key name."))
        }
        ok(exec("POST", "/type", JSONObject().put("key", key), 15))
    }

    override fun tool(name: String, args: JSONObject): ToolResult {
        val c = cfg()
        if (c.guest && !GuestPolicy.allows(name)) {
            throw BridgeException(BridgeError.Guest("'$name'"))
        }
        val raw = exec(
            "POST", "/tool",
            stamp(c, JSONObject().put("tool", name).put("args", args)),
            readTimeoutS = if (name == Tool.SCREENSHOT || name.startsWith("remote_")) 60 else 30,
        )
        // 400 with a JSON body is the bridge reporting a failed tool.
        if (raw.code == 400 && raw.json?.has("ok") == true) return toolResult(raw.json)
        val json = ok(raw)
        return toolResult(json)
    }

    private fun toolResult(j: JSONObject): ToolResult =
        ToolResult(j.optBoolean("ok", false), j.optString("error").takeIf { it.isNotEmpty() && it != "null" }, j)

    override fun route(text: String): RouteReply? {
        val t = text.trim()
        if (t.isEmpty() || t.length > Limits.ROUTE_MAX) return null
        val c = cfg()
        val raw = exec("POST", "/route", stamp(c, JSONObject().put("text", t)), 20)
        if (raw.code == 404) {
            val err = raw.json?.optString("error").orEmpty()
            if (err == "no-route") return null
            throw BridgeException(BridgeError.NotFound(if (err == "unknown route") "This PC bridge is too old for instant commands." else err))
        }
        val o = ok(raw)
        return RouteReply(o.optString("reply", "(done)"), parseAction(o.optJSONObject("action")))
    }

    override fun talk(audioB64: String, rate: Int): TalkReply {
        val o = post("/talk", JSONObject().put("audio_b64", audioB64).put("rate", rate), 120)
        return TalkReply(
            o.optString("transcript"),
            o.optString("reply"),
            o.optString("audio_b64"),
            o.optInt("audio_rate", 22050),
            o.optString("warning").ifEmpty { null },
            parseAction(o.optJSONObject("action")),
        )
    }

    override fun chat(text: String, history: List<List<String>>, voice: Boolean): ChatReply {
        val t = text.trim()
        if (t.isEmpty()) throw BridgeException(BridgeError.BadRequest("Nothing to send."))
        if (t.length > Limits.CHAT_MAX) {
            throw BridgeException(BridgeError.BadRequest("Messages are limited to ${Limits.CHAT_MAX} characters."))
        }
        if (voice && t.length > Limits.SEED_MAX) {
            throw BridgeException(BridgeError.BadRequest("Spoken messages are limited to ${Limits.SEED_MAX} characters."))
        }
        val arr = JSONArray()
        history.takeLast(Limits.HISTORY_MAX).forEach { (role, msg) ->
            arr.put(JSONArray().put(role).put(msg.take(1000)))
        }
        val o = post(
            "/chat",
            JSONObject().put("text", t).put("history", arr).put("voice", voice),
            120,
        )
        if (o.optString("voice") == "summoned") {
            return ChatReply("", null, true, parseAction(o.optJSONObject("action")))
        }
        val warning = o.optString("warning").ifEmpty { null }
        return ChatReply(o.optString("reply"), warning, false, parseAction(o.optJSONObject("action")))
    }

    override fun cameraFrame(jpeg: ByteArray) {
        if (jpeg.isEmpty()) throw BridgeException(BridgeError.BadRequest("Empty photo."))
        if (jpeg.size > Limits.CAMERA_MAX_BYTES) {
            throw BridgeException(BridgeError.BadRequest("Photo is larger than 8 MB."))
        }
        val b64 = Base64.getEncoder().encodeToString(jpeg)
        val raw = exec("POST", "/camera/frame", JSONObject().put("image_b64", b64), 60)
        if (!raw.ok) throw BridgeException(errorFor(raw))
    }

    override fun cameraLatest(): ByteArray? {
        val raw = exec("GET", "/camera/latest", readTimeoutS = 30)
        return when {
            raw.code == 404 -> null
            !raw.ok -> throw BridgeException(errorFor(raw))
            else -> raw.bytes
        }
    }

    override fun micMuted(): Boolean? {
        val o = get("/mic")
        return if (o.optBoolean("ok", true) && o.has("muted")) o.optBoolean("muted") else null
    }

    override fun setMicMuted(muted: Boolean): Ack {
        val o = post("/mic", JSONObject().put("muted", muted), guest = false)
        return Ack(o.optBoolean("ok", true), o.optString("error").ifEmpty { null })
    }

    override fun actions(limit: Int): List<String> {
        val arr = get("/actions?limit=${limit.coerceIn(1, 200)}").optJSONArray("actions")
            ?: return emptyList()
        return (0 until arr.length()).map { arr.opt(it)?.toString().orEmpty() }
    }

    override fun sys(): JSONObject = get("/sys")

    override fun postTelemetry(sample: BatterySample) {
        post("/phone/telemetry", JSONObject(telemetryPayload(sample)), 10, guest = false)
    }

    companion object {
        private val JSON = "application/json".toMediaType()

        fun defaultHttp(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(8, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .retryOnConnectionFailure(true)
            .build()

        /** Body for `POST /phone/telemetry`: battery clamped to 0..100. */
        fun telemetryPayload(s: BatterySample): String =
            "{\"battery\":${s.percent.coerceIn(0, 100)},\"charging\":${s.charging}}"

        /**
         * Accepts the documented `{url, token}` shape and the
         * `{serverUrl, participantToken}` shape; throws with the bridge's
         * own error text when the mint was refused.
         */
        fun parseToken(o: JSONObject): CallToken {
            val url = o.optString("serverUrl").ifEmpty { o.optString("url") }
            val tok = o.optString("participantToken").ifEmpty { o.optString("token") }
            if (url.isEmpty() || tok.isEmpty()) {
                throw BridgeException(
                    BridgeError.Server(503, o.optString("error").ifEmpty { "The bridge returned no call token." })
                )
            }
            return CallToken(url, tok)
        }

        fun parseAction(o: JSONObject?): ToolAction? {
            if (o == null) return null
            val tool = o.optString("tool")
            if (tool.isEmpty()) return null
            return ToolAction(
                tool = tool,
                ok = o.optBoolean("ok", false),
                error = o.optString("error").takeIf { it.isNotEmpty() && it != "null" },
                denied = o.optString("denied").ifEmpty { null },
                raw = o,
            )
        }
    }
}
