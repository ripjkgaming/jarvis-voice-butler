package dev.jarvis.link.logic

/** Optional ringtone errors must never erase a successfully posted incoming notification. */
object CallPresentation {
    data class Result(val posted: Boolean, val audible: Boolean, val code: String, val notificationError: String? = null)

    fun present(post: () -> Unit, ring: () -> Boolean): Result {
        try { post() } catch (e: Exception) { return Result(false,false,"notification_failed",failureCode(e)) }
        return ringPosted(ring)
    }

    /**
     * A separate incoming notification is not associated with the monitor's FGS.
     * Android can reject CallStyle without its own FGS or full-screen intent.
     * Ordinary actionable notifications preserve answering without that access.
     */
    fun presentIncoming(fullScreenAllowed: Boolean, post: (callStyle: Boolean) -> Unit, ring: () -> Boolean): Result {
        var error: String? = null
        try {
            post(fullScreenAllowed)
        } catch (e: Exception) {
            error=failureCode(e)
            if (!fullScreenAllowed) return Result(false,false,"notification_failed",error)
            try { post(false) }
            catch (fallback: Exception) { return Result(false,false,"notification_failed",failureCode(fallback)) }
        }
        return ringPosted(ring).copy(notificationError=error)
    }

    private fun ringPosted(ring: () -> Boolean): Result = try {
        val audible=ring()
        Result(true,audible,if (audible) "ringing" else "posted_silent")
    } catch (_: Exception) { Result(true,false,"ringtone_failed") }

    /** Never include exception messages: OEM errors can contain notification content. */
    private fun failureCode(error: Exception): String = when (error) {
        is SecurityException -> "permission"
        is IllegalArgumentException -> "invalid_notification"
        else -> "posting_error"
    }
}
