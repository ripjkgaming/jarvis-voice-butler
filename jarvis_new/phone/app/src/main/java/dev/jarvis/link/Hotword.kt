package dev.jarvis.link

/**
 * On-device "Jarvis" hotword matching + restart policy. Pure JVM (no
 * android.* imports) so plain unit tests cover it; [HotwordService] owns
 * the SpeechRecognizer loop and calls in here.
 */
object Hotword {
    const val WORD = "jarvis"

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

    /**
     * SpeechRecognizer error codes that deserve backoff (busy / server /
     * network / audio) rather than an immediate restart (no-match /
     * timeout / client). Codes are SpeechRecognizer.ERROR_* ints, passed
     * as Int so JVM tests stay android-free.
     */
    fun needsBackoff(error: Int): Boolean = error == 8 || error == 4 ||
        error == 2 || error == 1 || error == 3
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
}
