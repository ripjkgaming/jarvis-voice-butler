package dev.jarvis.link.logic

import dev.jarvis.link.net.BridgeConfig
import dev.jarvis.link.net.ScreenOutput
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import java.util.Base64

data class ScreensState(
    val outputs: List<ScreenOutput> = emptyList(),
    /** "all" or an output name from [outputs]. */
    val selected: String = ALL,
    /** Latest screenshot PNG bytes, if any. */
    val image: ByteArray? = null,
    /** Bumps with every new image so the UI redraws even for equal bytes. */
    val imageSeq: Int = 0,
    val guest: Boolean = false,
    val busy: Boolean = false,
    val notice: String = "",
) {
    companion object { const val ALL = "all" }
}

/** Screens tab: per-display screenshots, state, blackout/restore. */
class ScreensModel(
    private val config: () -> BridgeConfig,
    private val actions: PcActions,
    scope: CoroutineScope,
    io: CoroutineDispatcher,
) : Model<ScreensState>(ScreensState(), scope, io) {

    fun loadOutputs() {
        update { it.copy(guest = config().guest, busy = true, notice = "Reading displays...") }
        launch {
            when (val r = io { actions.screensState() }) {
                is Outcome.Ok -> {
                    val outs = r.value.outputs
                    update {
                        it.copy(
                            outputs = outs,
                            // a display that vanished falls back to All
                            selected = if (it.selected == ScreensState.ALL || outs.any { o -> o.name == it.selected }) it.selected else ScreensState.ALL,
                            busy = false,
                            notice = "${outs.size} display(s)",
                        )
                    }
                }
                is Outcome.Fail -> update { it.copy(busy = false, notice = r.message) }
            }
        }
    }

    fun select(name: String) = update { it.copy(selected = name, notice = "Screen: $name") }

    fun capture() {
        val out = current.selected
        update { it.copy(busy = true, notice = "Capturing $out...") }
        launch {
            when (val r = io { actions.screenshot(out) }) {
                is Outcome.Ok -> {
                    val bytes = decodeImage(r.value.imageB64)
                    if (bytes == null) {
                        update { it.copy(busy = false, notice = "The PC sent unreadable image data.") }
                    } else {
                        update {
                            it.copy(image = bytes, imageSeq = it.imageSeq + 1, busy = false, notice = "Screenshot OK")
                        }
                    }
                }
                is Outcome.Fail -> update { it.copy(busy = false, notice = r.message) }
            }
        }
    }

    fun blackout() = act("Screens off") {
        val sel = current.selected
        actions.blackout(if (sel == ScreensState.ALL) null else sel)
    }

    fun wake() = act("Screens on") {
        val sel = current.selected
        if (sel == ScreensState.ALL) actions.restoreScreens() else actions.wake(sel)
    }

    private fun <T> act(label: String, call: () -> Outcome<T>) {
        update { it.copy(busy = true, notice = "$label...") }
        launch {
            val msg = when (val r = io { call() }) {
                is Outcome.Ok -> "$label: done"
                is Outcome.Fail -> "$label failed: ${r.message}"
            }
            update { it.copy(busy = false, notice = msg) }
            loadOutputs()
        }
    }

    companion object {
        fun decodeImage(b64: String?): ByteArray? {
            if (b64.isNullOrEmpty()) return null
            return try {
                Base64.getMimeDecoder().decode(b64).takeIf { it.isNotEmpty() }
            } catch (_: IllegalArgumentException) {
                null
            }
        }
    }
}
