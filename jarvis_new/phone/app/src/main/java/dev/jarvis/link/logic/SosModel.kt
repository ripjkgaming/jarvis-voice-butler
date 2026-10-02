package dev.jarvis.link.logic

import dev.jarvis.link.net.BridgeConfig
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import java.util.Locale

/** Local alarm effects (flashing screen, vibration, notification). UI/device supplied. */
interface SosEffects {
    fun start()
    fun stop()
}

fun interface LocationSource {
    /** Last known fix as (lat, lon), or null (no permission / no fix). */
    fun lastKnown(): Pair<Double, Double>?
}

data class SosState(
    val active: Boolean = false,
    val location: String = "unknown",
    /** null = not attempted, true/false = PC notification result. */
    val pcNotified: Boolean? = null,
    val notice: String = "",
)

/**
 * SOS: the on-device alarm always fires; in addition the PC is told via the
 * `notify` tool with the last-known location (owner mode and online only).
 */
class SosModel(
    private val config: () -> BridgeConfig,
    private val actions: PcActions,
    private val effects: SosEffects,
    private val location: LocationSource,
    private val speaker: Speaker,
    scope: CoroutineScope,
    io: CoroutineDispatcher,
) : Model<SosState>(SosState(), scope, io) {

    fun refreshLocation() = update { it.copy(location = formatLocation(location.lastKnown())) }

    fun activate() {
        if (current.active) return
        val fix = location.lastKnown()
        effects.start()
        speaker.speak("S O S activated, Sir. Stay where you are.")
        val c = config()
        update {
            it.copy(
                active = true,
                location = formatLocation(fix),
                pcNotified = null,
                notice = "SOS active.",
            )
        }
        if (c.offline || c.guest || !c.configured) {
            val why = when {
                c.offline -> "offline mode"
                c.guest -> "guest mode"
                else -> "link not configured"
            }
            update { it.copy(pcNotified = false, notice = "SOS active on this phone only ($why).") }
            return
        }
        launch {
            val body = message(fix)
            when (val r = io { actions.notifyPc("SOS from phone", body) }) {
                is Outcome.Ok -> update { it.copy(pcNotified = true, notice = "SOS active. PC notified.") }
                is Outcome.Fail -> update {
                    it.copy(pcNotified = false, notice = "SOS active. PC not notified: ${r.message}")
                }
            }
        }
    }

    fun standDown() {
        if (!current.active) return
        effects.stop()
        update { it.copy(active = false, notice = "Stood down.") }
    }

    companion object {
        fun formatLocation(fix: Pair<Double, Double>?): String =
            if (fix == null) "unknown" else String.format(Locale.US, "%.5f, %.5f", fix.first, fix.second)

        /** Notify body (<= 300 chars, the bridge limit). */
        fun message(fix: Pair<Double, Double>?): String =
            if (fix == null) "SOS from the phone. Location unknown."
            else String.format(
                Locale.US, "SOS from the phone. Location %.5f, %.5f https://maps.google.com/?q=%.5f,%.5f",
                fix.first, fix.second, fix.first, fix.second,
            )
    }
}
