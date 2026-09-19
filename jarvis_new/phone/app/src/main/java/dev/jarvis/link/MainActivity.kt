package dev.jarvis.link

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.fragment.app.Fragment
import com.google.android.material.bottomnavigation.BottomNavigationView

class MainActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        requestPerms()
        val nav = findViewById<BottomNavigationView>(R.id.bottom_nav)
        nav.setOnItemSelectedListener { item ->
            val frag: Fragment = when (item.itemId) {
                R.id.tab_talk -> TalkFragment()
                R.id.tab_chat -> ChatFragment()
                R.id.tab_type -> TypeFragment()
                R.id.tab_control -> ControlFragment()
                R.id.tab_camera -> CameraFragment()
                else -> SettingsFragment()
            }
            supportFragmentManager.beginTransaction()
                .replace(R.id.fragment_host, frag).commit()
            true
        }
        if (savedInstanceState == null) nav.selectedItemId = R.id.tab_talk
        handleIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleIntent(intent)
    }

    private fun handleIntent(intent: Intent?) {
        if (intent?.action == LinkService.ACTION_WAKE) {
            findViewById<BottomNavigationView>(R.id.bottom_nav).selectedItemId = R.id.tab_talk
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
