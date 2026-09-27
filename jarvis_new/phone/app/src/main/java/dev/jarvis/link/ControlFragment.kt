package dev.jarvis.link

import android.app.AlertDialog
import android.graphics.BitmapFactory
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.ImageView
import android.widget.SeekBar
import android.widget.TextView
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricPrompt
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import org.json.JSONObject
import kotlin.math.abs

/**
 * Systems control — Kotlin port of Flutter `control_screen.dart` +
 * `control_ctrl.dart`: volume slider with bridge read-back, mute toggle,
 * media play/pause/next/prev, screenshot capture + preview, screens
 * state/off/restore, open ANY app by free-text name (tool `open_app` —
 * the bridge resolves any app/site), type text + Enter. Destructive
 * actions (sleep, lock) ask first. Owner-only extras kept: ping, lock,
 * fingerprint-gated unlock.
 */
class ControlFragment : Fragment() {
    private var status: TextView? = null
    private var sub: TextView? = null
    private var volumeBar: SeekBar? = null
    private var volumeLabel: TextView? = null
    private var muteBtn: Button? = null
    private var nowPlaying: TextView? = null
    private var note: TextView? = null
    private var shot: ImageView? = null
    private var swipe: SwipeRefreshLayout? = null

    private var volume = 50
    private var muted = false
    private var sliderHeld = false

    private fun bg(fn: () -> Unit) {
        Thread {
            try {
                fn()
            } catch (e: Exception) {
                activity?.runOnUiThread { status?.text = "Error: ${e.message}" }
            }
        }.also { it.isDaemon = true; it.start() }
    }

    private fun ui(fn: () -> Unit) {
        activity?.runOnUiThread { if (isAdded) fn() }
    }

    private fun tool(tool: String, args: JSONObject = JSONObject()): JSONObject =
        LinkApi(Prefs(requireContext())).tool(tool, args).also {
            RemoteLauncher.handleAction(context, it.optJSONObject("action"))
        }

    // ── Volume (Flutter setVolume walk + read-back) ──────────────

    /** Read the true level; updates fields + widgets. Returns level. */
    private fun refreshVolume(): Int {
        return try {
            val r = tool("volume_get")
            if (r.optBoolean("ok", false)) {
                volume = r.optInt("volume", volume).coerceIn(0, 100)
                if (r.has("muted")) muted = r.optBoolean("muted", muted)
            }
            ui { renderVolume() }
            volume
        } catch (_: Exception) {
            volume
        }
    }

    private fun renderVolume() {
        if (!sliderHeld) volumeBar?.progress = volume
        volumeLabel?.text = "$volume%"
        muteBtn?.text = if (muted) "UNMUTED" else "MUTE"
        sub?.text = if (muted) "Audio muted · $volume%" else "Audio live · $volume%"
    }

    /** Walk toward the target in bridge steps, then re-read the true level. */
    private fun setVolume(target0: Int) {
        val target = target0.coerceIn(0, 100)
        volume = target
        ui { renderVolume() }
        bg {
            repeat(8) {
                val cur = refreshVolumeQuiet()
                val diff = target - cur
                if (abs(diff) < 3) return@repeat
                try {
                    tool(if (diff > 0) "volume_up" else "volume_down")
                } catch (_: Exception) { }
            }
            refreshVolume()
        }
    }

    private fun refreshVolumeQuiet(): Int {
        return try {
            val r = tool("volume_get")
            if (r.optBoolean("ok", false)) {
                volume = r.optInt("volume", volume).coerceIn(0, 100)
                if (r.has("muted")) muted = r.optBoolean("muted", muted)
            }
            volume
        } catch (_: Exception) {
            volume
        }
    }

    private fun toggleMute() {
        muted = !muted
        ui { renderVolume() }
        bg {
            try {
                tool(if (muted) "volume_mute" else "volume_unmute")
            } catch (_: Exception) { }
            refreshVolume()
        }
    }

    // ── Media / screens / apps / keys (Flutter media/screens/...) ──

    private fun media(action: String) {
        status?.text = "$action…"
        bg {
            val r = tool(action)
            val s = r.optString("state")
            ui {
                if (s.isNotEmpty()) nowPlaying?.text = "♪ $s"
                status?.text = if (r.optBoolean("ok", true)) "$action OK"
                else "$action failed: ${r.optString("error")}"
            }
        }
    }

    private fun capture() {
        note?.text = "CAPTURING…"
        bg {
            val r = tool("screenshot")
            val img = r.optString("image_b64")
            ui {
                if (r.optBoolean("ok", false) && img.isNotEmpty()) {
                    try {
                        val bytes = android.util.Base64.decode(img, 0)
                        shot?.setImageBitmap(
                            BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                        )
                        note?.text = ""
                        status?.text = "Screenshot OK"
                    } catch (_: Exception) {
                        note?.text = "BAD IMAGE DATA"
                    }
                } else {
                    note?.text = (r.optString("error").ifEmpty { "capture failed" }).uppercase()
                }
            }
        }
    }

