package dev.jarvis.link

import android.app.Activity
import android.app.Application
import android.content.Context
import android.content.SharedPreferences
import android.os.Bundle
import dev.jarvis.link.logic.KeyValueStore
import dev.jarvis.link.svc.Notifs
import dev.jarvis.link.svc.VoiceCallService
import java.lang.ref.WeakReference

class SharedPrefsStore(context: Context) : KeyValueStore {
    private val sp: SharedPreferences =
        context.getSharedPreferences("jarvis_link", Context.MODE_PRIVATE)

    override fun getString(key: String, def: String?) = try { sp.getString(key, def) } catch (_: ClassCastException) { def }
    override fun putString(key: String, value: String) = sp.edit().putString(key, value).apply()
    override fun getBoolean(key: String, def: Boolean) = try { sp.getBoolean(key, def) } catch (_: ClassCastException) { def }
    override fun putBoolean(key: String, value: Boolean) = sp.edit().putBoolean(key, value).apply()
    override fun getInt(key: String, def: Int) = try { sp.getInt(key, def) } catch (_: ClassCastException) { def }
    override fun putInt(key: String, value: Int) = sp.edit().putInt(key, value).apply()
    override fun getLong(key: String, def: Long) = try { sp.getLong(key, def) } catch (_: ClassCastException) { def }
    override fun putLong(key: String, value: Long) = sp.edit().putLong(key, value).apply()
}

class JarvisApp : Application() {
    lateinit var graph: Graph
        private set

    override fun onCreate() {
        super.onCreate()
        graph = Graph(this)
        Notifs.createChannels(this)
        // A restarted process has no call: make sure no stale call notification lingers.
        VoiceCallService.stop(this)
        registerActivityLifecycleCallbacks(object : ActivityLifecycleCallbacks {
            override fun onActivityResumed(a: Activity) { resumed = WeakReference(a) }
            override fun onActivityPaused(a: Activity) { if (resumed?.get() === a) resumed = null }
            override fun onActivityCreated(a: Activity, s: Bundle?) = Unit
            override fun onActivityStarted(a: Activity) = Unit
            override fun onActivityStopped(a: Activity) = Unit
            override fun onActivitySaveInstanceState(a: Activity, s: Bundle) = Unit
            override fun onActivityDestroyed(a: Activity) = Unit
        })
    }

    companion object {
        @Volatile private var resumed: WeakReference<Activity>? = null

        fun graph(ctx: Context): Graph = (ctx.applicationContext as JarvisApp).graph

        /** The activity currently in the foreground, if any. */
        fun foreground(): Activity? = resumed?.get()
    }
}
