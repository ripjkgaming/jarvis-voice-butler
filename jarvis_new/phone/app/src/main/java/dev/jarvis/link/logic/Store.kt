package dev.jarvis.link.logic

import dev.jarvis.link.net.BridgeConfig
import java.util.UUID

/** Tiny key/value seam so settings logic runs on the JVM. */
interface KeyValueStore {
    fun getString(key: String, def: String?): String?
    fun putString(key: String, value: String)
    fun getBoolean(key: String, def: Boolean): Boolean
    fun putBoolean(key: String, value: Boolean)
    fun getInt(key: String, def: Int): Int
    fun putInt(key: String, value: Int)
    fun getLong(key: String, def: Long): Long
    fun putLong(key: String, value: Long)
}

class MemoryStore : KeyValueStore {
    private val m = HashMap<String, Any>()
    override fun getString(key: String, def: String?) = m[key] as? String ?: def
    override fun putString(key: String, value: String) { m[key] = value }
    override fun getBoolean(key: String, def: Boolean) = m[key] as? Boolean ?: def
    override fun putBoolean(key: String, value: Boolean) { m[key] = value }
    override fun getInt(key: String, def: Int) = m[key] as? Int ?: def
    override fun putInt(key: String, value: Int) { m[key] = value }
    override fun getLong(key: String, def: Long) = m[key] as? Long ?: def
    override fun putLong(key: String, value: Long) { m[key] = value }
}

/** All persisted settings. Setters sanitize; getters never throw. */
class Prefs(private val s: KeyValueStore) {
    var host: String
        get() = s.getString("host", "") ?: ""
        set(v) = s.putString("host", v.trim())
    var httpPort: Int
        get() = s.getInt("http_port", BridgeConfig.DEFAULT_PORT)
        set(v) = s.putInt("http_port", v)
    var micPort: Int
        get() = s.getInt("mic_port", BridgeConfig.DEFAULT_MIC_PORT)
        set(v) = s.putInt("mic_port", v)
    var token: String
        get() = s.getString("token", "") ?: ""
        set(v) = s.putString("token", BridgeConfig.normalizeToken(v))
    var micUplink: Boolean
        get() = s.getBoolean("mic_uplink", false)
        set(v) = s.putBoolean("mic_uplink", v)
    var offlineMode: Boolean
        get() = s.getBoolean("offline_mode", false)
        set(v) = s.putBoolean("offline_mode", v)
    var guestMode: Boolean
        get() = s.getBoolean("guest_mode", false)
        set(v) = s.putBoolean("guest_mode", v)
    var autostart: Boolean
        get() = s.getBoolean("autostart", true)
        set(v) = s.putBoolean("autostart", v)
    var ttsEnabled: Boolean
        get() = s.getBoolean("tts_enabled", true)
        set(v) = s.putBoolean("tts_enabled", v)
    var ttsMale: Boolean
        get() = s.getBoolean("tts_male", true)
        set(v) = s.putBoolean("tts_male", v)
    var hotwordEnabled: Boolean
        get() = s.getBoolean("hotword_enabled", false)
        set(v) = s.putBoolean("hotword_enabled", v)
    var hotwordPauseOnMusic: Boolean
        get() = s.getBoolean("hotword_pause_music", true)
        set(v) = s.putBoolean("hotword_pause_music", v)
    var remoteClient: String
        get() = s.getString("remote_client", "") ?: ""
        set(v) = s.putString("remote_client", v.trim())
    /** Persisted conversation (JSON array of [role, text, ts]). */
    var conversation: String
        get() = s.getString("conversation", "[]") ?: "[]"
        set(v) = s.putString("conversation", v)
    /** Stable per-install session id. */
    var sessionId: String
        get() = s.getString("session_id", null) ?: UUID.randomUUID().toString().also { sessionId = it }
        set(v) = s.putString("session_id", v)
    /** Last consumed action index; survives restarts. */
    var eventCursor: Long
        get() = s.getLong("event_cursor", 0)
        set(v) = s.putLong("event_cursor", v)

    fun config(): BridgeConfig = BridgeConfig(host, httpPort, token, guestMode, offlineMode)
}
