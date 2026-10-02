package dev.jarvis.link.ui

import android.os.Bundle
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView
import androidx.fragment.app.Fragment
import com.google.android.material.button.MaterialButton
import dev.jarvis.link.R
import dev.jarvis.link.logic.Approval

class ApprovalsFragment : Fragment(R.layout.fragment_approvals) {
    override fun onViewCreated(v: View, state: Bundle?) {
        val store = graph.approvals
        v.onClick(R.id.approvals_btn_demo) { store.request("Lock the PC?", "Test request from the Approvals tab.") }
        render(store.pending) { draw(v, it) }
    }

    private fun draw(v: View, list: List<Approval>) {
        v.text(R.id.approvals_empty).text = if (list.isEmpty()) "No pending approvals." else ""
        val box = v.findViewById<LinearLayout>(R.id.approvals_container)
        box.removeAllViews()
        for (a in list) {
            val card = LinearLayout(requireContext()).apply {
                orientation = LinearLayout.VERTICAL
                setBackgroundResource(R.drawable.hud_panel)
                val pad = (12 * resources.displayMetrics.density).toInt()
                setPadding(pad, pad, pad, pad)
                layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
                    .apply { topMargin = pad / 2 }
            }
            card.addView(TextView(requireContext()).apply { text = "#${a.id} ${a.title}\n${a.detail}"; tag = "approval_text_${a.id}" })
            val row = LinearLayout(requireContext()).apply {
                orientation = LinearLayout.HORIZONTAL
                isBaselineAligned = false
                setDividerDrawable(androidx.core.content.ContextCompat.getDrawable(context, R.drawable.hud_gap))
                showDividers = LinearLayout.SHOW_DIVIDER_MIDDLE
            }
            row.addView(MaterialButton(requireContext()).apply {
                text = "Deny"; tag = "approval_deny_${a.id}"
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                setOnClickListener { graph.approvals.decide(a.id, false) }
            })
            row.addView(MaterialButton(requireContext()).apply {
                text = "Approve"; tag = "approval_approve_${a.id}"
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                setOnClickListener { graph.approvals.decide(a.id, true) }
                emphasis(Emphasis.PRIMARY)
            })
            card.addView(row)
            box.addView(card)
        }
    }
}
