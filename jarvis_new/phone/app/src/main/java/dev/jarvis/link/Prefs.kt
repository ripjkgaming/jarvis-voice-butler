package dev.jarvis.link

import android.content.Context
import android.content.SharedPreferences

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

    val configured: Boolean get() = host.isNotEmpty() && token.isNotEmpty()
    val baseUrl: String get() = "http://${host}:${httpPort}"
}
