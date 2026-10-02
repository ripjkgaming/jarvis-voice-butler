package dev.jarvis.link.logic

import dev.jarvis.link.net.BridgeConfig
import dev.jarvis.link.net.Limits
import dev.jarvis.link.net.Tool
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope

data class ControlState(
    /** Last known PC volume percent (0..150), null until read. */
    val volume: Int? = null,
    val muted: Boolean? = null,
    val nowPlaying: String = "",
    val pcMicMuted: Boolean? = null,
    val guest: Boolean = false,
    val busy: Boolean = false,
    val notice: String = "",
)

/** Control tab: volume, media, open app, remote keyboard, PC mic. */
class ControlModel(
    private val config: () -> BridgeConfig,
    private val actions: PcActions,
    scope: CoroutineScope,
    io: CoroutineDispatcher,
) : Model<ControlState>(ControlState(), scope, io) {

    private fun syncGuest() = update { it.copy(guest = config().guest) }

    private fun applyVolume(r: dev.jarvis.link.net.ToolResult) = update {
        it.copy(volume = r.volume ?: it.volume, muted = r.muted ?: it.muted)
    }

    fun refresh() {
        syncGuest()
        launch {
            when (val r = io { actions.volumeGet() }) {
                is Outcome.Ok -> { applyVolume(r.value); update { it.copy(notice = "") } }
                is Outcome.Fail -> update { it.copy(notice = r.message) }
            }
        }
    }

    /** Set an absolute level (bridge `volume_set` 0..150); the reply is the true read-back. */
    fun setVolume(level: Int) {
        val target = level.coerceIn(0, Limits.VOLUME_MAX)
        update { it.copy(volume = target, notice = "Volume $target%...") }
        launch {
            when (val r = io { actions.volumeSet(target) }) {
                is Outcome.Ok -> {
                    applyVolume(r.value)
                    update { it.copy(notice = "Volume ${it.volume ?: target}%") }
                }
                is Outcome.Fail -> {
                    update { it.copy(notice = r.message) }
                    refresh()
                }
            }
        }
    }

    fun toggleMute() {
        val want = !(current.muted ?: false)
        update { it.copy(muted = want) }
        launch {
            when (val r = io { actions.mute(want) }) {
                is Outcome.Ok -> {
                    when (val v = io { actions.volumeGet() }) {
                        is Outcome.Ok -> applyVolume(v.value)
                        is Outcome.Fail -> Unit
                    }
                    update { it.copy(notice = if (want) "Muted" else "Unmuted") }
                }
                is Outcome.Fail -> update { it.copy(muted = !want, notice = r.message) }
            }
        }
    }

    fun media(tool: String) {
        require(tool in MEDIA_TOOLS) { "not a media tool: $tool" }
        update { it.copy(notice = "...") }
        launch {
            when (val r = io { actions.media(tool) }) {
                is Outcome.Ok -> update {
                    it.copy(nowPlaying = r.value.state ?: it.nowPlaying, notice = "OK")
                }
                is Outcome.Fail -> update { it.copy(notice = r.message) }
            }
        }
    }

    fun openApp(name: String) {
        if (name.isBlank()) { update { it.copy(notice = "Name an app first.") }; return }
        update { it.copy(busy = true, notice = "Opening ${name.trim()}...") }
        launch {
            val msg = when (val r = io { actions.openApp(name) }) {
                is Outcome.Ok -> r.value.say ?: "Opened ${r.value.app ?: name.trim()}"
                is Outcome.Fail -> r.message
            }
            update { it.copy(busy = false, notice = msg) }
        }
    }

    fun typeText(text: String) {
        update { it.copy(busy = true, notice = "Typing...") }
        launch {
            val msg = when (val r = io { actions.typeText(text) }) {
                is Outcome.Ok -> if (r.value > 1) "Typed (${r.value} parts)" else "Typed"
                is Outcome.Fail -> r.message
            }
            update { it.copy(busy = false, notice = msg) }
        }
    }

    fun pressKey(key: String) {
        launch {
            val msg = when (val r = io { actions.pressKey(key) }) {
                is Outcome.Ok -> "Sent $key"
                is Outcome.Fail -> r.message
            }
            update { it.copy(notice = msg) }
        }
    }

    fun togglePcMic() {
        launch {
            when (val r = io { actions.togglePcMic() }) {
                is Outcome.Ok -> update {
                    it.copy(pcMicMuted = r.value, notice = if (r.value) "PC mic muted" else "PC mic live")
                }
                is Outcome.Fail -> update { it.copy(notice = r.message) }
            }
        }
    }

    companion object {
        val MEDIA_TOOLS = setOf(Tool.MEDIA_PLAY_PAUSE, Tool.MEDIA_NEXT, Tool.MEDIA_PREV)
    }
}
