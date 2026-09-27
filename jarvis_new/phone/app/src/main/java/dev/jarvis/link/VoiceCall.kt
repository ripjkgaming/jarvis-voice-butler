package dev.jarvis.link

/**
 * Realtime voice-call value logic — Kotlin port of Flutter
 * `voice_ctrl.dart` (VoiceCtrl.connectParams + CallState). UI-free and
 * JVM-testable: the LiveKit Room itself lives in [VoiceCallManager].
 */
object VoiceCall {
    /** Join params minted by bridge POST /token. */
    data class TokenParams(val url: String, val token: String)

    /** Call states for the voice tab. LIVE = joined + mic hot. */
    enum class CallState { IDLE, JOINING, LIVE, ERROR }

    /**
     * Shape a POST /token payload into connect params. Accepts the
     * documented `{url, token}` shape and the bridge's
     * `{serverUrl, participantToken}` shape (what Flutter reads).
     * Null when the token was refused or the payload is malformed.
     */
    fun connectParams(payload: Map<String, Any?>): TokenParams? =
        connectParams(
            (payload["serverUrl"] ?: payload["url"]) as? String,
            (payload["participantToken"] ?: payload["token"]) as? String,
        )

    /** String overload for JSONObject call sites (fragments). */
    fun connectParams(url: String?, token: String?): TokenParams? {
        if (url.isNullOrEmpty() || token.isNullOrEmpty()) return null
        return TokenParams(url, token)
    }

    /** Bridge /token payloads arrive as org.json; convert without leaking
     *  Android JSON types into the pure path above. */
    fun connectParamsJson(o: org.json.JSONObject): TokenParams? =
        connectParams(
            o.optString("serverUrl").ifEmpty { o.optString("url") }.ifEmpty { null },
            o.optString("participantToken").ifEmpty { o.optString("token") }.ifEmpty { null },
        )
}
