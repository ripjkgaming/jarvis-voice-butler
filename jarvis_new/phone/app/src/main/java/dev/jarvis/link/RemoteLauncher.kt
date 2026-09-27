package dev.jarvis.link

import android.app.Activity
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.ActivityNotFoundException
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import androidx.core.app.NotificationCompat
import org.json.JSONObject

/**
 * Opens the user's RDP client app when a bridge reply carries a
 * `{"tool":"remote_start","ok":true,"host":...,"port":...}` action.
 *
 * Pure helpers ([rdpUri], [isRemoteStart], [parseRemoteStart]) are plain
 * JVM-testable Kotlin; everything touching [Context] lives in
 * [pickClient]/[open]/[handleAction] and fails soft (never throws).
 */
object RemoteLauncher {
    const val DEFAULT_PORT = 3389
    const val RUSTDESK_PKG = "com.carriez.flutter_hbb"
    const val PLAY_STORE_PKG = RUSTDESK_PKG
    private const val NOTIF_REMOTE = 3
    private const val REQ_REMOTE = 41

    /** Ordered (label, package) fallbacks shown/used in preference order. */
    val KNOWN_CLIENTS: List<Pair<String, String>> = listOf(
        "RustDesk" to RUSTDESK_PKG,
        "aRDP" to "com.iiordanov.freeaRDP",
        "Windows App" to "com.microsoft.rdc.androidx",
        "aFreeRDP" to "com.freerdp.afreerdp",
        "Moonlight" to "com.limelight",
    )

    /** Clients that accept an `rdp://` VIEW intent (others: launch intent). */
    private val RDP_URI_PKGS = setOf(
        "com.microsoft.rdc.androidx",
        "com.freerdp.afreerdp",
    )

    /** Pure: `rdp://` URI for Microsoft-style clients. Host may be blank. */
    fun rdpUri(host: String?, port: Int?): String {
        val h = host?.trim().orEmpty()
        val p = port ?: DEFAULT_PORT
        return "rdp://full%20address=s:$h:$p"
    }

    /** Pure: RustDesk deep link; direct IP access takes the tailnet IP as the ID. */
    fun rustdeskUri(host: String?): String = "rustdesk://${host?.trim().orEmpty()}"

    /** Pure: is this a successful remote_start action? */
    fun isRemoteStart(action: JSONObject?): Boolean =
        action != null &&
            action.optString("tool") == "remote_start" &&
            action.optBoolean("ok", false)

    /** Pure: host/port of a remote_start action, or null when not one.
     *  Accepts top-level or nested `args` fields; port defaults to 3389. */
    fun parseRemoteStart(action: JSONObject?): Pair<String?, Int?>? {
        if (!isRemoteStart(action)) return null
        action!!
        val args = action.optJSONObject("args")
        val host = action.optString("host", "")
            .ifEmpty { args?.optString("host", "").orEmpty() }
            .ifEmpty { null }
        val port = when {
            action.has("port") -> action.optInt("port", DEFAULT_PORT)
            args != null && args.has("port") -> args.optInt("port", DEFAULT_PORT)
            else -> DEFAULT_PORT
        }
        return host to port
    }

    /** Pure: display label for a client package. */
    fun labelFor(pkg: String): String =
        KNOWN_CLIENTS.firstOrNull { it.second == pkg }?.first ?: pkg

    /** Preferred package from prefs when installed, else first installed
     *  known client, else null. Needs package visibility (see manifest). */
    fun pickClient(ctx: Context?): String? {
        if (ctx == null) return null
        return try {
            val pm = ctx.packageManager
            val preferred = Prefs(ctx).remoteClient.trim()
            if (preferred.isNotEmpty() && isInstalled(pm, preferred)) return preferred
            KNOWN_CLIENTS.firstOrNull { isInstalled(pm, it.second) }?.second
        } catch (_: Exception) {
            null
        }
    }

    /**
     * Open the RDP client for host:port. Copies the host to the clipboard
     * (label "Jarvis remote host") so the user can paste it. Foreground
     * Activities launch directly (VIEW rdp:// first, launch-intent
     * fallback); background contexts post a high-priority notification
     * whose tap opens the client (Android 10+ background-start limit).
     * No client installed: opens the Play Store page, returns false.
     */
    fun open(ctx: Context?, host: String?, port: Int?): Boolean {
        if (ctx == null) return false
        return try {
            openUnsafe(ctx, host, port)
        } catch (_: Exception) {
            false
        }
    }

