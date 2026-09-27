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
import android.widget.TextView
import androidx.fragment.app.Fragment
import org.json.JSONObject

/**
 * Screens tab: PC screenshot viewer, display on/off/state, lock, and the
 * remote keyboard. Tap-to-click has no desktop endpoint in v1.3, so the
 * tab says so instead of faking it.
 */
class ScreensFragment : Fragment() {
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

    private var shotOutput = "all"

    /** Per-screen selector: All + every kscreen output. Selection drives captures. */
    private fun loadOutputs(box: android.widget.LinearLayout) {
        Thread {
            try {
                val r = LinkApi(Prefs(requireContext())).tool("screens_state")
                val arr = r.optJSONArray("outputs") ?: return@Thread
                val names = mutableListOf("all")
                for (i in 0 until arr.length()) {
                    arr.optJSONObject(i)?.optString("name")?.takeIf { it.isNotEmpty() }?.let {
                        names.add(it)
                    }
                }
                activity?.runOnUiThread {
                    box.removeAllViews()
                    for (name in names) {
                        val b = Button(requireContext()).apply {
                            text = if (name == "all") "ALL" else name
                            textSize = 11f
                            setOnClickListener {
                                shotOutput = name
                                status.text = "Screen: $name"
                                refreshOutputButtons(box)
                            }
                        }
                        box.addView(b)
                    }
                    refreshOutputButtons(box)
                }
            } catch (_: Exception) { }
        }.also { it.isDaemon = true; it.start() }
    }

    private fun refreshOutputButtons(box: android.widget.LinearLayout) {
        for (i in 0 until box.childCount) {
            val b = box.getChildAt(i) as? Button ?: continue
            val selected = (if (shotOutput == "all") "ALL" else shotOutput) == b.text.toString()
            b.alpha = if (selected) 1.0f else 0.45f
        }
    }

    override fun onCreateView(inflater: LayoutInflater, host: ViewGroup?, state: Bundle?): View {
        val v = inflater.inflate(R.layout.fragment_screens, host, false)
        status = v.findViewById(R.id.screens_status)
        val shot = v.findViewById<ImageView>(R.id.screens_shot)
        loadOutputs(v.findViewById(R.id.screens_outputs))
        v.findViewById<Button>(R.id.screens_capture).setOnClickListener {
            status.text = "Capturing…"
            Thread {
                try {
                    val args = org.json.JSONObject()
                    if (shotOutput != "all") args.put("output", shotOutput)
                    val r = LinkApi(Prefs(requireContext())).tool("screenshot", args)
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
        v.findViewById<Button>(R.id.screens_state).setOnClickListener {
            call("Display state") { it.tool("screens_state") }
        }
        v.findViewById<Button>(R.id.screens_off).setOnClickListener {
            call("Off") { it.tool("screen_off") }
        }
        v.findViewById<Button>(R.id.screens_on).setOnClickListener {
            call("Screens on") { it.tool("screens_restore") }
        }
        v.findViewById<Button>(R.id.screens_type).setOnClickListener {
            val input = EditText(requireContext())
            AlertDialog.Builder(requireContext()).setTitle("Type on PC").setView(input)
                .setPositiveButton("Send") { _, _ ->
                    call("Type") { it.typeText(input.text.toString()) }
                }
                .setNegativeButton("Cancel", null).show()
        }
        return v
    }
}
