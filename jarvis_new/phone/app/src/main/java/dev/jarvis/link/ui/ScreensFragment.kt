package dev.jarvis.link.ui

import android.graphics.BitmapFactory
import android.os.Bundle
import android.view.View
import android.widget.ImageView
import android.widget.RadioButton
import android.widget.RadioGroup
import androidx.fragment.app.Fragment
import dev.jarvis.link.R
import dev.jarvis.link.logic.ScreensState

class ScreensFragment : Fragment(R.layout.fragment_screens) {
    private var shownSeq = -1
    private var shownOutputs: List<String> = emptyList()

    override fun onViewCreated(v: View, state: Bundle?) {
        val m = graph.screens
        v.onClick(R.id.screens_btn_capture) { m.capture() }
        v.onClick(R.id.screens_btn_refresh) { m.loadOutputs() }
        v.onClick(R.id.screens_btn_off) { m.blackout() }
        v.onClick(R.id.screens_btn_on) { m.wake() }
        render(m.state) { draw(v, it) }
        m.loadOutputs()
    }

    private fun draw(v: View, s: ScreensState) {
        v.text(R.id.screens_notice).text = s.notice
        val group = v.findViewById<RadioGroup>(R.id.screens_outputs)
        val names = listOf(ScreensState.ALL) + s.outputs.map { it.name }
        if (names != shownOutputs) {
            shownOutputs = names
            group.removeAllViews()
            for (name in names) {
                group.addView(RadioButton(requireContext()).apply {
                    id = View.generateViewId()
                    text = if (name == ScreensState.ALL) "All displays" else name
                    tag = name
                    contentDescription = "screens_output_$name"
                })
            }
            group.setOnCheckedChangeListener { g, checked ->
                g.findViewById<RadioButton>(checked)?.tag?.let { graph.screens.select(it as String) }
            }
        }
        for (i in 0 until group.childCount) {
            val rb = group.getChildAt(i) as RadioButton
            if (rb.tag == s.selected && !rb.isChecked) rb.isChecked = true
        }
        v.button(R.id.screens_btn_capture).isEnabled = !s.busy
        v.button(R.id.screens_btn_off).isEnabled = !s.guest && !s.busy
        v.button(R.id.screens_btn_on).isEnabled = !s.guest && !s.busy
        if (s.imageSeq != shownSeq) {
            shownSeq = s.imageSeq
            s.image?.let {
                v.findViewById<ImageView>(R.id.screens_image)
                    .setImageBitmap(BitmapFactory.decodeByteArray(it, 0, it.size))
            }
        }
    }
}
