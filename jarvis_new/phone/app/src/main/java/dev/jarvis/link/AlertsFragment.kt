package dev.jarvis.link

import android.Manifest
import android.app.AlertDialog
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.PackageManager
import android.location.LocationManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.TextView
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationCompat
import androidx.fragment.app.Fragment

/**
 * Alerts tab: local SOS — red flashing screen, vibration, spoken
 * announcement, last-known location, and a persistent notification.
 * Laptop dispatch needs a bridge /phone/sos endpoint (absent in v1.3),
 * so SOS stays on-device and says so.
 */
class AlertsFragment : Fragment() {
    companion object {
        const val CH_SOS = "jarvis_sos"
        const val NOTIF_SOS = 7
    }

    private val ui = Handler(Looper.getMainLooper())
    private var flashing = false
    private var flashOn = false
    private var root: View? = null
    private var sosBtn: Button? = null

    private val flasher = object : Runnable {
        override fun run() {
            if (!flashing) return
            flashOn = !flashOn
            root?.setBackgroundColor(if (flashOn) 0xFFCC0000.toInt() else 0xFF330000.toInt())
            ui.postDelayed(this, 500)
        }
    }

    override fun onCreateView(inflater: LayoutInflater, host: ViewGroup?, state: Bundle?): View {
        val v = inflater.inflate(R.layout.fragment_alerts, host, false)
        root = v.findViewById(R.id.alerts_root)
        val loc = v.findViewById<TextView>(R.id.alerts_location)
        val status = v.findViewById<TextView>(R.id.alerts_status)
        val sos = v.findViewById<Button>(R.id.alerts_sos)
        sosBtn = sos
        // Breathing danger button until SOS fires.
        sos.startAnimation(
            android.view.animation.AnimationUtils.loadAnimation(requireContext(), R.anim.pulse)
        )
        val cancel = v.findViewById<Button>(R.id.alerts_cancel)
        TtsManager.init(requireContext())
        loc.text = "Location: ${lastKnown() ?: "unknown (grant location)"}"
        sos.setOnClickListener {
            AlertDialog.Builder(requireContext()).setTitle("Send SOS?")
                .setMessage("Flash red, vibrate, announce, and notify. No laptop dispatch in v1.3.")
                .setPositiveButton("SOS") { _, _ ->
                    startSos(status, cancel)
                }
                .setNegativeButton("Cancel", null).show()
        }
        cancel.setOnClickListener { standDown(status, cancel) }
        return v
    }

    private fun lastKnown(): String? {
        return try {
            val ctx = requireContext()
            if (ActivityCompat.checkSelfPermission(ctx, Manifest.permission.ACCESS_FINE_LOCATION) !=
                PackageManager.PERMISSION_GRANTED &&
                ActivityCompat.checkSelfPermission(ctx, Manifest.permission.ACCESS_COARSE_LOCATION) !=
                PackageManager.PERMISSION_GRANTED
            ) {
                ActivityCompat.requestPermissions(
                    requireActivity(),
                    arrayOf(
                        Manifest.permission.ACCESS_FINE_LOCATION,
                        Manifest.permission.ACCESS_COARSE_LOCATION,
                    ),
                    43,
                )
                return null
            }
            val lm = ctx.getSystemService(Context.LOCATION_SERVICE) as LocationManager
            val fix = try {
                lm.getLastKnownLocation(LocationManager.GPS_PROVIDER)
            } catch (_: Exception) {
                null
            } ?: try {
                lm.getLastKnownLocation(LocationManager.NETWORK_PROVIDER)
            } catch (_: Exception) {
                null
            }
            fix?.let { "${it.latitude}, ${it.longitude}" }
        } catch (_: Exception) {
            null
        }
    }

    private fun startSos(status: TextView, cancel: Button) {
        flashing = true
        sosBtn?.clearAnimation()
        ui.post(flasher)
        try {
            (requireContext().getSystemService(Vibrator::class.java))?.vibrate(
                VibrationEffect.createWaveform(longArrayOf(0, 400, 200, 400), 0)
            )
        } catch (_: Exception) { }
        TtsManager.speak("S O S activated, Sir. Stay where you are.")
        val nm = requireContext().getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CH_SOS, "Jarvis SOS", NotificationManager.IMPORTANCE_HIGH)
        )
        nm.notify(
            NOTIF_SOS,
            NotificationCompat.Builder(requireContext(), CH_SOS)
                .setSmallIcon(android.R.drawable.ic_dialog_alert)
                .setContentTitle("SOS ACTIVE")
                .setContentText("Jarvis SOS from this phone. On-device only (v1.3).")
                .setOngoing(true)
                .build(),
        )
        status.text = "SOS ACTIVE — on-device only in v1.3."
        cancel.isEnabled = true
    }

    private fun standDown(status: TextView, cancel: Button) {
        flashing = false
        ui.removeCallbacks(flasher)
        root?.setBackgroundColor(0x00000000)
        try {
            requireContext().getSystemService(Vibrator::class.java)?.cancel()
        } catch (_: Exception) { }
        requireContext().getSystemService(NotificationManager::class.java).cancel(NOTIF_SOS)
        status.text = "Stood down."
        cancel.isEnabled = false
    }

    override fun onPause() {
        super.onPause()
        if (!flashing) root?.setBackgroundColor(0x00000000)
    }

    override fun onDestroyView() {
        flashing = false
        ui.removeCallbacks(flasher)
        root = null
        super.onDestroyView()
    }
}
