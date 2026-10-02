package dev.jarvis.link.ui

import android.view.View
import android.view.ViewGroup
import android.widget.CompoundButton
import android.widget.EditText
import android.widget.Button
import android.widget.TextView
import androidx.fragment.app.Fragment
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import dev.jarvis.link.JarvisApp
import dev.jarvis.link.Graph
import dev.jarvis.link.logic.Message
import dev.jarvis.link.logic.Role
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

/** Render [flow] into the view while the fragment's view is started. */
fun <T> Fragment.render(flow: StateFlow<T>, draw: (T) -> Unit) {
    viewLifecycleOwner.lifecycleScope.launch {
        viewLifecycleOwner.repeatOnLifecycle(Lifecycle.State.STARTED) {
            flow.collect {
                draw(it)
                view?.let(::collapseEmptyLabels)
            }
        }
    }
}

/**
 * Hide id'd status/notice labels while they are empty so panels don't
 * show blank gaps; they reappear as soon as there is text. Buttons,
 * fields and switches are never touched.
 */
fun collapseEmptyLabels(root: View) {
    if (root is ViewGroup) {
        for (i in 0 until root.childCount) collapseEmptyLabels(root.getChildAt(i))
        return
    }
    if (root.id == View.NO_ID || root !is TextView) return
    if (root is Button || root is EditText || root is CompoundButton) return
    root.visibility = if (root.text.isNullOrEmpty()) View.GONE else View.VISIBLE
}

val Fragment.graph: Graph get() = JarvisApp.graph(requireContext())

fun <T : View> View.byId(id: Int): T = findViewById(id)

fun View.text(id: Int): TextView = findViewById(id)
fun View.button(id: Int): Button = findViewById(id)

fun View.onClick(id: Int, f: () -> Unit) = findViewById<View>(id).setOnClickListener { f() }

fun formatMessages(list: List<Message>): String =
    list.joinToString("\n") { (if (it.role == Role.USER) "You: " else "Jarvis: ") + it.text }
