package dev.jarvis.link.device

import androidx.biometric.BiometricManager
import androidx.biometric.BiometricPrompt
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity

/** Fingerprint gate for PC unlock: biometrics or nothing. */
object Biometric {
    /** Calls [result] once on the main thread: true only when the fingerprint matched. */
    fun confirm(activity: FragmentActivity, title: String, result: (Boolean) -> Unit) {
        val allowed = BiometricManager.Authenticators.BIOMETRIC_STRONG
        if (BiometricManager.from(activity).canAuthenticate(allowed) != BiometricManager.BIOMETRIC_SUCCESS) {
            result(false)
            return
        }
        var delivered = false
        fun deliver(ok: Boolean) { if (!delivered) { delivered = true; result(ok) } }
        BiometricPrompt(
            activity, ContextCompat.getMainExecutor(activity),
            object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(r: BiometricPrompt.AuthenticationResult) = deliver(true)
                override fun onAuthenticationError(code: Int, msg: CharSequence) = deliver(false)
            },
        ).authenticate(
            BiometricPrompt.PromptInfo.Builder()
                .setTitle(title)
                .setSubtitle("Confirm it's you")
                .setNegativeButtonText("Cancel")
                .setAllowedAuthenticators(allowed)
                .build(),
        )
    }
}
