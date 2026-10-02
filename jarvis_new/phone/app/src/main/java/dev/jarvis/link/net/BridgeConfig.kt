package dev.jarvis.link.net

/**
 * Everything the HTTP client needs to reach the PC bridge. Pure data so
 * every layer (client, models, services) can be tested without Android.
 */
data class BridgeConfig(
    val host: String = "",
    val httpPort: Int = DEFAULT_PORT,
    val token: String = "",
    val guest: Boolean = false,
    val offline: Boolean = false,
) {
    /** Why this config cannot be used, or null when it is complete. */
    enum class Problem(val message: String) {
        NO_HOST("No PC address set. Open Settings and enter the PC's Tailscale IP."),
        BAD_HOST("The PC address is not valid. Use the Tailscale IP (e.g. 100.77.6.93) or a hostname, without http:// or a path."),
        BAD_PORT("The bridge port must be a number from 1 to 65535 (default 4317)."),
        NO_TOKEN("No bridge token set. Open Settings and paste JARVIS_BRIDGE_TOKEN from the PC."),
    }

    fun problem(): Problem? = when {
        host.isBlank() -> Problem.NO_HOST
        !isValidHost(host) -> Problem.BAD_HOST
        httpPort !in 1..65535 -> Problem.BAD_PORT
        token.isBlank() -> Problem.NO_TOKEN
        else -> null
    }

    /** Result of cleaning user input from the host field. */
    data class HostInput(val host: String, val port: Int?)

    val configured: Boolean get() = problem() == null

    /** `host:port` for messages. */
    val endpoint: String get() = "${hostForUrl(host)}:$httpPort"

    val baseUrl: String get() = "http://$endpoint"

    companion object {
        const val DEFAULT_PORT = 4317
        const val DEFAULT_MIC_PORT = 4318

        private val HOST_RE = Regex("^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$")

        fun isValidHost(h: String): Boolean {
            val t = h.trim()
            if (t.isEmpty() || t.length > 253) return false
            if (t.contains(':')) return isValidIpv6(t)
            return HOST_RE.matches(t)
        }

        private fun isValidIpv6(t: String): Boolean =
            t.all { it.isDigit() || it in 'a'..'f' || it in 'A'..'F' || it == ':' || it == '.' } &&
                t.count { it == ':' } in 2..7

        /** IPv6 literals need brackets inside a URL. */
        fun hostForUrl(h: String): String = if (h.contains(':')) "[$h]" else h

        /**
         * Clean what a person pastes into the host field: strips a scheme,
         * path, whitespace and a trailing `:port` (returned separately).
         */
        fun normalizeHost(raw: String): HostInput {
            var s = raw.trim()
            s = s.substringAfter("://", s)
            s = s.substringBefore('/').substringBefore('?').trim()
            if (s.startsWith("[")) {
                val end = s.indexOf(']')
                if (end > 0) {
                    val host = s.substring(1, end)
                    val port = s.substring(end + 1).removePrefix(":").toIntOrNull()
                    return HostInput(host, port)
                }
            }
            if (s.count { it == ':' } == 1) {
                val host = s.substringBefore(':')
                val port = s.substringAfter(':').toIntOrNull()
                if (port != null) return HostInput(host, port)
            }
            return HostInput(s, null)
        }

        /** Clean a pasted token: drops a `Bearer ` prefix and surrounding quotes/space. */
        fun normalizeToken(raw: String): String {
            var s = raw.trim().trim('"', '\'').trim()
            if (s.startsWith("Bearer ", ignoreCase = true)) s = s.substring(7).trim()
            return s
        }

        /** A token is sent in an HTTP header: it must be printable ASCII without spaces. */
        fun isValidToken(t: String): Boolean =
            t.isNotEmpty() && t.all { it.code in 33..126 }
    }
}
