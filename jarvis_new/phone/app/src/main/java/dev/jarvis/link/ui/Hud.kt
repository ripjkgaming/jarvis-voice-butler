package dev.jarvis.link.ui

import android.content.res.ColorStateList
import android.widget.Button
import android.widget.TextView
import androidx.core.content.ContextCompat
import com.google.android.material.button.MaterialButton
import dev.jarvis.link.R

/**
 * Runtime half of the desktop-HUD look: state colours and button emphasis
 * that depend on live state. Static styling lives in themes.xml.
 */
enum class Tone(val color: Int) {
    NORMAL(R.color.hud_text_bright),
    DIM(R.color.hud_text_dim),
    OK(R.color.hud_ok),
    WARN(R.color.hud_warn),
    ALERT(R.color.hud_alert),
    THINK(R.color.hud_think),
    IDLE(R.color.hud_text_faint),
}

fun TextView.tone(t: Tone) = setTextColor(ContextCompat.getColor(context, t.color))

/** Tint the compound state dot (drawableStart) of a status pill. */
fun TextView.dot(t: Tone) {
    compoundDrawablesRelative[0]?.mutate()?.setTint(ContextCompat.getColor(context, t.color))
}

enum class Emphasis { DEFAULT, PRIMARY, DANGER }

/** Filled cyan for the main action, red outline for destructive/hang-up. */
fun Button.emphasis(e: Emphasis) {
    val b = this as? MaterialButton ?: return
    val c = { id: Int -> ContextCompat.getColor(context, id) }
    when (e) {
        Emphasis.DEFAULT -> {
            b.backgroundTintList = ContextCompat.getColorStateList(context, R.color.hud_button_fill)
            b.strokeColor = ContextCompat.getColorStateList(context, R.color.hud_button_stroke)
            b.setTextColor(ContextCompat.getColorStateList(context, R.color.hud_button_text))
        }
        Emphasis.PRIMARY -> {
            b.backgroundTintList = ColorStateList.valueOf(c(R.color.hud_cyan))
            b.strokeColor = ColorStateList.valueOf(c(R.color.hud_cyan))
            b.setTextColor(c(R.color.hud_bg))
        }
        Emphasis.DANGER -> {
            b.backgroundTintList = ColorStateList.valueOf(c(R.color.hud_alert_wash))
            b.strokeColor = ColorStateList.valueOf(c(R.color.hud_alert))
            b.setTextColor(c(R.color.hud_text_bright))
        }
    }
}