    /** Consume a bridge-reply `action`: launch on remote_start, else false. */
    fun handleAction(ctx: Context?, action: JSONObject?): Boolean {
        val (host, port) = try {
            parseRemoteStart(action) ?: return false
        } catch (_: Exception) {
            return false
        }
        return try {
            open(ctx, host, port)
            true
        } catch (_: Exception) {
            true
        }
    }

    // ── Internals (Context-touching) ─────────────────────────────

    private fun openUnsafe(ctx: Context, host: String?, port: Int?): Boolean {
        val h = host?.trim().orEmpty()
        val p = port ?: DEFAULT_PORT
        if (h.isNotEmpty()) copyHost(ctx, h)
        val pkg = pickClient(ctx) ?: return openPlayStore(ctx)
        val pm = ctx.packageManager
        val primary = buildLaunchIntent(ctx, pkg, h, p)
            ?: pm.getLaunchIntentForPackage(pkg)?.apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            } ?: return openPlayStore(ctx)
        if (ctx is Activity) {
            return try {
                ctx.startActivity(primary)
                true
            } catch (_: ActivityNotFoundException) {
                val fall = pm.getLaunchIntentForPackage(pkg)?.apply {
                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                } ?: return false
                try {
                    ctx.startActivity(fall)
                    true
                } catch (_: Exception) {
                    false
                }
            } catch (_: Exception) {
                false
            }
        }
        // Background (Service etc.): tap-to-open notification instead.
        notifyToLaunch(ctx, pkg, h, primary)
        return true
    }

    private fun buildLaunchIntent(ctx: Context, pkg: String, host: String, port: Int): Intent? {
        if (pkg == RUSTDESK_PKG && host.isNotEmpty()) {
            val view = Intent(Intent.ACTION_VIEW, Uri.parse(rustdeskUri(host))).apply {
                setPackage(pkg)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            if (view.resolveActivity(ctx.packageManager) != null) return view
        }
        if (pkg !in RDP_URI_PKGS) {
            return ctx.packageManager.getLaunchIntentForPackage(pkg)?.apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
        }
        return try {
            Intent(Intent.ACTION_VIEW, Uri.parse(rdpUri(host, port))).apply {
                setPackage(pkg)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
        } catch (_: Exception) {
            ctx.packageManager.getLaunchIntentForPackage(pkg)?.apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
        }
    }

    private fun copyHost(ctx: Context, host: String) {
        try {
            val cm = ctx.getSystemService(ClipboardManager::class.java) ?: return
            cm.setPrimaryClip(ClipData.newPlainText("Jarvis remote host", host))
        } catch (_: Exception) { }
    }

    private fun openPlayStore(ctx: Context): Boolean {
        return try {
            ctx.startActivity(
                Intent(Intent.ACTION_VIEW, Uri.parse("market://details?id=$PLAY_STORE_PKG"))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            )
            false
        } catch (_: ActivityNotFoundException) {
            try {
                ctx.startActivity(
                    Intent(
                        Intent.ACTION_VIEW,
                        Uri.parse("https://play.google.com/store/apps/details?id=$PLAY_STORE_PKG"),
                    ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                )
                false
            } catch (_: Exception) {
                false
            }
        } catch (_: Exception) {
            false
        }
    }

    /** Tap-to-open fallback reusing the LinkService wake channel. */
    private fun notifyToLaunch(ctx: Context, pkg: String, host: String, intent: Intent) {
        try {
            val nm = ctx.getSystemService(NotificationManager::class.java) ?: return
            try {
                nm.createNotificationChannel(
                    NotificationChannel(
                        LinkService.CH_WAKE, "Jarvis wake", NotificationManager.IMPORTANCE_HIGH
                    )
                )
            } catch (_: Exception) { }
            val pi = PendingIntent.getActivity(
                ctx, REQ_REMOTE, intent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            val notif = NotificationCompat.Builder(ctx, LinkService.CH_WAKE)
                .setSmallIcon(android.R.drawable.ic_btn_speak_now)
                .setContentTitle("Remote desktop ready")
                .setContentText("Tap to open ${labelFor(pkg)} ($host)")
                .setContentIntent(pi)
                .setAutoCancel(true)
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .build()
            try {
                nm.notify(NOTIF_REMOTE, notif)
            } catch (_: Exception) { }
        } catch (_: Exception) { }
    }

    private fun isInstalled(pm: PackageManager, pkg: String): Boolean = try {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            pm.getPackageInfo(pkg, PackageManager.PackageInfoFlags.of(0))
        } else {
            @Suppress("DEPRECATION")
            pm.getPackageInfo(pkg, 0)
        }
        true
    } catch (_: Exception) {
        false
    }
}
