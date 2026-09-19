package dev.jarvis.link

import android.app.AlertDialog
import android.graphics.BitmapFactory
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.ImageView
import android.widget.TextView
import androidx.fragment.app.Fragment
import org.json.JSONObject

/** Full computer control: volume, media, apps, screenshot, notify, lock. */
class ControlFragment : Fragment() {
    private lateinit var status: TextView

    private fun call(label: String, fn: (LinkApi) -> JSONObject) {
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
        val v = inflater.inflate(R.layout.fragment_control, host, false)
        status = v.findViewById(R.id.txt_control_status)
        v.findViewById<Button>(R.id.btn_vol_up).setOnClickListener {
            call("Volume +") { it.tool("volume_up") }
        }
        v.findViewById<Button>(R.id.btn_vol_down).setOnClickListener {
            call("Volume −") { it.tool("volume_down") }
        }
        v.findViewById<Button>(R.id.btn_vol_mute).setOnClickListener {
            call("Mute") { it.tool("volume_mute") }
        }
        v.findViewById<Button>(R.id.btn_media).setOnClickListener {
            call("Play/pause") { it.tool("media_play_pause") }
        }
        v.findViewById<Button>(R.id.btn_media_next).setOnClickListener {
            call("Next") { it.tool("media_next") }
        }
        val apps = arrayOf("brave", "files", "terminal", "calculator", "whatsie")
        v.findViewById<Button>(R.id.btn_open_app).setOnClickListener {
            AlertDialog.Builder(requireContext()).setTitle("Open app")
                .setItems(apps) { _, which ->
                    call("Open ${apps[which]}") {
                        it.tool("open_app", JSONObject().put("app", apps[which]))
                    }
                }.show()
        }
        val shot = v.findViewById<ImageView>(R.id.img_shot)
        v.findViewById<Button>(R.id.btn_shot).setOnClickListener {
            status.text = "Capturing…"
            Thread {
                try {
                    val r = LinkApi(Prefs(requireContext())).tool("screenshot")
                    val bytes = android.util.Base64.decode(r.getString("image_b64"), 0)
                    val bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                    activity?.runOnUiThread {
                        shot.setImageBitmap(bmp)
                        status.text = "Screenshot OK"
                    }
                } catch (e: Exception) {
                    activity?.runOnUiThread { status.text = "Error: ${e.message}" }
                }
            }.also { it.isDaemon = true; it.start() }
        }
        v.findViewById<Button>(R.id.btn_notify).setOnClickListener {
            call("Notify") {
                it.tool("notify", JSONObject().put("title", "Jarvis phone").put("body", "Ping from phone"))
            }
        }
        v.findViewById<Button>(R.id.btn_lock).setOnClickListener {
            AlertDialog.Builder(requireContext()).setTitle("Lock the PC?")
                .setPositiveButton("Lock") { _, _ -> call("Lock") { it.tool("lock") } }
                .setNegativeButton("Cancel", null).show()
        }
        v.findViewById<Button>(R.id.btn_unlock).setOnClickListener {
            AlertDialog.Builder(requireContext()).setTitle("Unlock the PC?")
                .setPositiveButton("Unlock") { _, _ -> call("Unlock") { it.tool("unlock") } }
                .setNegativeButton("Cancel", null).show()
        }
        v.findViewById<Button>(R.id.btn_blackout).setOnClickListener {
            AlertDialog.Builder(requireContext()).setTitle("Black out all screens?")
                .setPositiveButton("Blackout") { _, _ -> call("Blackout") { it.tool("screen_off") } }
                .setNegativeButton("Cancel", null).show()
        }
        v.findViewById<Button>(R.id.btn_restore).setOnClickListener {
            call("Restore screens") { it.tool("screens_restore") }
        }
        return v
    }
}
