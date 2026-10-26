package dev.jarvis.link.svc

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import dev.jarvis.link.JarvisApp

/** Explicit system call notification end action; safe to repeat. */
class CallEndReceiver : BroadcastReceiver() {
    override fun onReceive(context:Context,intent:Intent) { JarvisApp.graph(context).call.hangup() }
}
