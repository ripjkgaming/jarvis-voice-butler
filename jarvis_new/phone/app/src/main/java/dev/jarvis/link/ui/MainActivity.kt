package dev.jarvis.link.ui

import android.Manifest
import android.content.Intent
import android.os.Build
import android.os.Bundle
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.fragment.app.Fragment
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import android.widget.TextView
import dev.jarvis.link.logic.Link
import kotlinx.coroutines.launch
import com.google.android.material.tabs.TabLayout
import dev.jarvis.link.JarvisApp
import dev.jarvis.link.R
import dev.jarvis.link.svc.LinkService
import dev.jarvis.link.svc.WakeRouter

/**
 * Hosts the tab bar and one fragment. Holds no state of its own: state
 * lives in the [dev.jarvis.link.Graph] models, so rotation is harmless.
 */
class MainActivity : AppCompatActivity() {
    private lateinit var tabs: TabLayout
    private var selected = 0
    private var restoring = false
    private var onPermission: ((Boolean) -> Unit)? = null

    private val permissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { grants ->
            onPermission?.invoke(grants.values.all { it })
            onPermission = null
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        tabs = findViewById(R.id.main_tabs)
        Tab.entries.forEach { tabs.addTab(tabs.newTab().setText(it.label).setTag(it)) }
        selected = savedInstanceState?.getInt(KEY_TAB, 0) ?: 0
        restoring = savedInstanceState != null
        tabs.selectTab(tabs.getTabAt(selected), true)
        restoring = false
        tabs.addOnTabSelectedListener(object : TabLayout.OnTabSelectedListener {
            override fun onTabSelected(tab: TabLayout.Tab) = show(tab.position)
            override fun onTabUnselected(tab: TabLayout.Tab) = Unit
            override fun onTabReselected(tab: TabLayout.Tab) = Unit
        })
        if (savedInstanceState == null) show(selected)
        val pill = findViewById<TextView>(R.id.main_status)
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                JarvisApp.graph(this@MainActivity).home.state.collect { s ->
                    val (label, tone) = when (s.link) {
                        Link.ONLINE -> "ONLINE" to Tone.OK
                        Link.CHECKING -> "LINKING" to Tone.WARN
                        Link.FAILED -> "OFFLINE" to Tone.ALERT
                        Link.UNKNOWN -> "STANDBY" to Tone.IDLE
                    }
                    pill.text = if (s.guest) "$label · GUEST" else label
                    pill.dot(tone)
                }
            }
        }
        handleIntent(intent)
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putInt(KEY_TAB, selected)
    }

    override fun onStart() {
        super.onStart()
        // A visible activity may start the microphone services (also covers "tap to start" after boot).
        JarvisApp.graph(this).services.ensureRunning()
        JarvisApp.graph(this).telemetry.acquire()
    }

    override fun onStop() {
        JarvisApp.graph(this).telemetry.release()
        JarvisApp.graph(this).home.abortTalk()
        super.onStop()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleIntent(intent)
    }

    private fun show(position: Int) {
        selected = position
        if (restoring) return
        val frag: Fragment = Tab.entries[position].make()
        supportFragmentManager.beginTransaction().replace(R.id.main_content, frag).commit()
    }

    fun selectTab(tab: Tab) = tabs.selectTab(tabs.getTabAt(tab.ordinal))

    private fun handleIntent(intent: Intent?) {
        when (intent?.action) {
            LinkService.ACTION_WAKE -> {
                selectTab(Tab.HOME)
                if (intent.getBooleanExtra(WakeRouter.EXTRA_AUTO_TALK, false)) {
                    intent.removeExtra(WakeRouter.EXTRA_AUTO_TALK)
                    requirePermissions(arrayOf(Manifest.permission.RECORD_AUDIO)) { ok ->
                        if (ok) JarvisApp.graph(this).home.startTalk()
                    }
                }
            }
            LinkService.ACTION_START_SERVICES -> selectTab(Tab.SETTINGS)
        }
    }

    /** Run [then] once all [perms] are granted (asks the user if needed). */
    fun requirePermissions(perms: Array<String>, then: (Boolean) -> Unit) {
        val missing = perms.filter { checkSelfPermission(it) != android.content.pm.PackageManager.PERMISSION_GRANTED }
        if (missing.isEmpty()) { then(true); return }
        onPermission = then
        permissionLauncher.launch(missing.toTypedArray())
    }

    /** Every permission the app can use, requested together (Settings button). */
    fun requestAllPermissions(then: (Boolean) -> Unit) {
        val p = mutableListOf(Manifest.permission.RECORD_AUDIO, Manifest.permission.CAMERA,
            Manifest.permission.ACCESS_FINE_LOCATION)
        if (Build.VERSION.SDK_INT >= 33) p.add(Manifest.permission.POST_NOTIFICATIONS)
        requirePermissions(p.toTypedArray(), then)
    }

    private companion object {
        const val KEY_TAB = "tab"
    }
}
