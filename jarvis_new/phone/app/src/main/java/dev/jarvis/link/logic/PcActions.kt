package dev.jarvis.link.logic

import dev.jarvis.link.net.Bridge
import dev.jarvis.link.net.BridgeError
import dev.jarvis.link.net.BridgeException
import dev.jarvis.link.net.BridgeConfig
import dev.jarvis.link.net.GuestPolicy
import dev.jarvis.link.net.Keys
import dev.jarvis.link.net.Limits
import dev.jarvis.link.net.Tool
import dev.jarvis.link.net.ToolAction
import dev.jarvis.link.net.ToolResult
import org.json.JSONObject

/**
 * Every PC-control operation the UI can trigger, with request shaping,
 * guest/offline gating and failure messages in one place. Blocking: models
 * call these from their IO dispatcher.
 */
class PcActions(
    private val bridge: Bridge,
    private val config: () -> BridgeConfig,
    /** Receives successful `remote_start` actions so the app can open the RDP client. */
    private val remoteSink: (host: String?, port: Int?) -> Unit = { _, _ -> },
) {
    /** Run a tool; a bridge-reported failure becomes [Outcome.Fail]. */
    fun tool(name: String, args: JSONObject = JSONObject()): Outcome<ToolResult> {
        val c = config()
        if (c.guest && !GuestPolicy.allows(name)) {
            return Outcome.Fail("Guest mode refuses '$name'.")
        }
        return when (val r = attempt { bridge.tool(name, args) }) {
            is Outcome.Fail -> r
            is Outcome.Ok -> if (r.value.ok) r
            else Outcome.Fail(r.value.error ?: "'$name' failed on the PC.")
        }
    }

    fun lock() = tool(Tool.LOCK)

    /** Unlock only ever runs after a fingerprint confirmation and never as a guest. */
    fun unlock(biometricConfirmed: Boolean): Outcome<ToolResult> {
        if (config().guest) return Outcome.Fail("Unlock denied: guest mode keeps the PC locked.")
        if (!biometricConfirmed) return Outcome.Fail("Unlock denied: fingerprint required.")
        return tool(Tool.UNLOCK)
    }

    fun blackout(output: String? = null) = tool(Tool.SCREEN_OFF, Tool.output(output))
    fun wake(output: String? = null) = tool(Tool.SCREEN_ON, Tool.output(output))
    fun restoreScreens() = tool(Tool.SCREENS_RESTORE)
    fun screensState() = tool(Tool.SCREENS_STATE)
    fun screenshot(output: String?) = tool(Tool.SCREENSHOT, Tool.screenshot(output))
    fun volumeGet() = tool(Tool.VOLUME_GET)
    fun volumeSet(level: Int) = tool(Tool.VOLUME_SET, Tool.volumeSet(level))
    fun volumeStep(up: Boolean) = tool(if (up) Tool.VOLUME_UP else Tool.VOLUME_DOWN)
    fun mute(muted: Boolean) = tool(if (muted) Tool.VOLUME_MUTE else Tool.VOLUME_UNMUTE)
    fun media(tool: String) = tool(tool)

    fun openApp(name: String): Outcome<ToolResult> {
        val n = name.trim()
        if (n.isEmpty()) return Outcome.Fail("Name an app first.")
        return tool(Tool.OPEN_APP, Tool.openApp(n))
    }

    fun typeText(text: String): Outcome<Int> {
        if (config().guest) return Outcome.Fail("Guest mode refuses typing.")
        if (text.isEmpty()) return Outcome.Fail("Type something first.")
        return attempt { bridge.typeText(text); Limits.chunk(text).size }
    }

    fun pressKey(key: String): Outcome<Unit> {
        if (config().guest) return Outcome.Fail("Guest mode refuses typing.")
        return attempt { bridge.pressKey(key) }
    }

    fun pressEnter() = pressKey(Keys.ENTER)

    /** Mic mute on the PC wake listener (`/mic`): toggles and returns the new state. */
    fun togglePcMic(): Outcome<Boolean> = attempt {
        val muted = bridge.micMuted() ?: throw BridgeException(
            BridgeError.Server(503, "The PC wake listener is unavailable.")
        )
        val ack = bridge.setMicMuted(!muted)
        if (!ack.ok) throw BridgeException(
            BridgeError.Server(503, ack.error ?: "The PC wake listener is unavailable.")
        )
        !muted
    }

    /** Fire the notify tool on the PC (used by SOS). */
    fun notifyPc(title: String, body: String) = tool(Tool.NOTIFY, Tool.notify(title, body))

    /** `remote_start` then hand host/port to the RDP launcher. */
    fun remoteStart(): Outcome<ToolResult> {
        val r = tool(Tool.REMOTE_START)
        if (r is Outcome.Ok) {
            val port = if (r.value.json.has("port")) r.value.json.optInt("port") else null
            remoteSink(r.value.host, port)
        }
        return r
    }

    /** Apply a routed/chat/talk `action`: open the RDP client on remote_start. */
    fun handleAction(a: ToolAction?) {
        if (a == null || a.tool != Tool.REMOTE_START || !a.ok) return
        val raw = a.raw
        val host = raw?.optString("host")?.ifEmpty { null }
        val port = if (raw?.has("port") == true) raw.optInt("port") else null
        remoteSink(host, port)
    }

    /** Health + status summary for connection tests. */
    fun ping(): Outcome<String> = attempt {
        val h = bridge.health()
        val s = try { bridge.status() } catch (e: BridgeException) { null }
        buildString {
            append("Bridge ").append(h.optString("version", "?"))
            if (h.has("uptime_s")) append(", up ").append(formatUptime(h.optLong("uptime_s")))
            s?.optString("agent_name")?.takeIf { it.isNotEmpty() }?.let { append(", agent ").append(it) }
            if (s != null && !s.optBoolean("livekit_configured", true)) append(" (LiveKit not configured on PC)")
        }
    }

    companion object {
        fun formatUptime(sec: Long): String = when {
            sec < 90 -> "${sec}s"
            sec < 5400 -> "${sec / 60}m"
            sec < 129600 -> "${sec / 3600}h"
            else -> "${sec / 86400}d"
        }
    }
}
