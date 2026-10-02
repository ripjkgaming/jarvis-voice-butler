package dev.jarvis.link.logic

import dev.jarvis.link.net.BridgeException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Success value or a message fit to show a person. */
sealed class Outcome<out T> {
    data class Ok<T>(val value: T) : Outcome<T>()
    /** [fatal]: trying a different endpoint on the same bridge cannot help (down, unauthorized, offline, guest). */
    data class Fail(
        val message: String,
        val transient: Boolean = false,
        val fatal: Boolean = false,
        /** Optional machine-readable reason, e.g. "no_match". */
        val code: String? = null,
    ) : Outcome<Nothing>()

    val okOrNull: T? get() = (this as? Ok)?.value
    val failure: String? get() = (this as? Fail)?.message
}

/** Run blocking [block]; map every failure to [Outcome.Fail]. */
inline fun <T> attempt(block: () -> T): Outcome<T> = try {
    Outcome.Ok(block())
} catch (e: BridgeException) {
    Outcome.Fail(e.error.message, e.error.transient, e.error.fatal)
} catch (e: CancellationException) {
    throw e
} catch (e: Exception) {
    Outcome.Fail("Unexpected error: ${e.message ?: e.javaClass.simpleName}")
}

/** Speaks a reply aloud (on-device TTS). */
fun interface Speaker {
    fun speak(text: String)
}

/** Plays 16-bit mono PCM replies from /talk. */
fun interface AudioPlayer {
    fun play(audioB64: String, rate: Int)
}

/** Mic capture for push-to-talk and the assistant. PCM is 16 kHz mono 16-bit LE. */
interface AudioCapture {
    /** Begin recording. False when the mic is unavailable or permission is missing. */
    fun start(): Boolean

    /** Stop and return everything captured (may be empty). */
    fun stop(): ByteArray
}

/**
 * Base for screen state holders. State is one immutable data class the UI
 * renders; blocking bridge work hops to [io]; results land in [state].
 */
abstract class Model<S>(
    initial: S,
    protected val scope: CoroutineScope,
    protected val io: CoroutineDispatcher,
) {
    private val _state = MutableStateFlow(initial)
    val state: StateFlow<S> = _state.asStateFlow()

    protected val current: S get() = _state.value

    protected fun update(f: (S) -> S) = _state.update(f)

    protected fun launch(block: suspend CoroutineScope.() -> Unit): Job = scope.launch(block = block)

    protected suspend fun <T> io(block: () -> T): T = withContext(io) { block() }
}
