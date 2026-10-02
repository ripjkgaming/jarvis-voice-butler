package dev.jarvis.link.ui

import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import androidx.fragment.app.Fragment
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
            box.addView(TextView(requireContext()).apply { text = "#${a.id} ${a.title}\n${a.detail}"; tag = "approval_text_${a.id}" })
            val row = LinearLayout(requireContext()).apply { orientation = LinearLayout.HORIZONTAL }
            row.addView(Button(requireContext()).apply {
                text = "Deny"; tag = "approval_deny_${a.id}"
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                setOnClickListener { graph.approvals.decide(a.id, false) }
            })
            row.addView(Button(requireContext()).apply {
                text = "Approve"; tag = "approval_approve_${a.id}"
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                setOnClickListener { graph.approvals.decide(a.id, true) }
            })
            box.addView(row)
        }
    }
}