    private fun screens(action: String) {
        status?.text = "$action…"
        bg {
            val r = tool(action)
            ui {
                val msg = if (r.optBoolean("ok", false)) "done" else r.optString("error", "failed")
                note?.text = msg.uppercase()
                status?.text = msg
            }
        }
    }

    private fun openApp(app: String) {
        val name = app.trim()
        if (name.isEmpty()) {
            status?.text = "Name an app first"
            return
        }
        status?.text = "Opening $name…"
        bg {
            val r = try {
                tool("open_app", JSONObject().put("app", name))
            } catch (e: Exception) {
                ui { status?.text = "Error: ${e.message}" }
                return@bg
            }
            ui {
                val msg = if (r.optBoolean("ok", false)) "opening $name"
                else r.optString("error", "failed")
                note?.text = msg.uppercase()
                status?.text = msg
            }
        }
    }

    private fun typeText(text: String) {
        if (text.isEmpty()) {
            status?.text = "Type something first"
            return
        }
        status?.text = "Typing…"
        bg {
            try {
                LinkApi(Prefs(requireContext())).typeText(text)
                ui { status?.text = "Typed" }
            } catch (e: Exception) {
                ui { status?.text = "Error: ${e.message}" }
            }
        }
    }

    private fun pressEnter() {
        status?.text = "Key Enter…"
        bg {
            try {
                LinkApi(Prefs(requireContext())).pressKey("Enter")
                ui { status?.text = "Sent Enter" }
            } catch (e: Exception) {
                ui { status?.text = "Error: ${e.message}" }
            }
        }
    }

    private fun confirm(title: String, body: String, onYes: () -> Unit) {
        AlertDialog.Builder(requireContext())
            .setTitle(title.uppercase())
            .setMessage(body)
            .setNegativeButton("Cancel", null)
            .setPositiveButton("DO IT") { _, _ -> onYes() }
            .show()
    }

    // ── Lifecycle ────────────────────────────────────────────────

