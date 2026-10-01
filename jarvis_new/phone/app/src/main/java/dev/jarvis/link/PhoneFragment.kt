package dev.jarvis.link

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.fragment.app.Fragment
import org.json.JSONObject

/**
 * Phone tab: on-device remote actions — calls (dialer), SMS + WhatsApp
 * draft pre-fill, clipboard copy, TTS, PC mic mute, and laptop audio.
 * Server-pushed actions need a bridge /phone queue (absent in v1.3).
 */
class PhoneFragment : Fragment() {
    private lateinit var status: TextView

    /** Fingerprint-gated PC unlock (same deny posture as ControlFragment:
     *  biometrics or nothing — a stolen unlocked phone alone can't open it).
     *  Refused outright in guest mode — the laptop stays locked. */
    private fun unlockWithFingerprint() {
        if (Prefs(requireContext()).guestMode) {
            status.text = "Unlock denied: guest mode keeps the PC locked"
            return
        }
        val mgr = androidx.biometric.BiometricManager.from(requireContext())
        if (mgr.canAuthenticate(
                androidx.biometric.BiometricManager.Authenticators.BIOMETRIC_STRONG
            ) != androidx.biometric.BiometricManager.BIOMETRIC_SUCCESS
        ) {
            status.text = "Unlock denied: fingerprint unavailable"
            return
        }
        androidx.biometric.BiometricPrompt(
            this,
            androidx.core.content.ContextCompat.getMainExecutor(requireContext()),
            object : androidx.biometric.BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(
                    result: androidx.biometric.BiometricPrompt.AuthenticationResult
                ) {
                    runTool("Unlock") { it.tool("unlock") }
                }

                override fun onAuthenticationFailed() {
                    status.text = "Unlock denied: fingerprint not recognized"
                }

                override fun onAuthenticationError(code: Int, msg: CharSequence) {
                    status.text = "Unlock cancelled"
                }
            },
        ).authenticate(
            androidx.biometric.BiometricPrompt.PromptInfo.Builder()
                .setTitle("Unlock the PC?")
                .setSubtitle("Confirm it's you, Sir")
                .setNegativeButtonText("Cancel")
                .setAllowedAuthenticators(
                    androidx.biometric.BiometricManager.Authenticators.BIOMETRIC_STRONG
                )
                .build()
        )
    }

    private fun number(): String =
        view?.findViewById<EditText>(R.id.phone_number)?.text.toString().trim()

    private fun message(): String =
        view?.findViewById<EditText>(R.id.phone_text)?.text.toString()

    private fun runTool(label: String, fn: (LinkApi) -> JSONObject) {
        status.text = "$label…"
        Thread {
            try {
                val r = fn(LinkApi(Prefs(requireContext())))
                activity?.runOnUiThread {
                    status.text = if (r.optBoolean("ok", true)) "$label OK"
                    else "$label failed: ${r.optString("error")}"
                }
            } catch (e: Exception) {
                activity?.runOnUiThread { status.text = "Error: ${e.message}" }
            }
        }.also { it.isDaemon = true; it.start() }
    }

    override fun onCreateView(inflater: LayoutInflater, host: ViewGroup?, state: Bundle?): View {
        val v = inflater.inflate(R.layout.fragment_phone, host, false)
        status = v.findViewById(R.id.phone_status)
        TtsManager.init(requireContext())
        v.findViewById<Button>(R.id.phone_call).setOnClickListener {
            val n = number()
            if (n.isEmpty()) {
                status.text = "Enter a number first"
                return@setOnClickListener
            }
            startActivity(Intent(Intent.ACTION_DIAL, Uri.parse("tel:$n")))
        }
        v.findViewById<Button>(R.id.phone_sms).setOnClickListener {
            val n = number()
            if (n.isEmpty()) {
                status.text = "Enter a number first"
                return@setOnClickListener
            }
            startActivity(
                Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:$n"))
                    .putExtra("sms_body", message())
            )
        }
        v.findViewById<Button>(R.id.phone_wa).setOnClickListener {
            try {
                startActivity(
                    Intent(Intent.ACTION_SEND)
                        .setType("text/plain")
                        .setPackage("com.whatsapp")
                        .putExtra(Intent.EXTRA_TEXT, message())
                )
            } catch (_: Exception) {
                status.text = "WhatsApp not installed"
            }
        }
        v.findViewById<Button>(R.id.phone_copy).setOnClickListener {
            val cm = requireContext().getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            cm.setPrimaryClip(ClipData.newPlainText("jarvis", message()))
            status.text = "Copied"
        }
        v.findViewById<Button>(R.id.phone_tts).setOnClickListener {
            TtsManager.speak(message().ifEmpty { "At your service, Sir." })
            status.text = "Speaking…"
        }
        v.findViewById<Button>(R.id.phone_mic).setOnClickListener {
            Thread {
                try {
                    val api = LinkApi(Prefs(requireContext()))
                    val muted = api.micStatus().optBoolean("muted", false)
                    api.setMuted(!muted)
                    activity?.runOnUiThread {
                        status.text = if (!muted) "PC mic muted" else "PC mic live"
                    }
                } catch (e: Exception) {
                    activity?.runOnUiThread { status.text = "Error: ${e.message}" }
                }
            }.also { it.isDaemon = true; it.start() }
        }
        v.findViewById<Button>(R.id.phone_vol).setOnClickListener {
            runTool("Volume") { it.tool("volume_get") }
        }
        v.findViewById<Button>(R.id.phone_play).setOnClickListener {
            runTool("Play/pause") { it.tool("media_play_pause") }
        }
        val unlockBtn = v.findViewById<Button>(R.id.phone_unlock)
        // Guest mode = laptop locked, phone untouched: disable unlock here.
        unlockBtn.isEnabled = !Prefs(requireContext()).guestMode
        unlockBtn.setOnClickListener {
            if (Prefs(requireContext()).guestMode) {
                status.text = "Unlock denied: guest mode keeps the PC locked"
                return@setOnClickListener
            }
            unlockWithFingerprint()
        }
        return v
    }

    override fun onResume() {
        super.onResume()
        // Guest may have been toggled in Settings — refresh the gate.
        try {
            view?.findViewById<Button>(R.id.phone_unlock)?.isEnabled =
                !Prefs(requireContext()).guestMode
        } catch (_: Exception) { }
    }
}
