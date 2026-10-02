package dev.jarvis.link.logic

import dev.jarvis.link.net.Bridge
import dev.jarvis.link.net.BatterySample
import dev.jarvis.link.net.BridgeConfig
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

data class TelemetryState(
    val running: Boolean = false,
    val lastSample: BatterySample? = null,
    val lastOkAt: Long = 0,
    val lastError: String? = null,
    val pushes: Int = 0,
)

/**
 * Pushes phone battery to `POST /phone/telemetry` on start, every
 * [intervalMs] and whenever charging flips. Reference-counted so the app
 * (foreground) and LinkService can both ask for it without double loops.
 * Failures are recorded, never thrown; pushes are skipped while offline or
 * unconfigured.
 */
class TelemetryReporter(
    private val bridge: Bridge,
    private val config: () -> BridgeConfig,
    private val battery: () -> BatterySample?,
    private val scope: CoroutineScope,
    private val io: CoroutineDispatcher,
    private val clock: () -> Long = System::currentTimeMillis,
    private val intervalMs: Long = INTERVAL_MS,
) {
    private val _state = MutableStateFlow(TelemetryState())
    val state: StateFlow<TelemetryState> = _state.asStateFlow()

    private var users = 0
    private var loop: Job? = null
    private var lastCharging: Boolean? = null

    @Synchronized
    fun acquire() {
        users++
        if (loop?.isActive == true) return
        _state.value = _state.value.copy(running = true)
        loop = scope.launch {
            while (true) {
                push()
                delay(intervalMs)
            }
        }
    }

    @Synchronized
    fun release() {
        if (users > 0) users--
        if (users == 0) {
            loop?.cancel()
            loop = null
            _state.value = _state.value.copy(running = false)
        }
    }

    /** Call from a battery-changed receiver: pushes only when charging flipped. */
    fun onBatteryChanged(sample: BatterySample) {
        val prev = lastCharging
        lastCharging = sample.charging
        if (prev != null && prev != sample.charging && users > 0) scope.launch { push() }
    }

    /** One immediate push (also used by tests). */
    suspend fun push() {
        val c = config()
        if (c.offline || !c.configured) return
        val sample = battery() ?: return
        lastCharging = sample.charging
        val r = withContext(io) { attempt { bridge.postTelemetry(sample) } }
        _state.value = when (r) {
            is Outcome.Ok -> _state.value.copy(
                lastSample = sample, lastOkAt = clock(), lastError = null, pushes = _state.value.pushes + 1,
            )
            is Outcome.Fail -> _state.value.copy(lastSample = sample, lastError = r.message)
        }
    }

    companion object {
        const val INTERVAL_MS = 60_000L
    }
}
