package dev.jarvis.link.logic

import org.json.JSONException
import org.json.JSONObject

/**
 * The phone side of the PC `mic_uplink` socket protocol (port 4318): a JSON
 * handshake line, an `{"ok":true}` ack line, then raw 16 kHz mono PCM16 LE
 * up and `WAKE...` lines down.
 */
object MicUplink {
    const val RATE = 16000

    /** Reconnect delays in ms; the last repeats. */
    val BACKOFF_MS = longArrayOf(2_000, 5_000, 15_000, 30_000, 60_000)

    fun handshake(): ByteArray =
        (JSONObject().put("rate", RATE).put("channels", 1).toString() + "\n").toByteArray()

    fun ackOk(line: String?): Boolean = try {
        line != null && JSONObject(line).optBoolean("ok", false)
    } catch (_: JSONException) {
        false
    }

    fun isWake(line: String): Boolean = line.startsWith("WAKE")

    /** Delay before reconnect attempt number [failures] (1-based). */
    fun backoff(failures: Int): Long = BACKOFF_MS[(failures - 1).coerceIn(0, BACKOFF_MS.size - 1)]

    /** Little-endian PCM16 bytes of [n] samples into [out]; returns bytes written. */
    fun pcmBytes(samples: ShortArray, n: Int, out: ByteArray): Int {
        var o = 0
        for (i in 0 until n) {
            val v = samples[i].toInt()
            out[o++] = (v and 0xFF).toByte()
            out[o++] = ((v shr 8) and 0xFF).toByte()
        }
        return o
    }
}

/** Where a wake (hotword or PC "hey Jarvis") should land. */
object WakeRouting {
    enum class Target { ONE_SHOT, APP_AUTO_TALK }

    /** Locked screens stay closed (one-shot command); unlocked opens the app and talks. */
    fun decide(keyguardLocked: Boolean): Target =
        if (keyguardLocked) Target.ONE_SHOT else Target.APP_AUTO_TALK
}
