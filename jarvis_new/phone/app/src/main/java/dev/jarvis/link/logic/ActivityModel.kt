package dev.jarvis.link.logic

import dev.jarvis.link.net.Bridge
import dev.jarvis.link.net.Caption
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import org.json.JSONObject

data class ActivityState(
    /** PC action log lines, oldest first (newest last). */
    val actions: List<String> = emptyList(),
    /** Transcript lines, oldest first (newest last). */
    val captions: List<Caption> = emptyList(),
    /** `key: value` lines from `/sys`. */
    val system: List<String> = emptyList(),
    val loading: Boolean = false,
    /** Set when the last refresh failed; lists keep their previous data. */
    val error: String? = null,
    val refreshes: Int = 0,
)

/** Activity tab: actions feed + captions + system stats, polled while visible. */
class ActivityModel(
    private val bridge: Bridge,
    scope: CoroutineScope,
    io: CoroutineDispatcher,
    private val pollMs: Long = POLL_MS,
) : Model<ActivityState>(ActivityState(), scope, io) {

    private var poller: Job? = null

    fun refresh() {
        update { it.copy(loading = true) }
        launch { fetch() }
    }

    private suspend fun fetch() {
        val acts = io { attempt { bridge.actions(100) } }
        val caps = io { attempt { bridge.captions(30) } }
        val sys = io { attempt { bridge.sys() } }
        val err = listOf(acts, caps, sys).filterIsInstance<Outcome.Fail>().firstOrNull()?.message
        update {
            it.copy(
                actions = acts.okOrNull ?: it.actions,
                captions = caps.okOrNull ?: it.captions,
                system = sys.okOrNull?.let(::sysLines) ?: it.system,
                loading = false,
                error = err,
                refreshes = it.refreshes + 1,
            )
        }
    }

    fun startPolling() {
        if (poller?.isActive == true) return
        poller = launch {
            while (true) {
                fetch()
                delay(pollMs)
            }
        }
    }

    fun stopPolling() {
        poller?.cancel()
        poller = null
    }

    companion object {
        const val POLL_MS = 5000L

        /** Flatten /sys into short display lines (scalars and the phone sample). */
        fun sysLines(o: JSONObject): List<String> {
            val out = mutableListOf<String>()
            val keys = o.keys().asSequence().filter { it != "ok" }.sorted().toList()
            for (k in keys) {
                when (val v = o.opt(k)) {
                    null, JSONObject.NULL -> Unit
                    is JSONObject -> {
                        if (k == "phone") {
                            val b = v.optInt("battery", -1)
                            if (b >= 0) {
                                out.add("phone battery: $b%${if (v.optBoolean("charging")) " (charging)" else ""}")
                            }
                        }
                    }
                    is org.json.JSONArray -> Unit
                    else -> out.add("$k: $v")
                }
            }
            return out
        }
    }
}
