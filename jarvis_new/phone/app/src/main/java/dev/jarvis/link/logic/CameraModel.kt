package dev.jarvis.link.logic

import dev.jarvis.link.net.Bridge
import dev.jarvis.link.net.Limits
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope

data class CameraState(
    val busy: Boolean = false,
    val latest: ByteArray? = null,
    val latestSeq: Int = 0,
    val notice: String = "",
)

/** Camera tab: send a frame to the PC and view the latest one back. */
class CameraModel(
    private val bridge: Bridge,
    scope: CoroutineScope,
    io: CoroutineDispatcher,
    /** Re-encode a too-big JPEG (device layer); identity by default. */
    private val shrink: (ByteArray) -> ByteArray = { it },
) : Model<CameraState>(CameraState(), scope, io) {

    fun note(msg: String) = update { it.copy(notice = msg) }

    fun upload(jpeg: ByteArray) {
        if (jpeg.isEmpty()) { update { it.copy(notice = "The camera returned an empty photo.") }; return }
        update { it.copy(busy = true, notice = "Sending...") }
        launch {
            val r = io {
                attempt {
                    val data = if (jpeg.size > Limits.CAMERA_MAX_BYTES) shrink(jpeg) else jpeg
                    bridge.cameraFrame(data)
                }
            }
            update {
                it.copy(
                    busy = false,
                    notice = when (r) {
                        is Outcome.Ok -> "Sent to Jarvis"
                        is Outcome.Fail -> r.message
                    },
                )
            }
        }
    }

    fun loadLatest() {
        update { it.copy(busy = true, notice = "Loading...") }
        launch {
            when (val r = io { attempt { bridge.cameraLatest() } }) {
                is Outcome.Ok -> update {
                    val b = r.value
                    if (b == null || b.isEmpty()) it.copy(busy = false, notice = "No frames on the PC yet.")
                    else it.copy(busy = false, latest = b, latestSeq = it.latestSeq + 1, notice = "Latest frame")
                }
                is Outcome.Fail -> update { it.copy(busy = false, notice = r.message) }
            }
        }
    }
}
