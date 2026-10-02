package dev.jarvis.link.ui

import android.os.Bundle
import android.view.View
import android.widget.EditText
import android.widget.SeekBar
import androidx.fragment.app.Fragment
import dev.jarvis.link.R
import dev.jarvis.link.logic.ControlState
import dev.jarvis.link.net.Keys
import dev.jarvis.link.net.Tool

class ControlFragment : Fragment(R.layout.fragment_control) {
    private var dragging = false

    override fun onViewCreated(v: View, state: Bundle?) {
        val m = graph.control
        val seek = v.findViewById<SeekBar>(R.id.control_volume)
        seek.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(s: SeekBar, p: Int, fromUser: Boolean) {
                if (fromUser) v.text(R.id.control_volume_label).text = "Volume $p%"
            }
            override fun onStartTrackingTouch(s: SeekBar) { dragging = true }
            override fun onStopTrackingTouch(s: SeekBar) { dragging = false; m.setVolume(s.progress) }
        })
        v.onClick(R.id.control_btn_vol_down) { m.setVolume((m.state.value.volume ?: 50) - 5) }
        v.onClick(R.id.control_btn_vol_up) { m.setVolume((m.state.value.volume ?: 50) + 5) }
        v.onClick(R.id.control_btn_mute) { m.toggleMute() }
        v.onClick(R.id.control_btn_play) { m.media(Tool.MEDIA_PLAY_PAUSE) }
        v.onClick(R.id.control_btn_next) { m.media(Tool.MEDIA_NEXT) }
        v.onClick(R.id.control_btn_prev) { m.media(Tool.MEDIA_PREV) }
        val app = v.findViewById<EditText>(R.id.control_app_input)
        v.onClick(R.id.control_btn_open_app) { m.openApp(app.text.toString()) }
        val typed = v.findViewById<EditText>(R.id.control_type_input)
        v.onClick(R.id.control_btn_type) { m.typeText(typed.text.toString()) }
        v.onClick(R.id.control_btn_enter) { m.pressKey(Keys.ENTER) }
        v.onClick(R.id.control_btn_esc) { m.pressKey(Keys.ESCAPE) }
        v.onClick(R.id.control_btn_tab) { m.pressKey(Keys.TAB) }
        v.onClick(R.id.control_btn_backspace) { m.pressKey(Keys.BACKSPACE) }
        v.onClick(R.id.control_btn_up) { m.pressKey(Keys.UP) }
        v.onClick(R.id.control_btn_down) { m.pressKey(Keys.DOWN) }
        v.onClick(R.id.control_btn_left) { m.pressKey(Keys.LEFT) }
        v.onClick(R.id.control_btn_right) { m.pressKey(Keys.RIGHT) }
        v.onClick(R.id.control_btn_pc_mic) { m.togglePcMic() }
        v.onClick(R.id.control_btn_refresh) { m.refresh() }
        render(m.state) { draw(v, it) }
        m.refresh()
    }

    private fun draw(v: View, s: ControlState) {
        val seek = v.findViewById<SeekBar>(R.id.control_volume)
        if (!dragging) {
            s.volume?.let { seek.progress = it }
            v.text(R.id.control_volume_label).text =
                (s.volume?.let { "Volume $it%" } ?: "Volume unknown") + (if (s.muted == true) " (muted)" else "")
        }
        v.button(R.id.control_btn_mute).text = if (s.muted == true) "Unmute" else "Mute"
        v.text(R.id.control_now_playing).text = if (s.nowPlaying.isEmpty()) "" else "Now playing: ${s.nowPlaying}"
        v.text(R.id.control_notice).text = s.notice
        for (id in GUEST_BLOCKED) v.findViewById<View>(id).isEnabled = !s.guest
    }

    private companion object {
        val GUEST_BLOCKED = intArrayOf(
            R.id.control_btn_open_app, R.id.control_btn_type, R.id.control_btn_enter, R.id.control_btn_esc,
            R.id.control_btn_tab, R.id.control_btn_backspace, R.id.control_btn_up, R.id.control_btn_down,
            R.id.control_btn_left, R.id.control_btn_right,
        )
    }
}
