package dev.jarvis.link

import android.content.Context
import android.content.SharedPreferences
import java.util.UUID

/** Connection settings. Host = PC tailnet IP (e.g. 100.77.6.93). */
class Prefs(context: Context) {
    private val sp: SharedPreferences =
        context.getSharedPreferences("jarvis_link", Context.MODE_PRIVATE)

    var host: String
        get() = sp.getString("host", "") ?: ""
        set(v) = sp.edit().putString("host", v.trim()).apply()
    var httpPort: Int
        get() = sp.getInt("http_port", 4317)
        set(v) = sp.edit().putInt("http_port", v).apply()
    var micPort: Int
        get() = sp.getInt("mic_port", 4318)
        set(v) = sp.edit().putInt("mic_port", v).apply()
    var token: String
        get() = sp.getString("token", "") ?: ""
        set(v) = sp.edit().putString("token", v.trim()).apply()
    var micUplink: Boolean
        get() = sp.getBoolean("mic_uplink", true)
        set(v) = sp.edit().putBoolean("mic_uplink", v).apply()
    /** Stable per-install session id (memory continuity with the PC). */
    var sessionId: String
        get() = sp.getString("session_id", null)
            ?: UUID.randomUUID().toString().also { sessionId = it }
        set(v) = sp.edit().putString("session_id", v).apply()
    /** Event cursor: last consumed action index, survives restarts. */
    var eventCursor: Long
        get() = sp.getLong("event_cursor", 0)
        set(v) = sp.edit().putLong("event_cursor", v).apply()
    /** Offline mode: local listening still works, bridge calls are held. */
    var offlineMode: Boolean
        get() = sp.getBoolean("offline_mode", false)
        set(v) = sp.edit().putBoolean("offline_mode", v).apply()
    /** Guest mode: cold persona, Q&A + media/volume only (server-enforced). */
    var guestMode: Boolean
        get() = sp.getBoolean("guest_mode", false)
        set(v) = sp.edit().putBoolean("guest_mode", v).apply()
    /** Boot autostart for LinkService + HotwordService (per toggle). */
    var autostart: Boolean
        get() = sp.getBoolean("autostart", true)
        set(v) = sp.edit().putBoolean("autostart", v).apply()
    /** Speak Jarvis replies aloud. */
    var ttsEnabled: Boolean
        get() = sp.getBoolean("tts_enabled", true)
        set(v) = sp.edit().putBoolean("tts_enabled", v).apply()
    /** Male, focus-friendly voice when the engine has one. */
    var ttsMale: Boolean
        get() = sp.getBoolean("tts_male", true)
        set(v) = sp.edit().putBoolean("tts_male", v).apply()
    /** On-device "Jarvis" hotword loop. */
    var hotwordEnabled: Boolean
        get() = sp.getBoolean("hotword_enabled", true)
        set(v) = sp.edit().putBoolean("hotword_enabled", v).apply()
    /** Hotword stands down while music/audio is playing. */
    var hotwordPauseOnMusic: Boolean
        get() = sp.getBoolean("hotword_pause_music", true)
        set(v) = sp.edit().putBoolean("hotword_pause_music", v).apply()
    /** Persisted Home chat stream (JSON array of [who, text], newest last). */
    var chatStream: String
        get() = sp.getString("chat_stream", "[]") ?: "[]"
        set(v) = sp.edit().putString("chat_stream", v).apply()
    /** Preferred RDP client package (empty = auto-pick first installed). */
    var remoteClient: String
        get() = sp.getString("remote_client", "") ?: ""
        set(v) = sp.edit().putString("remote_client", v.trim()).apply()

    val configured: Boolean get() = host.isNotEmpty() && token.isNotEmpty()
    val baseUrl: String get() = "http://${host}:${httpPort}"
}