    override fun onCreateView(
        inflater: LayoutInflater, host: ViewGroup?, state: Bundle?
    ): View {
        val v = inflater.inflate(R.layout.fragment_control, host, false)
        status = v.findViewById(R.id.txt_control_status)
        sub = v.findViewById(R.id.ctrl_sub)
        volumeBar = v.findViewById(R.id.ctrl_volume)
        volumeLabel = v.findViewById(R.id.ctrl_volume_label)
        muteBtn = v.findViewById(R.id.btn_vol_mute)
        nowPlaying = v.findViewById(R.id.ctrl_now_playing)
        note = v.findViewById(R.id.ctrl_note)
        shot = v.findViewById(R.id.img_shot)
        swipe = v.findViewById(R.id.ctrl_swipe)

        swipe?.setOnRefreshListener {
            bg {
                refreshVolume()
                ui { swipe?.isRefreshing = false }
            }
        }
        volumeBar?.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(bar: SeekBar, p: Int, fromUser: Boolean) {
                if (fromUser) {
                    volume = p
                    volumeLabel?.text = "$p%"
                }
            }

            override fun onStartTrackingTouch(bar: SeekBar) {
                sliderHeld = true
            }

            override fun onStopTrackingTouch(bar: SeekBar) {
                sliderHeld = false
                setVolume(bar.progress)
            }
        })
        muteBtn?.setOnClickListener { toggleMute() }
        v.findViewById<Button>(R.id.btn_vol_up).setOnClickListener {
            bg { tool("volume_up"); refreshVolume() }
        }
        v.findViewById<Button>(R.id.btn_vol_down).setOnClickListener {
            bg { tool("volume_down"); refreshVolume() }
        }
        v.findViewById<Button>(R.id.btn_media).setOnClickListener { media("media_play_pause") }
        v.findViewById<Button>(R.id.btn_media_next).setOnClickListener { media("media_next") }
        v.findViewById<Button>(R.id.btn_media_prev).setOnClickListener { media("media_prev") }
        v.findViewById<Button>(R.id.btn_shot).setOnClickListener { capture() }
        v.findViewById<Button>(R.id.btn_screens_state).setOnClickListener { screens("screens_state") }
        v.findViewById<Button>(R.id.btn_restore).setOnClickListener { screens("screens_restore") }
        v.findViewById<Button>(R.id.btn_blackout).setOnClickListener {
            confirm("Blank all displays?", "Turns every monitor off until woken.") {
                screens("screens_off")
            }
        }
        val presets = arrayOf("brave", "files", "terminal", "calculator", "whatsie")
        v.findViewById<Button>(R.id.btn_open_app).setOnClickListener {
            AlertDialog.Builder(requireContext()).setTitle("Open app")
                .setItems(presets) { _, which -> openApp(presets[which]) }
                .show()
        }
        val appField = v.findViewById<EditText>(R.id.edit_open_app)
        v.findViewById<Button>(R.id.btn_open_app_go).setOnClickListener {
            openApp(appField.text.toString())
        }
        val typeField = v.findViewById<EditText>(R.id.edit_type_text)
        v.findViewById<Button>(R.id.btn_type_send).setOnClickListener {
            typeText(typeField.text.toString())
        }
        v.findViewById<Button>(R.id.btn_type_enter).setOnClickListener { pressEnter() }

        // Owner extras (kept): ping, lock (confirm), fingerprint unlock.
        v.findViewById<Button>(R.id.btn_notify).setOnClickListener {
            status?.text = "Ping…"
            bg {
                val r = tool("notify", JSONObject().put("title", "Jarvis phone").put("body", "Ping from phone"))
                ui { status?.text = if (r.optBoolean("ok", true)) "Ping OK" else "Ping failed: ${r.optString("error")}" }
            }
        }
        v.findViewById<Button>(R.id.btn_lock).setOnClickListener {
            confirm("Lock the PC?", "Locks the laptop screen now.") {
                status?.text = "Lock…"
                bg {
                    val r = tool("lock")
                    ui { status?.text = if (r.optBoolean("ok", true)) "Lock OK" else "Lock failed: ${r.optString("error")}" }
                }
            }
        }
        val unlockBtn = v.findViewById<Button>(R.id.btn_unlock)
        // Fingerprint gates PC unlock for the OWNER only. A guest session
        // is already consented-to and locked-down: the PC stays locked
        // during guest mode, so the unlock button is disabled there — a
        // guest (stolen unlocked phone) can never open the PC. Only the
        // owner's fingerprint does.
        unlockBtn.isEnabled = !Prefs(requireContext()).guestMode
        unlockBtn.setOnClickListener {
            if (Prefs(requireContext()).guestMode) {
                status?.text = "Unlock denied: guest mode keeps the PC locked"
                return@setOnClickListener
            }
            unlockWithFingerprint()
        }
        bg { refreshVolume() }
        return v
    }

    override fun onResume() {
        super.onResume()
        // Guest may have been toggled in Settings — refresh the gate.
        try {
            view?.findViewById<Button>(R.id.btn_unlock)?.isEnabled =
                !Prefs(requireContext()).guestMode
        } catch (_: Exception) { }
    }

    override fun onDestroyView() {
        status = null
        sub = null
        volumeBar = null
        volumeLabel = null
        muteBtn = null
        nowPlaying = null
        note = null
        shot = null
        swipe = null
        super.onDestroyView()
    }

    /**
     * Phone-side fingerprint gate for PC unlock: the bridge command only
     * fires after a successful strong-biometric auth on this device. No
     * enrolled fingerprint (or no hardware) denies with a message — there
     * is deliberately no PIN fallback, so a stolen unlocked phone alone
     * cannot open the PC.
     */
    private fun unlockWithFingerprint() {
        if (activity == null) return
        if (Prefs(requireContext()).guestMode) {
            status?.text = "Unlock denied: guest mode keeps the PC locked"
            return
        }
        when (BiometricManager.from(requireContext())
            .canAuthenticate(BiometricManager.Authenticators.BIOMETRIC_STRONG)) {
            BiometricManager.BIOMETRIC_SUCCESS -> Unit // proceed below
            BiometricManager.BIOMETRIC_ERROR_NONE_ENROLLED -> {
                status?.text = "Unlock denied: enroll a fingerprint in Settings first"
                return
            }
            else -> {
                status?.text = "Unlock denied: fingerprint unavailable on this phone"
                return
            }
        }
        val prompt = BiometricPrompt(
            this,
            ContextCompat.getMainExecutor(requireContext()),
            object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                    status?.text = "Unlock…"
                    bg {
                        val r = tool("unlock")
                        ui { status?.text = if (r.optBoolean("ok", true)) "Unlock OK" else "Unlock failed: ${r.optString("error")}" }
                    }
                }

                override fun onAuthenticationFailed() {
                    status?.text = "Unlock denied: fingerprint not recognized"
                }

                override fun onAuthenticationError(code: Int, msg: CharSequence) {
                    status?.text = "Unlock cancelled"
                }
            },
        )
        prompt.authenticate(
            BiometricPrompt.PromptInfo.Builder()
                .setTitle("Unlock the PC?")
                .setSubtitle("Confirm it's you, Sir")
                .setNegativeButtonText("Cancel")
                .setAllowedAuthenticators(BiometricManager.Authenticators.BIOMETRIC_STRONG)
                .build()
        )
    }
}
