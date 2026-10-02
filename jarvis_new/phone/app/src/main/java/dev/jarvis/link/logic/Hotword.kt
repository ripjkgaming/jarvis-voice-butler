package dev.jarvis.link.logic

/**
 * On-device "Jarvis" hotword matching, restart policy and the decision
 * machine behind [dev.jarvis.link.svc.HotwordService]. Pure JVM: the
 * service owns the SpeechRecognizer and just executes the decisions.
 */
object Hotword {
    const val WORD = "jarvis"

    // SpeechRecognizer.ERROR_* values, as ints so JVM tests stay android-free.
    const val ERROR_NETWORK_TIMEOUT = 1
    const val ERROR_NETWORK = 2
    const val ERROR_AUDIO = 3
    const val ERROR_SERVER = 4
    const val ERROR_CLIENT = 5
    const val ERROR_SPEECH_TIMEOUT = 6
    const val ERROR_NO_MATCH = 7
    const val ERROR_RECOGNIZER_BUSY = 8
    const val ERROR_INSUFFICIENT_PERMISSIONS = 9
    const val ERROR_TOO_MANY_REQUESTS = 10
    const val ERROR_SERVER_DISCONNECTED = 11
    const val ERROR_LANGUAGE_NOT_SUPPORTED = 12
    const val ERROR_LANGUAGE_UNAVAILABLE = 13

    const val COOLDOWN_AFTER_WAKE_MS = 5000L
    const val GATE_RECHECK_MS = 2000L
    const val MIN_RESTART_MS = 500L

    /** True when [text] contains "jarvis" as a whole word (partial or final). */
    fun isWakeWord(text: String): Boolean =
        text.lowercase().split(Regex("[^a-z]+")).any { it == WORD }

    /**
     * Restart delay after [failures] consecutive error backoffs:
     * 1s, 2s, 4s, 8s, 16s, capped at 30s. Zero when nothing failed.
     */
    fun backoffMs(failures: Int): Long {
        if (failures <= 0) return 0
        var ms = 1000L
        repeat(minOf(failures - 1, 5)) { ms *= 2 }
        return minOf(ms, 30_000L)
    }

    /** Errors that deserve backoff (busy/server/network/audio) instead of an instant restart. */
    fun needsBackoff(error: Int): Boolean = error in setOf(
        ERROR_NETWORK_TIMEOUT, ERROR_NETWORK, ERROR_AUDIO, ERROR_SERVER, ERROR_RECOGNIZER_BUSY,
        ERROR_TOO_MANY_REQUESTS, ERROR_SERVER_DISCONNECTED, ERROR_LANGUAGE_NOT_SUPPORTED,
        ERROR_LANGUAGE_UNAVAILABLE,
    )

    /** Errors where restarting can never help until the user acts. */
    fun isFatal(error: Int): Boolean = error == ERROR_INSUFFICIENT_PERMISSIONS
}

/** What the service should do next. */
sealed class HotwordStep {
    /** Create a recognizer and start listening now. */
    object Listen : HotwordStep()
    /** Check again in [ms] (gate closed or cooling down). */
    data class Wait(val ms: Long) : HotwordStep()
    /** Wake word heard: route the wake, then re-arm after [cooldownMs]. */
    data class Wake(val cooldownMs: Long) : HotwordStep()
    /** Restart the recognizer after [ms]. */
    data class Restart(val ms: Long) : HotwordStep()
    /** Stop the service for good ([reason] is user-facing). */
    data class Stop(val reason: String) : HotwordStep()
}

/** Hotword restart/backoff/cooldown state machine. */
class HotwordController(private val clock: () -> Long = System::currentTimeMillis) {
    private var failures = 0
    private var coolUntil = 0L

    /** Before each listen: is the hotword allowed to hold the mic right now? */
    fun arm(hotwordOn: Boolean, pauseOnMusic: Boolean, musicActive: Boolean): HotwordStep {
        if (!hotwordOn) return HotwordStep.Stop("Hotword is turned off.")
        if (clock() < coolUntil || !HotwordGate.allowed(true, pauseOnMusic, musicActive)) {
            return HotwordStep.Wait(Hotword.GATE_RECHECK_MS)
        }
        return HotwordStep.Listen
    }

    /** Partial or final recognizer results. Null means "no wake word, keep going" for partials. */
    fun heard(texts: List<String>, final: Boolean): HotwordStep? {
        if (texts.any { Hotword.isWakeWord(it) }) {
            failures = 0
            coolUntil = clock() + Hotword.COOLDOWN_AFTER_WAKE_MS
            return HotwordStep.Wake(Hotword.COOLDOWN_AFTER_WAKE_MS)
        }
        return if (final) HotwordStep.Restart(0) else null
    }

    fun error(code: Int): HotwordStep {
        if (Hotword.isFatal(code)) {
            return HotwordStep.Stop("Microphone permission is missing; open JarvisLink and grant it.")
        }
        if (Hotword.needsBackoff(code)) failures++ else failures = 0
        return HotwordStep.Restart(Hotword.backoffMs(failures).coerceAtLeast(Hotword.MIN_RESTART_MS))
    }

    /** Recognizer could not be created or started. */
    fun startFailed(): HotwordStep {
        failures++
        return HotwordStep.Restart(Hotword.backoffMs(failures).coerceAtLeast(2000L))
    }

    val consecutiveFailures: Int get() = failures
}

/**
 * Mic arbitration: PTT and assistant sessions hold the mic, so the
 * hotword loop must stand down while either is active.
 */
object HotwordGate {
    @Volatile var pttHeld: Boolean = false
    @Volatile var sessionActive: Boolean = false

    fun allowed(hotwordOn: Boolean, pauseOnMusic: Boolean, musicActive: Boolean): Boolean {
        if (!hotwordOn || pttHeld || sessionActive) return false
        if (pauseOnMusic && musicActive) return false
        return true
    }

    fun reset() { pttHeld = false; sessionActive = false }
}
