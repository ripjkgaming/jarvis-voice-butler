package dev.jarvis.link.net

/** Every way a bridge call can fail, each with a message fit to show a person. */
sealed class BridgeError(open val message: String) {
    data class NotConfigured(val problem: BridgeConfig.Problem) : BridgeError(problem.message)

    object Offline : BridgeError("Offline mode is on, so bridge calls are held. Turn it off in Settings.")

    data class Guest(val what: String) :
        BridgeError("Guest mode refuses $what.")

    object Unauthorized : BridgeError(
        "The PC bridge rejected the token (HTTP 401). Re-enter the bridge token in Settings; " +
            "it must match JARVIS_BRIDGE_TOKEN on the PC."
    )

    data class Forbidden(val detail: String) : BridgeError(detail.ifBlank { "The bridge refused this request (HTTP 403)." })

    data class BadRequest(val detail: String) : BridgeError(detail.ifBlank { "The bridge rejected the request (HTTP 400)." })

    data class NotFound(val detail: String) : BridgeError(detail.ifBlank { "The bridge has no such endpoint (HTTP 404)." })

    data class Server(val code: Int, val detail: String) :
        BridgeError(detail.ifBlank { "The bridge failed (HTTP $code)." })

    data class Unreachable(val endpoint: String) : BridgeError(
        "Cannot reach the PC at $endpoint. Check that Tailscale is connected on both devices, " +
            "the bridge is running, and JARVIS_BRIDGE_BIND allows the tailnet address."
    )

    data class Timeout(val endpoint: String) : BridgeError(
        "The PC at $endpoint did not answer in time. It may be asleep or busy; try again."
    )

    data class Network(val detail: String) : BridgeError("Network error: $detail")

    data class Malformed(val endpoint: String) : BridgeError(
        "The reply from $endpoint was not a Jarvis bridge reply. Check the port number."
    )

    /** True when no other endpoint on this bridge can succeed either. */
    val fatal: Boolean
        get() = this is NotConfigured || this is Offline || this is Unauthorized ||
            this is Guest || this is Unreachable || this is Timeout || this is Network

    /** True when retrying later could plausibly succeed. */
    val transient: Boolean
        get() = this is Unreachable || this is Timeout || this is Network ||
            (this is Server && code in 502..504)
}

class BridgeException(val error: BridgeError, cause: Throwable? = null) :
    Exception(error.message, cause)
