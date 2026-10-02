package dev.jarvis.link.logic

import dev.jarvis.link.net.BridgeConfig
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope

/** Starts/stops the foreground services behind the toggles. Returns an error message or null. */
interface ServiceControl {
    fun applyMicUplink(on: Boolean): String?
    fun applyHotword(on: Boolean): String?
    /** Re-read TTS voice prefs. */
    fun refreshTts()
}

data class SettingsState(
    val host: String = "",
    val httpPort: String = "",
    val micPort: String = "",
    val token: String = "",
    /** Field name -> message: host, port, micPort, token. */
    val errors: Map<String, String> = emptyMap(),
    val micUplink: Boolean = false,
    val hotword: Boolean = false,
    val pauseOnMusic: Boolean = true,
    val tts: Boolean = true,
    val ttsMale: Boolean = true,
    val autostart: Boolean = true,
    val offline: Boolean = false,
    val guest: Boolean = false,
    val remoteClient: String = "",
    val session: String = "",
    val testing: Boolean = false,
    val notice: String = "",
)

/** Settings tab: connection fields with validation, toggles, link test. */
class SettingsModel(
    private val prefs: Prefs,
    private val actions: PcActions,
    private val services: ServiceControl,
    scope: CoroutineScope,
    io: CoroutineDispatcher,
) : Model<SettingsState>(SettingsState(), scope, io) {

    init { load() }

    fun load() = update {
        SettingsState(
            host = prefs.host,
            httpPort = prefs.httpPort.toString(),
            micPort = prefs.micPort.toString(),
            token = prefs.token,
            micUplink = prefs.micUplink,
            hotword = prefs.hotwordEnabled,
            pauseOnMusic = prefs.hotwordPauseOnMusic,
            tts = prefs.ttsEnabled,
            ttsMale = prefs.ttsMale,
            autostart = prefs.autostart,
            offline = prefs.offlineMode,
            guest = prefs.guestMode,
            remoteClient = prefs.remoteClient,
            session = "Session ${prefs.sessionId.take(8)}",
            notice = it.notice,
        )
    }

    /** Validate and persist the four connection fields. True when saved. */
    fun save(hostRaw: String, portRaw: String, micPortRaw: String, tokenRaw: String): Boolean {
        val errors = LinkedHashMap<String, String>()
        val hostIn = BridgeConfig.normalizeHost(hostRaw)
        val host = hostIn.host
        if (host.isEmpty()) errors["host"] = BridgeConfig.Problem.NO_HOST.message
        else if (!BridgeConfig.isValidHost(host)) errors["host"] = BridgeConfig.Problem.BAD_HOST.message

        val portText = portRaw.trim()
        val port = when {
            portText.isEmpty() -> hostIn.port ?: BridgeConfig.DEFAULT_PORT
            else -> portText.toIntOrNull()
        }
        if (port == null || port !in 1..65535) errors["port"] = BridgeConfig.Problem.BAD_PORT.message

        val micText = micPortRaw.trim()
        val micPort = if (micText.isEmpty()) BridgeConfig.DEFAULT_MIC_PORT else micText.toIntOrNull()
        if (micPort == null || micPort !in 1..65535) errors["micPort"] = "The mic port must be a number from 1 to 65535 (default 4318)."

        val token = BridgeConfig.normalizeToken(tokenRaw)
        if (token.isEmpty()) errors["token"] = BridgeConfig.Problem.NO_TOKEN.message
        else if (!BridgeConfig.isValidToken(token)) errors["token"] = "The token contains spaces or non-ASCII characters; paste it exactly as on the PC."

        if (errors.isNotEmpty()) {
            update { it.copy(errors = errors, notice = "Not saved: fix the highlighted fields.") }
            return false
        }
        prefs.host = host
        prefs.httpPort = port!!
        prefs.micPort = micPort!!
        prefs.token = token
        update {
            it.copy(
                host = host, httpPort = port.toString(), micPort = micPort.toString(), token = token,
                errors = emptyMap(), notice = "Saved.",
            )
        }
        return true
    }

    fun saveAndTest(hostRaw: String, portRaw: String, micPortRaw: String, tokenRaw: String) {
        if (save(hostRaw, portRaw, micPortRaw, tokenRaw)) testConnection()
    }

    fun testConnection() {
        update { it.copy(testing = true, notice = "Testing...") }
        launch {
            val msg = when (val r = io { actions.ping() }) {
                is Outcome.Ok -> "OK: ${r.value}"
                is Outcome.Fail -> "Failed: ${r.message}"
            }
            update { it.copy(testing = false, notice = msg) }
        }
    }

    fun setMicUplink(on: Boolean) {
        val err = services.applyMicUplink(on)
        prefs.micUplink = on && err == null
        update { it.copy(micUplink = prefs.micUplink, notice = err ?: it.notice) }
    }

    fun setHotword(on: Boolean) {
        val err = services.applyHotword(on)
        prefs.hotwordEnabled = on && err == null
        update { it.copy(hotword = prefs.hotwordEnabled, notice = err ?: it.notice) }
    }

    fun setPauseOnMusic(on: Boolean) { prefs.hotwordPauseOnMusic = on; update { it.copy(pauseOnMusic = on) } }
    fun setTts(on: Boolean) { prefs.ttsEnabled = on; update { it.copy(tts = on) } }
    fun setTtsMale(on: Boolean) { prefs.ttsMale = on; services.refreshTts(); update { it.copy(ttsMale = on) } }
    fun setAutostart(on: Boolean) { prefs.autostart = on; update { it.copy(autostart = on) } }

    fun setOffline(on: Boolean) {
        prefs.offlineMode = on
        update { it.copy(offline = on, notice = if (on) "Offline: bridge calls are held." else "Online.") }
    }

    /** Guest mode locks the PC when switched on; switching off leaves it locked. */
    fun setGuest(on: Boolean) {
        prefs.guestMode = on
        update { it.copy(guest = on, notice = if (on) "Guest mode on: cold and limited." else "Guest mode off.") }
        if (on) {
            launch {
                val r = io { actions.lock() }
                update {
                    it.copy(notice = if (r is Outcome.Fail) "Guest mode on, but the PC could not be locked: ${r.message}"
                    else "Guest mode on: PC locked.")
                }
            }
        }
    }

    fun setRemoteClient(pkg: String) { prefs.remoteClient = pkg; update { it.copy(remoteClient = pkg) } }

    fun startRemote() {
        update { it.copy(notice = "Starting remote session...") }
        launch {
            val msg = when (val r = io { actions.remoteStart() }) {
                is Outcome.Ok -> "Remote ready on ${r.value.host ?: "the PC"}"
                is Outcome.Fail -> "Remote failed: ${r.message}"
            }
            update { it.copy(notice = msg) }
        }
    }
}
