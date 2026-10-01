package dev.jarvis.link

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.fragment.app.Fragment
import com.google.android.material.tabs.TabLayout

class MainActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        requestPerms()
        TtsManager.init(this)
        val nav = findViewById<TabLayout>(R.id.bottom_nav)
        Tabs13.NAMES.forEach { nav.addTab(nav.newTab().setText(it)) }
        nav.addOnTabSelectedListener(object : TabLayout.OnTabSelectedListener {
            override fun onTabSelected(tab: TabLayout.Tab) = showTab(tab.position)
            override fun onTabUnselected(tab: TabLayout.Tab) = Unit
            override fun onTabReselected(tab: TabLayout.Tab) = showTab(tab.position)
        })
        if (savedInstanceState == null) nav.getTabAt(0)?.select()
        handleIntent(intent)
    }

    private fun showTab(position: Int) {
        val frag: Fragment = when (Tabs13.NAMES.getOrNull(position)) {
            "Voice" -> VoiceFragment()
            "Chat" -> ChatFragment()
            "Control" -> ControlFragment()
            "Activity" -> ActivityFragment()
            "Screens" -> ScreensFragment()
            "Approvals" -> ApprovalsFragment()
            "Phone" -> PhoneFragment()
            "Alerts" -> AlertsFragment()
            "Settings" -> SettingsFragment()
            else -> HomeFragment()
        }
        supportFragmentManager.beginTransaction()
            .setCustomAnimations(R.anim.hud_enter, R.anim.hud_exit)
            .replace(R.id.fragment_host, frag).commit()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleIntent(intent)
    }

    private fun handleIntent(intent: Intent?) {
        if (intent?.action == LinkService.ACTION_WAKE) {
            findViewById<TabLayout>(R.id.bottom_nav).getTabAt(0)?.select()
            if (intent.getBooleanExtra(WakeRouter.EXTRA_AUTO_TALK, false)) {
                findViewById<TabLayout>(R.id.bottom_nav).postDelayed({
                    (supportFragmentManager.findFragmentById(R.id.fragment_host) as? HomeFragment)
                        ?.startAutoExchange()
                }, 500)
            }
        }
    }

    private fun requestPerms() {
        val need = mutableListOf(Manifest.permission.RECORD_AUDIO, Manifest.permission.CAMERA)
        if (Build.VERSION.SDK_INT >= 33) need.add(Manifest.permission.POST_NOTIFICATIONS)
        val missing = need.filter {
            checkSelfPermission(it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isNotEmpty()) {
            ActivityCompat.requestPermissions(this, missing.toTypedArray(), 42)
        }
    }
}
